"""Index recorder output into PostgreSQL.

    python manage.py sync_recordings                 # one pass
    python manage.py sync_recordings --loop          # run forever (indexer container)
"""

import logging
import signal
import threading
import time
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import close_old_connections, connection

from recordings.indexer import sync_all

logger = logging.getLogger("recordings.sync")

SYNC_LOCK_ID = 727_002
HEARTBEAT_FILE = Path("/tmp/indexer-heartbeat")


class Command(BaseCommand):
    help = "Scan the recordings directory and upsert metadata (read-only on the video files)."

    def add_arguments(self, parser):
        parser.add_argument("--camera", help="Only sync the camera with this slug.")
        parser.add_argument("--loop", action="store_true", help="Repeat forever.")
        parser.add_argument("--interval", type=int, default=300, help="Seconds between passes with --loop.")

    def handle(self, *args, camera=None, loop=False, interval=300, **options):
        stop = threading.Event()
        if loop:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
        while True:
            self._run_once(camera)
            if not loop:
                return
            close_old_connections()
            if stop.wait(max(interval, 10)):
                self.stdout.write("Indexer stopping.")
                return

    def _run_once(self, camera_slug):
        started = time.monotonic()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_try_advisory_lock(%s)", [SYNC_LOCK_ID])
                if not cursor.fetchone()[0]:
                    self.stdout.write("Another sync is running; skipping this pass.")
                    return
                try:
                    stats = sync_all(camera_slug)
                finally:
                    cursor.execute("SELECT pg_advisory_unlock(%s)", [SYNC_LOCK_ID])
        except Exception:  # keep the loop alive across DB restarts etc.
            logger.exception("Recording sync failed")
            connection.close()
            return
        HEARTBEAT_FILE.touch()
        self.stdout.write(f"Sync finished in {time.monotonic() - started:.1f}s: {stats}")
