import tkinter as tk
from tkinter import filedialog
from tkinter import messagebox
from tkinter import simpledialog  
import subprocess
import os
import re
import shlex
import datetime
from pathlib import Path
import json

LABEL = "com.tmraudio.s3upload"
DOMAIN = f"gui/{os.getuid()}"
SERVICE = f"{DOMAIN}/{LABEL}"
PLIST = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

# main folder for photo team,
STAFF_DIR = Path.home() / "S3_Uploader"
UPLOAD_DIR = STAFF_DIR / "upload"
LOGS_DIR = STAFF_DIR / "logs"

# Settings shared with upload.sh. MUST match CONFIG= in upload.sh and APP_DIR in install.sh
CONFIG_DIR = Path.home() / "Library" / "Application Support" / "S3Uploader"
CONFIG_PATH = CONFIG_DIR / "app.config"
DEFAULT_CONFIG = {
    "BACKUP_DIR": str(STAFF_DIR / "backups"),
}

# Backup folder rules. The background job can only use folders inside these:
ALLOWED_ROOTS = [Path.home(), Path("/Users/Shared")]

# ...and never inside these (folder, reason shown to the user):
MACOS_BLOCKED_MSG = (
    "macOS doesn't let background tasks use Desktop, Documents, Downloads, "
    "iCloud Drive or cloud folders like Dropbox.\n\n"
    "Create a folder somewhere else in your home folder instead. You can add it "
    "to the Finder sidebar (Ctrl+Cmd+T) or the Dock for quick access."
)

BLOCKED_DIRS = [
    (Path.home() / "Desktop", MACOS_BLOCKED_MSG),
    (Path.home() / "Documents", MACOS_BLOCKED_MSG),
    (Path.home() / "Downloads", MACOS_BLOCKED_MSG),
    (Path.home() / "Library", "Choose a folder outside the Library folder.\n\n" + MACOS_BLOCKED_MSG),
    (Path.home() / ".Trash", "That folder is in the Trash."),
    (UPLOAD_DIR, "The backup folder can't be inside the upload folder."),
    (LOGS_DIR, "The backup folder can't be inside the logs folder."),
]



# Config file (app.config)
# ---------------------------------------------------------------------------
def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except OSError:
        return cfg                                   # no config yet -> defaults
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        try:
            parts = shlex.split(value)               # undoes shlex.quote
        except ValueError:
            continue                                 # malformed line, skip it
        cfg[key.strip()] = parts[0] if parts else ""
    return cfg


def save_config(cfg):
    """Write ALL settings at once, atomically, so upload.sh never reads a half-written file."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = "# Written by the S3 Uploader app. Read by upload.sh on every run.\n"
    text += "".join(f"{key}={shlex.quote(str(value))}\n" for key, value in cfg.items())
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def check_backup_dir(chosen):
    """Returns (ok, message). On success, message is the cleaned-up real path to save."""
    path = Path(chosen).expanduser().resolve()       # follow symlinks, tidy the path

    if path == Path.home().resolve():
        return False, "Choose a folder inside your home folder, not the home folder itself."

    if not any(path.is_relative_to(root.resolve()) for root in ALLOWED_ROOTS):
        return False, (
            "The backup folder has to be inside your home folder.\n\n"
            "External drives, network drives and system folders can't be used by the "
            "background uploader."
        )

    for blocked, reason in BLOCKED_DIRS:
        if path.is_relative_to(blocked.resolve()):   # the folder itself, or anything inside it
            return False, reason

    if not path.is_dir():
        return False, f"That folder doesn't exist:\n{path}"

    # Real write test (more reliable than asking for permissions)
    test_file = path / f".s3uploader_write_test_{os.getpid()}"
    try:
        test_file.write_text("ok")
        test_file.unlink()
    except OSError as e:
        return False, f"Can't write to that folder:\n{e}"

    return True, str(path)


# Background job (launchd)
# ---------------------------------------------------------------------------
def _launchctl(*args):
    result = subprocess.run(["launchctl", *args], capture_output=True, text=True)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def job_is_loaded():
    ok, _ = _launchctl("print", SERVICE)
    return ok


def start_job():
    _launchctl("enable", SERVICE)              # clear a previous pause
    if job_is_loaded():
        return True, "Already running"
    if not PLIST.exists():
        return False, f"Job file not found:\n{PLIST}"
    ok, msg = _launchctl("bootstrap", DOMAIN, str(PLIST))
    if ok:
        _launchctl("kickstart", SERVICE)       # run now instead of waiting a full interval
    return ok, msg


def stop_job():
    _launchctl("disable", SERVICE)             # stay stopped after a restart / login
    if not job_is_loaded():
        return True, "Already stopped"
    return _launchctl("bootout", SERVICE)


def toggle_job():
    if job_is_loaded():
        action = "stop"
        ok, msg = stop_job()
    else:
        action = "start"
        ok, msg = start_job()

    if not ok:
        messagebox.showerror("S3 Uploader", f"Couldn't {action} the uploader:\n\n{msg}", parent=root)

    update_status_panel()                      # show the new state right away


def get_status():
    """Returns (text, colour) for the status label."""
    result = subprocess.run(["launchctl", "print", SERVICE], capture_output=True, text=True)
    if result.returncode != 0:
        return "Stopped", "red"

    out = result.stdout
    if re.search(r"^\s*state = running", out, re.MULTILINE):
        return "Uploading…", "blue"

    exit_code = re.search(r"last exit code = (-?\d+)", out)
    if (exit_code and exit_code.group(1) != "0") or "last terminating signal" in out:
        return "Error - last run failed (check logs)", "orange"

    return "Running", "green"



# Logs & totals
# ---------------------------------------------------------------------------
def read_logs(type):
    cur_month = datetime.datetime.now().strftime("%Y-%m")
    today_am = datetime.datetime.now().strftime("%Y-%m-%d") + "_AM"
    today_pm = datetime.datetime.now().strftime("%Y-%m-%d") + "_PM"

    log_dir = LOGS_DIR / f'{type}s' / f'{type}s_{cur_month}'
    both_logs = [log_dir / f'{type}_{today_am}.log', log_dir / f'{type}_{today_pm}.log']

    entries = []

    for log in both_logs:
        try:
            with open(log, 'r', encoding='utf-8', errors='replace') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        # errs or echo lines
                        entries.append({"time": None, "level": "raw", "msg": line})
        except OSError:
            continue                                 # no log yet for this half of the day

    return entries


def get_totals(type):
    # Count one line per file copied ("Copied (new)", "Copied (replaced existing)").
    # rclone's "stats" summaries are cumulative and repeat every minute during long
    # runs, so adding those up over-counts big batches.
    total_files = 0
    for entry in read_logs(type):
        if str(entry.get("msg", "")).startswith("Copied"):
            total_files += 1
    return f'{total_files}'



# Backup folder controls
# ---------------------------------------------------------------------------
def current_backup_dir():
    return load_config().get("BACKUP_DIR") or DEFAULT_CONFIG["BACKUP_DIR"]


def choose_backup_folder():
    cfg = load_config()
    current = cfg.get("BACKUP_DIR") or DEFAULT_CONFIG["BACKUP_DIR"]
    start_in = current if Path(current).is_dir() else str(Path.home())

    chosen = filedialog.askdirectory(
        parent=root,
        title="Choose backup folder",
        initialdir=start_in,
        mustexist=True,
    )
    if not chosen:                                   # "" if they pressed Cancel
        return

    ok, result = check_backup_dir(chosen)
    if not ok:
        messagebox.showerror("Can't use that folder", result, parent=root)
        return

    cfg["BACKUP_DIR"] = result
    try:
        save_config(cfg)
    except OSError as e:
        messagebox.showerror("Couldn't save setting", str(e), parent=root)
        return

    # Run the job now so any problem with the new folder shows up in Status immediately
    if job_is_loaded():
        _launchctl("kickstart", SERVICE)

    update_status_panel()
    messagebox.showinfo(
        "Backup folder updated",
        f"New backups will go to:\n{result}\n\nExisting backups stay where they were.",
        parent=root,
    )


def open_backup_folder():
    folder = current_backup_dir()
    if Path(folder).is_dir():
        subprocess.run(["open", folder])
    else:
        messagebox.showerror("Folder not found", f"Backup folder doesn't exist:\n{folder}", parent=root)

# Set Buffer Time
# ---------------------------------------------------------------------------

def set_buffer():
    cfg = load_config()
    try:
        current = int(cfg.get("BUFFER_MIN", 10))
    except ValueError:
        current = 10

    minutes = simpledialog.askinteger(
        "Upload buffer",
        "Minutes to wait before uploading a new image (2–60):",
        parent=root,
        initialvalue=current,
        minvalue=2,
        maxvalue=60,
    )
    if minutes is None:                      # Cancel
        return

    cfg["BUFFER_MIN"] = minutes
    try:
        save_config(cfg)
    except OSError as e:
        messagebox.showerror("Couldn't save setting", str(e), parent=root)
        return

    messagebox.showinfo(
        "Upload buffer updated",
        f"New images will wait {minutes} minutes before uploading.",
        parent=root,
    )



# Refresh
# ---------------------------------------------------------------------------
def update_status_panel():
    """Update every label once. Safe to call from buttons."""
    text, colour = get_status()
    status_label.config(text=f'Status: {text}', fg=colour)
    start_stop_button.config(text="Start Uploader" if text == "Stopped" else "Stop Uploader")

    upload_totals_label.config(text=f'Total Files Uploaded: {get_totals("upload_log")}')
    backup_totals_label.config(text=f'Total Files Backed Up: {get_totals("backup_log")}')

    folder = current_backup_dir()
    if Path(folder).is_dir():
        backup_dir_label.config(text=f'Backup folder: {folder}', fg="systemTextColor")  # follows Light/Dark Mode
    else:
        backup_dir_label.config(text=f'Backup folder: {folder}  (MISSING)', fg="red")


def tick():
    """Self-repeating timer. Start it ONCE."""
    update_status_panel()
    root.after(1000, tick)


# Window
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    root = tk.Tk()
    root.title("S3 Uploader")
    root.geometry("600x400")

    # Status + totals
    status_frame = tk.Frame(root)
    status_frame.pack(side="top", pady=20)

    status_label = tk.Label(status_frame, text='Status: …', font=("Helvetica", 16, "bold"))
    status_label.pack()

    upload_totals_label = tk.Label(status_frame, text='Total Files Uploaded: ')
    backup_totals_label = tk.Label(status_frame, text='Total Files Backed Up: ')
    upload_totals_label.pack()
    backup_totals_label.pack()

    start_stop_button = tk.Button(status_frame, text="Start/Stop Uploader", command=toggle_job)
    start_stop_button.pack(pady=10)

    # Backup folder
    backup_frame = tk.Frame(root)
    backup_frame.pack(side="top", pady=10)

    backup_dir_label = tk.Label(backup_frame, text='Backup folder: …', wraplength=560)
    backup_dir_label.pack()

    backup_buttons = tk.Frame(backup_frame)
    backup_buttons.pack(pady=6)
    tk.Button(backup_buttons, text="Change Backup Folder…", command=choose_backup_folder).pack(side="bottom", padx=4)
    tk.Button(backup_buttons, text="Set Upload Buffer Time", command=set_buffer).pack(side="bottom", pady=4)
    tk.Button(backup_buttons, text="Open Backup Folder", command=open_backup_folder).pack(side="bottom", padx=4)


    tick()
    root.mainloop()