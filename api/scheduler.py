"""Background scheduler that runs the Aptem sync every 6 hours.

Started from AppConfig.ready(). Runs in-process via APScheduler. The scheduled
job calls run_sync() directly (same work the /api/sync-aptem-users/ endpoint
does: upsert all users and delete any that the API no longer returns).
"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

logger = logging.getLogger(__name__)

_scheduler = None


def _job():
    # Imported lazily so this module is safe to import before apps are ready.
    from api.views import run_sync
    from api.mcr_views import run_sync as run_mcr_sync
    from api.pr_views import run_sync as run_pr_sync

    try:
        result = run_sync()
        logger.info("Aptem sync: upserted %s, deleted %s stale row(s).",
                    result["upserted"], result["deleted"])
    except Exception:
        logger.exception("Aptem scheduled sync failed.")

    # MCR and PR read from the Aptem_users table the sync above populates, so
    # they run afterwards. Each is isolated so one failing does not skip the
    # other.
    try:
        result = run_mcr_sync()
        logger.info("MCR sync: upserted %s, deleted %s stale row(s).",
                    result["upserted"], result["deleted"])
    except Exception:
        logger.exception("MCR scheduled sync failed.")

    try:
        result = run_pr_sync()
        logger.info("PR sync: upserted %s, deleted %s stale row(s).",
                    result["upserted"], result["deleted"])
    except Exception:
        logger.exception("PR scheduled sync failed.")


def start():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _job,
        trigger="interval",
        hours=6,
        id="aptem_sync",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    _scheduler.start()
    logger.info("Aptem sync scheduler started (every 6 hours).")
