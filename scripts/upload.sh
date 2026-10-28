#!/bin/bash
CONFIG="$HOME/Library/Application Support/S3Uploader/app.config"
[ -f "$CONFIG" ] && source "$CONFIG"

FAILED=0

YEAR="$(date +%Y)"
MONTH="$(date +%Y-%m)"
DAY="$(date +%Y-%m-%d)"

STAFF_DIR="$HOME/S3_Uploader"
SRC="$STAFF_DIR/images/$MONTH/$DAY"
DEST="tmr-s3:tmraudio-product-images/rclone_test"   # test
# DEST="tmr-s3:tmraudio-product-images/product_images"            # prod

RCLONE=/usr/local/bin/rclone

YESTERDAY="$(date -v-1d +%Y-%m-%d)"        
YESTERDAY_MONTH="$(date -v-1d +%Y-%m)"    
YESTERDAY_YEAR="$(date -v-1d +%Y)"       
YESTERDAY_SRC="$STAFF_DIR/images/$YESTERDAY_MONTH/$YESTERDAY"

LOG_DEST="$STAFF_DIR/logs/upload_logs/upload_logs_$MONTH"
LOG_FILENAME="$LOG_DEST/upload_log_$DAY.log"

# recreate every run just in case they get deleted / moved
mkdir -p "$SRC" "$LOG_DEST" "$STAFF_DIR"

BUFFER_MIN="${BUFFER_MIN:-10}"
if ! [[ "$BUFFER_MIN" =~ ^[0-9]+$ ]] || [ "$BUFFER_MIN" -lt 2 ] || [ "$BUFFER_MIN" -gt 60 ]; then
  echo "$(date) invalid BUFFER_MIN '$BUFFER_MIN' in app.config - using 10" >> "$LOG_FILENAME"
  BUFFER_MIN=10
fi

if [ -d "$YESTERDAY_SRC" ]; then

  # Double check yesterday's folder in case any were missed
  "$RCLONE" copy "$YESTERDAY_SRC" "$DEST/$YESTERDAY_YEAR" \
  --min-age "${BUFFER_MIN}m" --max-depth 1 --ignore-case \
  --include "*.jpg" \
  --log-file "$LOG_FILENAME" --use-json-log --log-level INFO --stats-log-level DEBUG \
  --ignore-existing --no-traverse --no-update-modtime \
  || FAILED=1
fi

#  Upload
"$RCLONE" copy "$SRC" "$DEST/$YEAR" \
  --min-age "${BUFFER_MIN}m" --max-depth 1 --ignore-case \
  --include "*.jpg" \
  --log-file "$LOG_FILENAME" --use-json-log --log-level INFO --stats-log-level DEBUG \
  --ignore-existing --no-traverse --no-update-modtime \
  || FAILED=1

  exit $FAILED