import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

from django.core.validators import MaxValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

from cameras.models import Camera
from core.paths import VIDEO_EXTENSIONS, resolve_within


class Recording(models.Model):
    class Status(models.TextChoices):
        RECORDING = "recording", "Recording"
        COMPLETE = "complete", "Complete"
        MISSING = "missing", "Missing"
        CORRUPT = "corrupt", "Corrupt"

    class Source(models.TextChoices):
        HOURLY = "hourly", "Hourly folder"
        LEGACY = "legacy", "Legacy file"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    camera = models.ForeignKey(Camera, on_delete=models.PROTECT, related_name="recordings")
    recording_date = models.DateField()
    start_hour = models.PositiveSmallIntegerField(validators=[MaxValueValidator(23)])
    # Approximate wall-clock span. Exact when the indexer saw the recorder's part files.
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)
    # Relative to the camera's recording directory, POSIX separators.
    relative_file_path = models.CharField(max_length=500)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.HOURLY)
    file_size_bytes = models.BigIntegerField(null=True, blank=True)
    file_mtime_ns = models.BigIntegerField(null=True, blank=True)
    duration_seconds = models.FloatField(null=True, blank=True)
    format = models.CharField(max_length=32, blank=True)
    video_codec = models.CharField(max_length=32, blank=True)
    audio_codec = models.CharField(max_length=32, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices)
    error_message = models.CharField(max_length=255, blank=True)
    probed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["recording_date", "start_hour", "start_time"]
        indexes = [
            models.Index(fields=["camera", "recording_date", "start_hour"], name="rec_cam_date_hour_idx"),
            models.Index(fields=["status"], name="rec_status_idx"),
            models.Index(fields=["recording_date"], name="rec_date_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["camera", "recording_date", "start_hour"],
                condition=Q(source="hourly"),
                name="rec_unique_hourly_slot",
            ),
            models.UniqueConstraint(fields=["camera", "relative_file_path"], name="rec_unique_camera_path"),
            models.CheckConstraint(condition=Q(start_hour__lte=23), name="rec_start_hour_range"),
        ]

    def __str__(self) -> str:
        return f"{self.camera.slug} {self.recording_date} {self.slot_label}"

    @property
    def slot_label(self) -> str:
        return f"{self.start_hour:02d}-{(self.start_hour + 1) % 24:02d}"

    @property
    def slot_start(self) -> datetime:
        return timezone.make_aware(datetime.combine(self.recording_date, time(self.start_hour)))

    @property
    def slot_end(self) -> datetime:
        # 23-00 ends at midnight of the following day.
        return self.slot_start + timedelta(hours=1)

    @property
    def is_playable(self) -> bool:
        return self.status == self.Status.COMPLETE

    @property
    def is_downloadable(self) -> bool:
        return self.status in (self.Status.COMPLETE, self.Status.CORRUPT) and bool(self.file_size_bytes)

    def absolute_path(self) -> Path:
        """Resolve the stored relative path inside the camera's directory (validated)."""
        return resolve_within(
            self.camera.recording_dir, self.relative_file_path, allowed_extensions=VIDEO_EXTENSIONS
        )

    def download_filename(self) -> str:
        suffix = Path(self.relative_file_path).suffix or ".mkv"
        if self.source == self.Source.HOURLY:
            return f"{self.camera.slug}_{self.recording_date}_{self.slot_label}{suffix}"
        return f"{self.camera.slug}_{Path(self.relative_file_path).name}"


class StorageSnapshot(models.Model):
    total_bytes = models.BigIntegerField()
    used_bytes = models.BigIntegerField()
    available_bytes = models.BigIntegerField()
    recordings_bytes = models.BigIntegerField(default=0)
    recording_count = models.PositiveIntegerField(default=0)
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-recorded_at"]
        get_latest_by = "recorded_at"

    def __str__(self) -> str:
        return f"Storage @ {self.recorded_at:%Y-%m-%d %H:%M}"
