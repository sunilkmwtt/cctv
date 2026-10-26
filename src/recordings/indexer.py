"""Idempotent sync of scanner results into PostgreSQL. Never touches the video files."""

import logging
import shutil
from dataclasses import dataclass, fields
from datetime import date, datetime, time, timedelta

from django.conf import settings
from django.utils import timezone

from cameras.models import Camera
from core import media
from core.paths import UnsafePathError

from .models import Recording, StorageSnapshot
from .scanner import FileInfo, HourlySlot, LegacyFile, scan_camera_dir

logger = logging.getLogger(__name__)

SNAPSHOT_RETENTION = timedelta(days=30)
Status = Recording.Status


@dataclass
class SyncStats:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    probed: int = 0
    marked_missing: int = 0
    skipped_unsettled: int = 0
    bytes_on_disk: int = 0
    root_missing: bool = False

    def merge(self, other: "SyncStats") -> None:
        for f in fields(self):
            mine, theirs = getattr(self, f.name), getattr(other, f.name)
            setattr(self, f.name, (mine or theirs) if isinstance(mine, bool) else mine + theirs)

    def __str__(self) -> str:
        return ", ".join(f"{f.name}={getattr(self, f.name)}" for f in fields(self))


def _aware(naive: datetime) -> datetime:
    return timezone.make_aware(naive, timezone.get_current_timezone())


def _from_ns(ns: int | None) -> datetime | None:
    if ns is None:
        return None
    return datetime.fromtimestamp(ns / 1e9, tz=timezone.get_current_timezone())


def _slot_bounds(day: date, hour: int) -> tuple[datetime, datetime]:
    start = _aware(datetime.combine(day, time(hour)))
    return start, start + timedelta(hours=1)


class CameraSync:
    def __init__(self, camera: Camera, *, now: datetime | None = None, probe=None):
        self.camera = camera
        self.now = now or timezone.now()
        self.local_now = timezone.localtime(self.now)
        self.probe = probe or media.probe
        self.stats = SyncStats()
        self.settle_ns = settings.CCTV_SETTLE_SECONDS * 1_000_000_000
        self.now_ns = int(self.now.timestamp() * 1_000_000_000)
        self.existing: dict[str, Recording] = {}

    # -- helpers ---------------------------------------------------------------
    def _save(self, rel_path: str, values: dict) -> Recording:
        """Create or update one row, writing only changed fields."""
        record = self.existing.get(rel_path)
        if record is None:
            record = Recording.objects.create(camera=self.camera, relative_file_path=rel_path, **values)
            self.existing[rel_path] = record
            self.stats.created += 1
            return record
        changed = [k for k, v in values.items() if getattr(record, k) != v]
        if changed:
            for key in changed:
                setattr(record, key, values[key])
            record.save(update_fields=[*changed, "updated_at"])
            self.stats.updated += 1
        else:
            self.stats.unchanged += 1
        return record

    def _is_settled(self, info: FileInfo) -> bool:
        return self.now_ns - info.mtime_ns >= self.settle_ns

    def _unchanged_final(self, rel_path: str, info: FileInfo) -> bool:
        record = self.existing.get(rel_path)
        return bool(
            record
            and record.status in (Status.COMPLETE, Status.CORRUPT)
            and record.file_size_bytes == info.size
            and record.file_mtime_ns == info.mtime_ns
        )

    def _probe_values(self, info: FileInfo) -> dict:
        """Metadata for a finished file; corrupt status if ffprobe cannot read it."""
        base = {"file_size_bytes": info.size, "file_mtime_ns": info.mtime_ns, "probed_at": self.now}
        if info.size == 0:
            return {**base, "status": Status.CORRUPT, "error_message": "Empty file (0 bytes)",
                    "duration_seconds": None, "format": "", "video_codec": "", "audio_codec": ""}
        try:
            path = self.camera.recording_dir / info.relative_path
            result = self.probe(path)
        except media.ProbeError as exc:
            self.stats.probed += 1
            return {**base, "status": Status.CORRUPT, "error_message": str(exc)[:255],
                    "duration_seconds": None, "format": "", "video_codec": "", "audio_codec": ""}
        self.stats.probed += 1
        return {
            **base,
            "status": Status.COMPLETE,
            "error_message": "",
            "duration_seconds": result.duration_seconds,
            "format": result.format_name,
            "video_codec": result.video_codec or "",
            "audio_codec": result.audio_codec or "",
        }

    # -- hourly folders --------------------------------------------------------
    def _sync_slot(self, slot: HourlySlot) -> None:
        rel = slot.relative_path
        slot_start, slot_end = _slot_bounds(slot.recording_date, slot.start_hour)
        common = {"recording_date": slot.recording_date, "start_hour": slot.start_hour,
                  "source": Recording.Source.HOURLY}
        record = self.existing.get(rel)

        final = slot.final
        if final is not None and self._is_settled(final):
            if self._unchanged_final(rel, final):
                self.stats.unchanged += 1
                return
            values = {**common, **self._probe_values(final)}
            duration = values.get("duration_seconds") or 0
            # Prefer the real start seen from part filenames while recording was in progress.
            start = record.start_time if record and record.start_time else None
            if start is None:
                start = max(slot_start, slot_end - timedelta(seconds=duration)) if duration else slot_start
            end_candidates = [start + timedelta(seconds=duration)]
            if record and record.end_time:
                end_candidates.append(record.end_time)
            values["start_time"] = start
            values["end_time"] = min(slot_end, max(end_candidates))
            self._save(rel, values)
            return

        if final is not None:
            self.stats.skipped_unsettled += 1

        is_current = (slot.recording_date, slot.start_hour) == (self.local_now.date(), self.local_now.hour)
        if final is None and not slot.parts and not is_current:
            # Past hour where the recorder never captured anything: nothing to show.
            if record is not None and record.status != Status.MISSING:
                self._save(rel, {**common, "status": Status.MISSING, "error_message": "No video was saved"})
            return

        start = _aware(slot.first_part_start) if slot.first_part_start else (record.start_time if record else None)
        latest = _from_ns(slot.latest_part_mtime_ns or (final.mtime_ns if final else None))
        values = {
            **common,
            "status": Status.RECORDING,
            "error_message": "" if is_current else "Awaiting finalization by the recorder",
            "file_size_bytes": slot.parts_size or (final.size if final else 0),
            "file_mtime_ns": None,  # forces a probe once the final file appears
            "start_time": start,
            "end_time": min(latest, slot_end) if latest else None,
            "duration_seconds": None,
        }
        self._save(rel, values)

    # -- legacy files ------------------------------------------------------------
    def _sync_legacy(self, legacy: LegacyFile) -> None:
        info = legacy.file
        if not self._is_settled(info):
            self.stats.skipped_unsettled += 1
            return
        if self._unchanged_final(info.relative_path, info):
            self.stats.unchanged += 1
            return
        values = {
            "recording_date": legacy.recording_date,
            "start_hour": legacy.start_hour,
            "source": Recording.Source.LEGACY,
            **self._probe_values(info),
        }
        start = _aware(legacy.started_at)
        duration = values.get("duration_seconds")
        values["start_time"] = start
        values["end_time"] = start + timedelta(seconds=duration) if duration else None
        self._save(info.relative_path, values)

    # -- entry point ---------------------------------------------------------------
    def run(self) -> SyncStats:
        try:
            root = self.camera.recording_dir
        except UnsafePathError:
            logger.error("Camera %s has an unsafe recording_root; skipped", self.camera.slug)
            self.stats.root_missing = True
            return self.stats
        if not root.is_dir():
            # Never mark everything missing just because the mount is absent.
            logger.warning("Recording directory for %s not found: %s", self.camera.slug, root)
            self.stats.root_missing = True
            return self.stats

        self.existing = {r.relative_file_path: r for r in Recording.objects.filter(camera=self.camera)}
        result = scan_camera_dir(root)
        self.stats.bytes_on_disk = result.total_bytes
        seen = set()
        for slot in result.slots:
            seen.add(slot.relative_path)
            self._sync_slot(slot)
        for legacy in result.legacy:
            seen.add(legacy.file.relative_path)
            self._sync_legacy(legacy)

        for rel, record in self.existing.items():
            if rel not in seen and record.status != Status.MISSING:
                record.status = Status.MISSING
                record.error_message = "File no longer present on disk"
                record.save(update_fields=["status", "error_message", "updated_at"])
                self.stats.marked_missing += 1
        if result.ignored:
            logger.debug("Ignored unrecognised files for %s: %s", self.camera.slug, result.ignored[:20])
        return self.stats


def sync_camera(camera: Camera, **kwargs) -> SyncStats:
    return CameraSync(camera, **kwargs).run()


def take_storage_snapshot(recordings_bytes: int) -> StorageSnapshot | None:
    try:
        usage = shutil.disk_usage(settings.CCTV_RECORDINGS_ROOT)
    except OSError as exc:
        logger.warning("Cannot read disk usage: %s", exc)
        return None
    snapshot = StorageSnapshot.objects.create(
        total_bytes=usage.total,
        used_bytes=usage.used,
        available_bytes=usage.free,
        recordings_bytes=recordings_bytes,
        recording_count=Recording.objects.exclude(status=Status.MISSING).count(),
    )
    StorageSnapshot.objects.filter(recorded_at__lt=timezone.now() - SNAPSHOT_RETENTION).delete()
    return snapshot


def sync_all(camera_slug: str | None = None, **kwargs) -> SyncStats:
    total = SyncStats()
    cameras = Camera.objects.all()
    if camera_slug:
        cameras = cameras.filter(slug=camera_slug)
    for camera in cameras:
        stats = sync_camera(camera, **kwargs)
        logger.info("Synced %s: %s", camera.slug, stats)
        total.merge(stats)
    if not camera_slug:
        take_storage_snapshot(total.bytes_on_disk)
    return total
