"""Camera availability: recorder activity on disk + MediaMTX stream state."""

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import Camera

logger = logging.getLogger(__name__)

STREAM_STATUS_TTL = 5


@dataclass
class RecorderActivity:
    active: bool
    last_write: datetime | None
    current_slot: str


@dataclass
class StreamStatus:
    # ready: being pulled now; standby: configured, on-demand idle; unavailable: MediaMTX down;
    # not_configured: MediaMTX has no such path.
    state: str
    ready: bool
    readers: int = 0
    tracks: list[str] | None = None


def current_slot_dir(camera: Camera, now: datetime | None = None) -> tuple[Path, str]:
    now = timezone.localtime(now)
    slot = f"{now.hour:02d}-{(now.hour + 1) % 24:02d}"
    return camera.recording_dir / now.strftime("%Y-%m-%d") / slot, slot


def recorder_activity(camera: Camera, now: datetime | None = None) -> RecorderActivity:
    """The recorder appends to .parts/*.mkv while the camera is connected."""
    now = timezone.localtime(now)
    folder, slot = current_slot_dir(camera, now)
    latest = None
    try:
        with os.scandir(folder / ".parts") as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False) and entry.name.endswith(".mkv"):
                    mtime = entry.stat(follow_symlinks=False).st_mtime
                    latest = mtime if latest is None else max(latest, mtime)
    except OSError:
        pass
    last_write = datetime.fromtimestamp(latest, tz=now.tzinfo) if latest else None
    active = bool(latest and now.timestamp() - latest <= settings.CCTV_RECORDER_ACTIVE_SECONDS)
    return RecorderActivity(active=active, last_write=last_write, current_slot=slot)


def _fetch_stream_status(stream_path: str) -> StreamStatus:
    url = f"{settings.CCTV_MEDIAMTX_API_URL.rstrip('/')}/v3/paths/get/{stream_path}"
    try:
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310 - internal URL from settings
            data = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            # MediaMTX only lists paths that have been touched; a configured
            # on-demand path that nobody watches yet looks like this.
            return StreamStatus(state="standby", ready=False)
        logger.warning("MediaMTX API returned %s for path %s", exc.code, stream_path)
        return StreamStatus(state="unavailable", ready=False)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return StreamStatus(state="unavailable", ready=False)

    ready = bool(data.get("ready"))
    tracks = [str(t) for t in (data.get("tracks") or [])]
    readers = len(data.get("readers") or [])
    return StreamStatus(state="ready" if ready else "standby", ready=ready, readers=readers, tracks=tracks)


def stream_status(stream_path: str) -> StreamStatus:
    key = f"mediamtx-status:{stream_path}"
    cached = cache.get(key)
    if cached is not None:
        return StreamStatus(**cached)
    status = _fetch_stream_status(stream_path)
    cache.set(key, asdict(status), STREAM_STATUS_TTL)
    return status


def camera_status(camera: Camera) -> dict:
    activity = recorder_activity(camera) if camera.enabled else None
    stream = stream_status(camera.stream_path) if camera.enabled else None
    online = bool((activity and activity.active) or (stream and stream.ready))
    return {
        "camera_id": str(camera.id),
        "enabled": camera.enabled,
        "online": online if camera.enabled else False,
        "checked_at": timezone.now(),
        "recorder": asdict(activity) if activity else None,
        "live": {**asdict(stream), "hls_url": camera.hls_url} if stream else None,
    }

