#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_FILE="$SCRIPT_DIR/sync_history.log"
TIMESTAMP="$(date '+%Y-%m-%d %H:%M:%S')"

echo "============================================================"
echo " Aptem Sync - Run History"
echo "============================================================"
echo ""

case "$1" in
  run)
    echo "[$TIMESTAMP] Starting sync..."
    echo ""
    cd "$SCRIPT_DIR"
    python manage.py sync_aptem 2>&1 | tee /tmp/aptem_run_output.tmp
    EXIT_CODE="${PIPESTATUS[0]}"

    STATUS="SUCCESS"
    [ "$EXIT_CODE" -ne 0 ] && STATUS="FAILED"

    {
      echo ""
      echo "------------------------------------------------------------"
      echo "Run: $TIMESTAMP"
      echo "Status: $STATUS"
      cat /tmp/aptem_run_output.tmp
      echo "------------------------------------------------------------"
    } >> "$LOG_FILE"

    echo ""
    echo "Result logged to: $LOG_FILE"
    ;;

  history)
    if [ ! -f "$LOG_FILE" ]; then
      echo "No history found yet. Run './run_history.sh run' first."
    else
      echo "Log file: $LOG_FILE"
      echo ""
      cat "$LOG_FILE"
    fi
    ;;

  clear)
    if [ ! -f "$LOG_FILE" ]; then
      echo "No history file to clear."
    else
      rm "$LOG_FILE"
      echo "History cleared."
    fi
    ;;

  *)
    echo "Usage:"
    echo "  ./run_history.sh run        - Run the sync and log the result"
    echo "  ./run_history.sh history    - Show the run history log"
    echo "  ./run_history.sh clear      - Clear the run history log"
    ;;
esac
