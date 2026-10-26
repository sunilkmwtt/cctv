"""Print MediaMTX source overrides for cameras whose RTSP URL is stored in the admin.

Usage (on the host):
    docker compose exec -T web python manage.py export_mediamtx_env > secrets/mediamtx.env
    chmod 600 secrets/mediamtx.env && docker compose up -d mediamtx
"""

from django.core.management.base import BaseCommand

from cameras.models import Camera


class Command(BaseCommand):
    help = "Write MTX_PATHS_<PATH>_SOURCE lines for enabled cameras to stdout (contains secrets)."

    def handle(self, *args, **options):
        missing = []
        for camera in Camera.objects.filter(enabled=True).select_related("credential"):
            credential = getattr(camera, "credential", None)
            if credential is None:
                missing.append(camera.slug)
                continue
            self.stdout.write(f"MTX_PATHS_{camera.stream_path.upper()}_SOURCE={credential.get_rtsp_url()}")
        for slug in missing:
            self.stderr.write(f"warning: camera {slug!r} has no stored credential; skipped")
