#!/bin/bash
#
# S3 Uploader - installer
#
# Package layout:   s3uploader.py
#                   scripts/install.sh
#                   scripts/upload.sh
# Run from anywhere:
#     bash /path/to/package/scripts/install.sh
#
# Optional: use a specific Python instead of the first python3 on PATH:
#     PYTHON=/path/to/python3 bash install.sh
#
# Safe to run again. It updates the app + script, rewrites the background job,
# and rebuilds the launcher, but NEVER overwrites app.config (staff settings).
#

LABEL="com.tmraudio.s3upload"
INTERVAL=60                                        # seconds between runs

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"     # folder this installer is in
PKG_DIR="$(cd "$SCRIPTS_DIR/.." && pwd)"           # package root (one level up)
SRC_APP="$PKG_DIR/s3uploader.py"
SRC_SCRIPT="$SCRIPTS_DIR/upload.sh"

APP_DIR="$HOME/Library/Application Support/S3Uploader"
CONFIG="$APP_DIR/app.config"
STAFF_DIR="$HOME/S3_Uploader"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LAUNCHER="$HOME/Applications/S3 Uploader.app"
RCLONE="/usr/local/bin/rclone"
DOMAIN="gui/$(id -u)"
SERVICE="$DOMAIN/$LABEL"

step() { echo; echo "== $*"; }
ok()   { echo "   [ok] $*"; }
warn() { echo "   [!!] $*"; }
die()  { echo; echo "   [x] $*"; echo; echo "Install stopped at this step."; exit 1; }


# ---------------------------------------------------------------------------
step "1. Checking this Mac"
# ---------------------------------------------------------------------------
[ "$(uname)" = "Darwin" ] || die "This installer is for macOS."
[ "$(id -u)" -ne 0 ]      || die "Don't run this with sudo. Run it as the user who will use the uploader."
[ -f "$SRC_APP" ]         || die "Can't find s3uploader.py in the package folder:  $SRC_APP"
[ -f "$SRC_SCRIPT" ]      || die "Can't find scripts/upload.sh:  $SRC_SCRIPT"
ok "Package files found in $PKG_DIR"


# ---------------------------------------------------------------------------
step "2. Checking Python"
# ---------------------------------------------------------------------------
if [ -n "$VIRTUAL_ENV" ] && [ -z "$PYTHON" ]; then
  die "A Python virtual environment is active ($VIRTUAL_ENV).
       Run 'deactivate' first, or pass a Python explicitly with PYTHON=..."
fi

PYTHON="${PYTHON:-$(command -v python3)}"
[ -n "$PYTHON" ] && [ -x "$PYTHON" ] || die "python3 not found. Install Python from python.org."

PY_INFO="$("$PYTHON" -c 'import sys, tkinter; print(sys.version_info[0], sys.version_info[1], tkinter.TkVersion)' 2>&1)" \
  || die "The Python at $PYTHON can't load tkinter:
       $PY_INFO
       Install Python from python.org (it includes a working tkinter)."
read -r PY_MAJOR PY_MINOR TK_VER <<< "$PY_INFO"

if [ "$PY_MAJOR" -ne 3 ] || [ "$PY_MINOR" -lt 9 ]; then
  die "Python $PY_MAJOR.$PY_MINOR is too old (need 3.9+). Install a newer Python from python.org."
fi
if ! awk -v v="$TK_VER" 'BEGIN { exit !(v >= 8.6) }'; then
  die "tkinter (Tk $TK_VER) is too old - need 8.6+. This is usually Apple's built-in Python.
       Install Python from python.org instead."
fi
ok "Python $PY_MAJOR.$PY_MINOR with Tk $TK_VER at $PYTHON"

case "$PYTHON" in
  */.local/share/uv/*|*/.pyenv/*|*/.venv/*|*/venv/*)
    warn "This Python is managed by a dev tool (uv / pyenv / venv). The launcher will break"
    warn "if that tool removes or upgrades it. Fine for testing; use python.org Python for staff." ;;
esac


# ---------------------------------------------------------------------------
step "3. Checking rclone"
# ---------------------------------------------------------------------------
[ -x "$RCLONE" ] || die "rclone not found at $RCLONE. Install it first."
"$RCLONE" listremotes 2>/dev/null | grep -qx "tmr-s3:" \
  || die "The rclone remote 'tmr-s3' isn't set up. Run 'rclone config' first."
ok "rclone and the tmr-s3 remote are set up"


# ---------------------------------------------------------------------------
step "4. Confirming upload destination"
# ---------------------------------------------------------------------------
ACTIVE_DEST_LINES="$(grep -E '^[[:space:]]*DEST=' "$SRC_SCRIPT")"
DEST_COUNT="$(printf '%s\n' "$ACTIVE_DEST_LINES" | grep -c 'DEST=')"
[ "$DEST_COUNT" -eq 1 ] || die "upload.sh must have exactly ONE active (uncommented) DEST= line. Found $DEST_COUNT."

ACTIVE_DEST="${ACTIVE_DEST_LINES#*=}"
ACTIVE_DEST="${ACTIVE_DEST%%#*}"                   # drop any trailing comment
ACTIVE_DEST="$(printf '%s' "$ACTIVE_DEST" | sed -e 's/[[:space:]]*$//' -e 's/"//g' -e "s/\$(date +%Y)/$(date +%Y)/")"
case "$ACTIVE_DEST" in
  *rclone_test*) MODE="TEST destination" ;;
  *)             MODE="LIVE - product images go public" ;;
esac

echo
echo "   Uploads will go to:  $ACTIVE_DEST"
echo "   Mode:                $MODE"
echo
read -r -p "   Is that correct? Type 'yes' to continue: " ANSWER
[ "$ANSWER" = "yes" ] || die "Cancelled. Edit the DEST line in scripts/upload.sh, then run this again."


# ---------------------------------------------------------------------------
step "5. Creating folders"
# ---------------------------------------------------------------------------
mkdir -p "$STAFF_DIR/upload" "$STAFF_DIR/logs" "$STAFF_DIR/backups" \
         "$APP_DIR" "$HOME/Library/LaunchAgents" "$HOME/Applications" \
  || die "Couldn't create folders."
ok "Staff folder:  $STAFF_DIR  (upload, logs, backups)"
ok "App folder:    $APP_DIR"


# ---------------------------------------------------------------------------
step "6. Installing app and upload script"
# ---------------------------------------------------------------------------
cp "$SRC_APP" "$APP_DIR/s3uploader.py" \
  && cp "$SRC_SCRIPT" "$APP_DIR/upload.sh" \
  && chmod +x "$APP_DIR/upload.sh" \
  || die "Couldn't copy files into $APP_DIR"
xattr -c "$APP_DIR/s3uploader.py" "$APP_DIR/upload.sh" 2>/dev/null   # clear 'downloaded' flags
ok "Copied s3uploader.py and upload.sh"


# ---------------------------------------------------------------------------
step "7. Settings (app.config)"
# ---------------------------------------------------------------------------
if [ -f "$CONFIG" ]; then
  ok "Kept existing settings:"
else
  cat > "$CONFIG" <<EOF
# Written by the S3 Uploader app. Read by upload.sh on every run.
BACKUP_DIR='$STAFF_DIR/backups'
BUFFER_MIN=10
EOF
  [ -f "$CONFIG" ] || die "Couldn't write $CONFIG"
  ok "Created default settings:"
fi
grep -v '^#' "$CONFIG" | sed 's/^/          /'


# ---------------------------------------------------------------------------
step "8. Installing background job"
# ---------------------------------------------------------------------------
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$APP_DIR/upload.sh</string></array>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>StandardErrorPath</key><string>$STAFF_DIR/logs/launchd-error.log</string>
</dict></plist>
EOF
plutil -lint "$PLIST" >/dev/null || die "The job file didn't validate: $PLIST"
ok "Wrote $PLIST (runs every $INTERVAL seconds)"

launchctl enable "$SERVICE" 2>/dev/null           # clear any earlier Stop / pause
launchctl bootout "$SERVICE" 2>/dev/null          # unload old version, if any

LOADED=no
for attempt in 1 2 3 4 5; do                      # bootout can take a moment to finish
  if launchctl bootstrap "$DOMAIN" "$PLIST" 2>/dev/null; then LOADED=yes; break; fi
  sleep 1
done
[ "$LOADED" = yes ] || die "Couldn't load the background job. Try manually:
       launchctl bootstrap $DOMAIN \"$PLIST\""
launchctl print "$SERVICE" >/dev/null 2>&1 || die "Job loaded but launchd can't find it."
ok "Background job loaded (first run in about $INTERVAL seconds)"


# ---------------------------------------------------------------------------
step "9. Building the launcher app"
# ---------------------------------------------------------------------------
rm -rf "$LAUNCHER"
osacompile -o "$LAUNCHER" \
  -e "set py to \"$PYTHON\"" \
  -e "set appFile to POSIX path of (path to home folder) & \"Library/Application Support/S3Uploader/s3uploader.py\"" \
  -e 'do shell script quoted form of py & " " & quoted form of appFile & " > /dev/null 2>&1 &"' \
  || die "Couldn't build the launcher app."
ok "Launcher: $LAUNCHER"


# ---------------------------------------------------------------------------
step "Done"
# ---------------------------------------------------------------------------
cat <<EOF

   Uploads to:     $ACTIVE_DEST   ($MODE)
   Drop images in: $STAFF_DIR/upload
   Settings:       $CONFIG
   Logs:           $STAFF_DIR/logs

   Still to do by hand:
     1. Open "S3 Uploader" (Finder window opening now) and drag it to the Dock.
     2. Add $STAFF_DIR/upload and the backup folder to the Finder sidebar (Ctrl+Cmd+T).
     3. System Settings > General > Login Items: allow the job in the background.
     4. System Settings > Energy: prevent automatic sleep; start up after power failure.
     5. Point the Photoshop export/action at $STAFF_DIR/upload
     6. Test: one new image + one already in S3; check backup, S3, and the upload folder.

EOF
open -R "$LAUNCHER" 2>/dev/null