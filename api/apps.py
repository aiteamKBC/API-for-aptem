import os
import sys

from django.apps import AppConfig


class ApiConfig(AppConfig):
    name = 'api'

    def ready(self):
        # Only start the scheduler for an actually-running server, and only in
        # the worker process (avoid the autoreloader's parent double-starting).
        # Skip entirely if system cron is handling the schedule (USE_CRON=true).
        if os.environ.get("USE_CRON") == "true":
            return

        runserver = "runserver" in sys.argv
        wsgi_or_asgi = any(
            s in " ".join(sys.argv) for s in ("gunicorn", "uvicorn", "daphne")
        )
        if not (runserver or wsgi_or_asgi):
            return
        if runserver and os.environ.get("RUN_MAIN") != "true":
            return

        from api import scheduler
        scheduler.start()
