"""Run `migrate` under a PostgreSQL advisory lock so concurrent runs cannot overlap."""

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import connection

MIGRATION_LOCK_ID = 727_001


class Command(BaseCommand):
    help = "Apply database migrations while holding a cluster-wide advisory lock."

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            self.stdout.write("Waiting for migration lock...")
            cursor.execute("SELECT pg_advisory_lock(%s)", [MIGRATION_LOCK_ID])
            try:
                call_command("migrate", interactive=False, verbosity=options["verbosity"])
            finally:
                cursor.execute("SELECT pg_advisory_unlock(%s)", [MIGRATION_LOCK_ID])
        self.stdout.write(self.style.SUCCESS("Migrations complete."))
