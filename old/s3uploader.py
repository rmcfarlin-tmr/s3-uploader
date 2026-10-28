import tkinter as tk
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

# Main folder for the photo team. MUST match STAFF_DIR in upload.sh / install.sh
STAFF_DIR = Path.home() / "S3_Uploader"
IMAGES_DIR = STAFF_DIR / "images"          # images/<YYYY-MM>/<YYYY-MM-DD>/
LOGS_DIR = STAFF_DIR / "logs"

# Settings shared with upload.sh. MUST match CONFIG= in upload.sh and APP_DIR in install.sh
CONFIG_DIR = Path.home() / "Library" / "Application Support" / "S3Uploader"
CONFIG_PATH = CONFIG_DIR / "app.config"
DEFAULT_CONFIG = {
    "BUFFER_MIN": "10",
}


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
    """Write all settings at once, atomically, so upload.sh never reads a half-written file.
    Only known settings are written, so old keys (e.g. BACKUP_DIR) get cleaned out."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    text = "# Written by the S3 Uploader app. Read by upload.sh on every run.\n"
    text += "".join(f"{key}={shlex.quote(str(cfg[key]))}\n" for key in DEFAULT_CONFIG if key in cfg)
    tmp = CONFIG_PATH.with_name(CONFIG_PATH.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, CONFIG_PATH)


def buffer_minutes():
    try:
        return int(load_config().get("BUFFER_MIN", 10))
    except ValueError:
        return 10


# Today's folder - same layout as upload.sh: images/<YYYY-MM>/<YYYY-MM-DD>
# ---------------------------------------------------------------------------
def today_dir():
    now = datetime.datetime.now()
    return IMAGES_DIR / now.strftime("%Y-%m") / now.strftime("%Y-%m-%d")


def count_images(folder):
    try:
        return sum(1 for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".jpg")
    except OSError:
        return 0                                     # folder not created yet


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


# Logs & totals - same names as upload.sh:
#   logs/upload_logs/upload_logs_<YYYY-MM>/upload_log_<YYYY-MM-DD>.log
# ---------------------------------------------------------------------------
def today_log_path():
    now = datetime.datetime.now()
    return (LOGS_DIR / "upload_logs" / f"upload_logs_{now:%Y-%m}"
            / f"upload_log_{now:%Y-%m-%d}.log")


def read_log(path):
    entries = []
    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    # the script's own echo lines
                    entries.append({"time": None, "level": "raw", "msg": line})
    except OSError:
        pass                                         # no log yet today
    return entries


def uploaded_today():
    # One "Copied (new)" line per file uploaded. Includes anything uploaded today
    # from yesterday's folder (upload.sh logs both passes to today's log).
    return sum(1 for e in read_log(today_log_path())
               if str(e.get("msg", "")).startswith("Copied"))


# Buttons
# ---------------------------------------------------------------------------
def open_today_folder():
    folder = today_dir()
    try:
        folder.mkdir(parents=True, exist_ok=True)    # upload.sh creates it too; harmless
    except OSError as e:
        messagebox.showerror("Folder not found", f"Can't open today's folder:\n{folder}\n\n{e}", parent=root)
        return
    subprocess.run(["open", str(folder)])


def set_buffer():
    cfg = load_config()
    minutes = simpledialog.askinteger(
        "Upload buffer",
        "Minutes to wait before uploading a new image (2–60):",
        parent=root,
        initialvalue=buffer_minutes(),
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

    update_status_panel()
    messagebox.showinfo(
        "Upload buffer updated",
        f"Images will wait {minutes} minutes after their last save before uploading.",
        parent=root,
    )


# Refresh
# ---------------------------------------------------------------------------
def update_status_panel():
    """Update every label once. Safe to call from buttons."""
    text, colour = get_status()
    status_label.config(text=f'Status: {text}', fg=colour)
    start_stop_button.config(text="Start Uploader" if text == "Stopped" else "Stop Uploader")

    folder = today_dir()
    upload_totals_label.config(text=f'Uploaded today: {uploaded_today()}')
    folder_totals_label.config(text=f"Images in today's folder: {count_images(folder)}")
    buffer_label.config(text=f'Upload buffer: {buffer_minutes()} min')
    today_label.config(text=f"Today's folder: {folder}")


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

    upload_totals_label = tk.Label(status_frame, text='Uploaded today: ')
    folder_totals_label = tk.Label(status_frame, text="Images in today's folder: ")
    buffer_label = tk.Label(status_frame, text='Upload buffer: ')
    upload_totals_label.pack()
    folder_totals_label.pack()
    buffer_label.pack()

    start_stop_button = tk.Button(status_frame, text="Start/Stop Uploader", command=toggle_job)
    start_stop_button.pack(pady=10)

    # Today's folder
    folder_frame = tk.Frame(root)
    folder_frame.pack(side="top", pady=10)

    today_label = tk.Label(folder_frame, text="Today's folder: …", wraplength=560)
    today_label.pack()

    folder_buttons = tk.Frame(folder_frame)
    folder_buttons.pack(pady=6)
    tk.Button(folder_buttons, text="Open Today's Folder", command=open_today_folder).pack(pady=2)
    tk.Button(folder_buttons, text="Set Upload Buffer Time", command=set_buffer).pack(pady=2)

    tick()
    root.mainloop()