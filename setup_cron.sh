#!/bin/bash
# Run this once on the server to install the 6-hour cron job.
# It replaces the in-process APScheduler with a reliable system cron.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$SCRIPT_DIR/venv/bin/python"
LOG="$SCRIPT_DIR/sync_history.log"

# Verify python path exists
if [ ! -f "$PYTHON" ]; then
    echo "ERROR: venv not found at $PYTHON"
    echo "Update the PYTHON variable in this script to match your setup."
    exit 1
fi

CRON_LINE="0 */6 * * * cd $SCRIPT_DIR && USE_CRON=true $PYTHON manage.py sync_aptem >> $LOG 2>&1"

# Add only if not already present
(crontab -l 2>/dev/null | grep -qF "sync_aptem") && {
    echo "Cron job already exists. Current crontab:"
    crontab -l | grep sync_aptem
    exit 0
}

(crontab -l 2>/dev/null; echo "$CRON_LINE") | crontab -
echo "Cron job installed. Runs every 6 hours."
echo ""
echo "Entry added:"
echo "  $CRON_LINE"
echo ""
echo "To verify: crontab -l"
echo "To remove: crontab -e  (then delete the sync_aptem line)"
