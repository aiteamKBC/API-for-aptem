from django.core.management.base import BaseCommand

from api.views import run_sync


class Command(BaseCommand):
    help = "Sync users from the Aptem API into LMS.Aptem_users and remove stale rows."

    def handle(self, *args, **options):
        self.stdout.write("Starting Aptem sync...")
        result = run_sync()
        self.stdout.write(self.style.SUCCESS(
            f"Done. Upserted {result['upserted']}, deleted {result['deleted']} stale row(s)."
        ))
        if result["deleted_emails"]:
            self.stdout.write("Deleted emails:")
            for email in result["deleted_emails"]:
                self.stdout.write(f"  - {email}")
