"""Shared fixtures. Every filesystem test works inside pytest's tmp_path - never real recordings."""

import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest
from django.utils import timezone

from cameras.models import Camera
from core.media import ProbeError, ProbeResult

REAL_RECORDINGS = Path("/home/sunil/cctv/recordings")


@pytest.fixture(autouse=True)
def isolated_paths(settings, tmp_path):
    root = tmp_path / "recordings"
    cache = tmp_path / "cache"
    root.mkdir()
    settings.CCTV_RECORDINGS_ROOT = root
    settings.CCTV_PLAYBACK_CACHE_DIR = cache
    settings.CCTV_USE_X_ACCEL = False
    settings.CCTV_SETTLE_SECONDS = 30
    settings.CCTV_PLAYBACK_MIN_FREE_BYTES = 0
    settings.CCTV_MEDIAMTX_API_URL = "http://127.0.0.1:9"  # nothing listens: "MediaMTX down"
    assert not str(root).startswith(str(REAL_RECORDINGS))
    from django.core.cache import cache as django_cache

    django_cache.clear()
    return root


@pytest.fixture
def recordings_root(isolated_paths) -> Path:
    return isolated_paths


@pytest.fixture
def camera(db) -> Camera:
    # Seeded by migration cameras.0002_main_entrance.
    return Camera.objects.get(slug="main-entrance")


@pytest.fixture
def user(django_user_model):
    return django_user_model.objects.create_user(username="viewer", password="correct-horse-battery")


@pytest.fixture
def auth_client(client, user):
    client.force_login(user)
    return client


def set_age(path: Path, seconds: float) -> None:
    ts = time.time() - seconds
    os.utime(path, (ts, ts))


def write_file(path: Path, size: int = 1024, age: float = 3600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    set_age(path, age)
    return path


@pytest.fixture
def make_file():
    return write_file


class FakeProbe:
    """Stands in for ffprobe; records calls and can fail for chosen files."""

    def __init__(self, duration: float = 3540.0, video="h264", audio="aac"):
        self.calls: list[Path] = []
        self.fail: dict[str, str] = {}
        self.duration = duration
        self.video = video
        self.audio = audio

    def __call__(self, path: Path) -> ProbeResult:
        self.calls.append(Path(path))
        if Path(path).name in self.fail:
            raise ProbeError(self.fail[Path(path).name])
        return ProbeResult("matroska", self.duration, self.video, self.audio, 640, 480)


@pytest.fixture
def fake_probe():
    return FakeProbe()


def local_dt(*args) -> datetime:
    return timezone.make_aware(datetime(*args))


requires_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg not installed"
)


@pytest.fixture(scope="session")
def sample_mkv(tmp_path_factory) -> Path:
    """A real 3-second H.264/AAC Matroska file, generated once per test session."""
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    out = tmp_path_factory.mktemp("media") / "sample.mkv"
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
            "-f", "lavfi", "-i", "testsrc2=size=320x240:rate=15",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", "3", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-f", "matroska", str(out),
        ],
        check=True,
    )
    return out
