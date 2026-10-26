"""Browser playback: MKV -> MP4 remux (stream copy) into a bounded on-disk cache.

Concurrency model (works across Gunicorn worker processes):
  * locks/<key>.lock  - held (flock) while one recording is being remuxed
  * locks/remux.slot  - held while *any* remux runs; at most one FFmpeg job at a time
Locks are released by the kernel if a worker dies, so they can never go stale.
"""

import fcntl
import logging
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from django.conf import settings

from core import media

from .models import Recording

logger = logging.getLogger(__name__)

COPY_AUDIO_CODECS = {"aac", "mp3"}
COPY_VIDEO_CODECS = {"h264": [], "hevc": ["-tag:v", "hvc1"]}
FAILURE_TTL_SECONDS = 3600
STALE_TMP_SECONDS = 3 * 3600


class PlaybackState(str, Enum):
    READY = "ready"
    PROCESSING = "processing"
    QUEUED = "queued"


class PlaybackError(Exception):
    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


@dataclass
class PlaybackStatus:
    state: PlaybackState
    path: Path | None = None


def _try_lock(path: Path):
    """Return an open file holding an exclusive flock, or None if someone else holds it."""
    handle = open(path, "a+")  # noqa: SIM115 - lifetime is managed by the caller
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        return None
    return handle


class PlaybackCache:
    def __init__(self, cache_dir: Path | None = None, max_bytes: int | None = None):
        self.cache_dir = Path(cache_dir or settings.CCTV_PLAYBACK_CACHE_DIR)
        self.max_bytes = max_bytes if max_bytes is not None else settings.CCTV_PLAYBACK_CACHE_MAX_BYTES
        self.lock_dir = self.cache_dir / "locks"

    # -- naming -------------------------------------------------------------------
    @staticmethod
    def cache_key(recording: Recording, source_stat: os.stat_result) -> str:
        # Source size + mtime in the key: a changed source never serves a stale MP4.
        return f"{recording.id}-{source_stat.st_size}-{source_stat.st_mtime_ns}"

    def output_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.mp4"

    def _failure_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.failed"

    # -- public API ------------------------------------------------------------------
    def get_or_start(self, recording: Recording) -> PlaybackStatus:
        if recording.status == Recording.Status.RECORDING:
            raise PlaybackError("This hour is still being recorded.", code="recording_in_progress", http_status=409)
        if recording.status == Recording.Status.MISSING:
            raise PlaybackError("The recording file is no longer on disk.", code="missing", http_status=410)
        if recording.status == Recording.Status.CORRUPT:
            raise PlaybackError(
                f"The recording is damaged and cannot be played: {recording.error_message}",
                code="corrupt", http_status=422,
            )

        source = recording.absolute_path()
        try:
            source_stat = source.stat()
        except FileNotFoundError:
            raise PlaybackError("The recording file is no longer on disk.", code="missing", http_status=410)

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(exist_ok=True)
        key = self.cache_key(recording, source_stat)
        output = self.output_path(key)
        if output.exists():
            os.utime(output)  # LRU bookkeeping
            return PlaybackStatus(PlaybackState.READY, output)

        failure = self._failure_path(key)
        if failure.exists() and time.time() - failure.stat().st_mtime < FAILURE_TTL_SECONDS:
            raise PlaybackError(failure.read_text()[:300] or "Conversion failed.", code="remux_failed", http_status=422)

        command = self.build_command(recording, source)  # raises for unsupported codecs
        if source_stat.st_size > self.max_bytes:
            raise PlaybackError("Recording is larger than the playback cache limit.", code="cache_too_small",
                                http_status=507)

        job_lock = _try_lock(self.lock_dir / f"{key}.lock")
        if job_lock is None:
            return PlaybackStatus(PlaybackState.PROCESSING)
        if output.exists():  # finished between our check and taking the lock
            job_lock.close()
            return PlaybackStatus(PlaybackState.READY, output)
        slot_lock = _try_lock(self.lock_dir / "remux.slot")
        if slot_lock is None:
            job_lock.close()
            return PlaybackStatus(PlaybackState.QUEUED)

        try:
            self._ensure_space(source_stat.st_size)
        except PlaybackError:
            slot_lock.close()
            job_lock.close()
            raise

        thread = threading.Thread(
            target=self._remux, args=(command, key, job_lock, slot_lock), daemon=True, name=f"remux-{key[:8]}"
        )
        thread.start()
        return PlaybackStatus(PlaybackState.PROCESSING)

    # -- remux -------------------------------------------------------------------------
    def build_command(self, recording: Recording, source: Path) -> list[str]:
        video, audio = recording.video_codec, recording.audio_codec
        if not video:
            try:
                info = media.probe(source)
            except media.ProbeError as exc:
                raise PlaybackError(f"Cannot read recording: {exc}", code="corrupt", http_status=422)
            video, audio = info.video_codec or "", info.audio_codec or ""
        if video not in COPY_VIDEO_CODECS:
            raise PlaybackError(
                f"Video codec '{video}' cannot be played in browsers without re-encoding.",
                code="unsupported_codec", http_status=415,
            )
        args = [
            "nice", "-n", "10", settings.CCTV_FFMPEG_BIN, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-y", "-fflags", "+genpts", "-i", str(source), "-map", "0:v:0",
            "-c:v", "copy", *COPY_VIDEO_CODECS[video],
        ]
        if audio:
            args += ["-map", "0:a:0"]
            # Compatible audio is copied; anything else becomes cheap AAC (video still copied).
            args += ["-c:a", "copy"] if audio in COPY_AUDIO_CODECS else ["-c:a", "aac", "-b:a", "96k"]
        else:
            args += ["-an"]
        args += ["-sn", "-dn", "-avoid_negative_ts", "make_zero"]
        if settings.CCTV_PLAYBACK_FASTSTART:
            args += ["-movflags", "+faststart"]
        args += ["-f", "mp4"]
        return args  # output path appended by _remux

    def _remux(self, command: list[str], key: str, job_lock, slot_lock) -> None:
        tmp = self.cache_dir / f"{key}.tmp-{os.getpid()}-{threading.get_ident()}.mp4"
        started = time.monotonic()
        try:
            result = subprocess.run(
                [*command, str(tmp)], capture_output=True, text=True, stdin=subprocess.DEVNULL,
                timeout=settings.CCTV_REMUX_TIMEOUT_SECONDS, check=False,
            )
            if result.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
                os.chmod(tmp, 0o644)
                os.replace(tmp, self.output_path(key))
                logger.info("Remuxed %s in %.1fs", key, time.monotonic() - started)
                self.enforce_limit(keep={self.output_path(key)})
            else:
                lines = (result.stderr or "").strip().splitlines()
                message = lines[-1] if lines else f"ffmpeg exited {result.returncode}"
                # Keep absolute container paths out of user-visible errors.
                source = command[command.index("-i") + 1]
                message = message.replace(source, "input").replace(str(tmp), "output")
                self._record_failure(key, message)
        except subprocess.TimeoutExpired:
            self._record_failure(key, "Conversion timed out.")
        except OSError as exc:
            self._record_failure(key, f"Conversion could not start: {exc}")
        finally:
            tmp.unlink(missing_ok=True)
            slot_lock.close()
            job_lock.close()

    def _record_failure(self, key: str, message: str) -> None:
        logger.error("Remux failed for %s: %s", key, message)
        try:
            self._failure_path(key).write_text(f"Conversion failed: {message}"[:300])
        except OSError:
            pass

    # -- cache bounds ----------------------------------------------------------------------
    def _cached_files(self) -> list[Path]:
        try:
            return [p for p in self.cache_dir.iterdir() if p.suffix == ".mp4" and ".tmp-" not in p.name]
        except FileNotFoundError:
            return []

    def size(self) -> int:
        return sum(p.stat().st_size for p in self._cached_files())

    def _ensure_space(self, needed: int) -> None:
        self.enforce_limit(extra=needed)
        free = shutil.disk_usage(self.cache_dir).free
        if free < needed + settings.CCTV_PLAYBACK_MIN_FREE_BYTES:
            raise PlaybackError("Not enough free disk space to prepare this video.", code="insufficient_space",
                                http_status=507)

    def enforce_limit(self, *, extra: int = 0, keep: set[Path] | None = None) -> None:
        """Evict least-recently-used MP4s until cache + `extra` fits in max_bytes."""
        keep = keep or set()
        files = sorted(self._cached_files(), key=lambda p: p.stat().st_mtime)
        total = sum(p.stat().st_size for p in files) + extra
        for path in files:
            if total <= self.max_bytes:
                break
            if path in keep:
                continue
            size = path.stat().st_size
            path.unlink(missing_ok=True)  # an open reader (nginx) keeps its file handle
            total -= size
            logger.info("Evicted %s from playback cache", path.name)
        self._cleanup_stale()

    def _cleanup_stale(self) -> None:
        now = time.time()
        for path in self.cache_dir.glob("*"):
            try:
                age = now - path.stat().st_mtime
            except FileNotFoundError:
                continue
            if (".tmp-" in path.name and age > STALE_TMP_SECONDS) or (
                path.suffix == ".failed" and age > FAILURE_TTL_SECONDS
            ):
                path.unlink(missing_ok=True)
