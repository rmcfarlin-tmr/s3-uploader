#!/bin/bash
CONFIG="$HOME/Library/Application Support/S3Uploader/app.config"
[ -f "$CONFIG" ] && source "$CONFIG"

STAFF_DIR="$HOME/S3_Uploader"
SRC="$STAFF_DIR/upload"
DEST="tmr-s3:tmraudio-product-images/rclone_test/$(date +%Y)/"   # test
# DEST="tmr-s3:tmraudio-product-images/product_images/$(date +%Y)/"            # prod

RCLONE=/usr/local/bin/rclone

MONTH="$(date +%Y-%m)"
# AM/PM from the hour, so log names don't depend on the Mac's language settings
if [ "$(date +%H)" -lt 12 ]; then AMPM=AM; else AMPM=PM; fi
HALF_DAY="$(date +%Y-%m-%d)_$AMPM"

BACKUP_LOG_DEST="$STAFF_DIR/logs/backup_logs/backup_logs_$MONTH"
BACKUP_LOG_FILENAME="$BACKUP_LOG_DEST/backup_log_$HALF_DAY.log"

LOG_DEST="$STAFF_DIR/logs/upload_logs/upload_logs_$MONTH"
LOG_FILENAME="$LOG_DEST/upload_log_$HALF_DAY.log"

# recreate every run just in case they get deleted / moved
mkdir -p "$SRC" "$LOG_DEST" "$BACKUP_LOG_DEST"

BUFFER_MIN="${BUFFER_MIN:-10}"
if ! [[ "$BUFFER_MIN" =~ ^[0-9]+$ ]] || [ "$BUFFER_MIN" -lt 2 ] || [ "$BUFFER_MIN" -gt 60 ]; then
  echo "$(date) invalid BUFFER_MIN '$BUFFER_MIN' in app.config - using 10" >> "$LOG_FILENAME"
  BUFFER_MIN=10
fi

if [ -z "$BACKUP_DIR" ]; then
  BACKUP_DIR="$STAFF_DIR/backups"
  mkdir -p "$BACKUP_DIR"                     
fi

if [ ! -d "$BACKUP_DIR" ]; then
  echo "$(date) backup folder missing: '$BACKUP_DIR' - skipping run" >> "$BACKUP_LOG_FILENAME"
  exit 1
fi

BACKUP_DEST="$BACKUP_DIR/$HALF_DAY"
mkdir -p "$BACKUP_DEST" || {                   
  echo "$(date) can't write to backup folder: '$BACKUP_DIR' - skipping run" >> "$BACKUP_LOG_FILENAME"
  exit 1
}

# Back up
"$RCLONE" copy "$SRC" "$BACKUP_DEST" \
  --min-age 1m --max-depth 1 --ignore-case \
  --include "*.jpg" \
  --log-file "$BACKUP_LOG_FILENAME" --use-json-log --log-level INFO --stats-log-level DEBUG \
  || { echo "$(date) backup failed - skipping upload" >> "$BACKUP_LOG_FILENAME"; exit 1; }

#  Upload
"$RCLONE" move "$SRC" "$DEST" \
  --min-age "${BUFFER_MIN}m" --max-depth 1 --ignore-case \
  --include "*.jpg" \
  --log-file "$LOG_FILENAME" --use-json-log --log-level INFO --stats-log-level DEBUG \
  --ignore-existing --no-traverse --no-update-modtime