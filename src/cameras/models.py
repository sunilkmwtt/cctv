import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models

from core import crypto
from core.paths import UnsafePathError, resolve_within, validate_relative_path


def validate_recording_root(value: str) -> None:
    try:
        validate_relative_path(value, allow_empty=True)
    except UnsafePathError as exc:
        raise ValidationError(str(exc)) from exc


class Camera(models.Model):
    class CameraType(models.TextChoices):
        ANDROID_IP = "android_ip", "Android IP Camera"
        RTSP = "rtsp", "Generic RTSP camera"

    class StreamProtocol(models.TextChoices):
        RTSP = "rtsp", "RTSP"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=50, unique=True, help_text="Stable identifier, e.g. main-entrance.")
    location = models.CharField(max_length=200, blank=True)
    camera_type = models.CharField(max_length=20, choices=CameraType.choices, default=CameraType.RTSP)
    stream_protocol = models.CharField(
        max_length=10, choices=StreamProtocol.choices, default=StreamProtocol.RTSP
    )
    stream_path = models.CharField(
        max_length=32,
        unique=True,
        validators=[
            RegexValidator(
                r"^[a-z0-9]+$",
                "Lowercase letters and digits only (MediaMTX env overrides cannot handle _ or -).",
            )
        ],
        help_text="MediaMTX path name. Live stream is served at /stream/hls/<stream_path>/.",
    )
    recording_root = models.CharField(
        max_length=255,
        unique=True,
        blank=True,
        validators=[validate_recording_root],
        help_text="Folder relative to the recordings mount. Empty = the mount itself.",
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def recording_dir(self) -> Path:
        return resolve_within(settings.CCTV_RECORDINGS_ROOT, self.recording_root, allow_empty=True)

    @property
    def hls_url(self) -> str:
        return f"{settings.CCTV_HLS_PUBLIC_PREFIX}{self.stream_path}/index.m3u8"


class CameraCredential(models.Model):
    """Connection secret kept apart from camera metadata, encrypted at rest, admin-only."""

    camera = models.OneToOneField(
        Camera, on_delete=models.CASCADE, primary_key=True, related_name="credential"
    )
    rtsp_url_encrypted = models.TextField(editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "camera credential"

    def __str__(self) -> str:
        return f"Credential for {self.camera}"

    def set_rtsp_url(self, url: str) -> None:
        self.rtsp_url_encrypted = crypto.encrypt(url)

    def get_rtsp_url(self) -> str:
        return crypto.decrypt(self.rtsp_url_encrypted)
