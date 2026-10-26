"""Thin, safe wrappers around ffprobe. Never uses a shell; arguments are passed as a list."""

import json
import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)


class ProbeError(Exception):
    """The file could not be read as a video."""


@dataclass(frozen=True)
class ProbeResult:
    format_name: str
    duration_seconds: float | None
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None


def _run(args: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )


def _parse_tag_duration(value: str | None) -> float | None:
    """Parse Matroska DURATION tags such as '00:59:58.123000000'."""
    if not value:
        return None
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except ValueError:
        return None


def _to_float(value) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _last_packet_time(path: Path, timeout: int) -> float | None:
    """Fallback for interrupted recordings without a duration header.

    Seeks far past the end so the demuxer only reads the tail of the file.
    """
    args = [
        settings.CCTV_FFPROBE_BIN, "-v", "error", "-select_streams", "v:0",
        "-read_intervals", "99999999", "-show_entries", "packet=pts_time",
        "-of", "csv=p=0", "--", str(path),
    ]
    try:
        result = _run(args, timeout)
    except subprocess.TimeoutExpired:
        return None
    times = [_to_float(line.strip().rstrip(",")) for line in result.stdout.splitlines()]
    times = [t for t in times if t]
    return max(times) if times else None


def probe(path: Path, timeout: int | None = None) -> ProbeResult:
    timeout = timeout or settings.CCTV_FFPROBE_TIMEOUT_SECONDS
    args = [
        settings.CCTV_FFPROBE_BIN, "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "--", str(path),
    ]
    try:
        result = _run(args, timeout)
    except subprocess.TimeoutExpired as exc:
        raise ProbeError("ffprobe timed out") from exc
    except OSError as exc:
        raise ProbeError(f"ffprobe could not be executed: {exc}") from exc
    if result.returncode != 0:
        message = (result.stderr or "").strip().splitlines()
        # Never leak absolute container paths into stored/displayed errors.
        text = message[-1].replace(str(path), path.name) if message else "ffprobe failed"
        raise ProbeError(text[:200])
    try:
        data = json.loads(result.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError("ffprobe returned invalid JSON") from exc

    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise ProbeError("No video stream found")

    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    if duration is None:
        tag_durations = [
            _parse_tag_duration((s.get("tags") or {}).get("DURATION")) for s in streams
        ]
        tag_durations = [d for d in tag_durations if d]
        duration = max(tag_durations) if tag_durations else None
    if duration is None:
        duration = _last_packet_time(path, timeout)

    return ProbeResult(
        format_name=(fmt.get("format_name") or "").split(",")[0][:32],
        duration_seconds=round(duration, 3) if duration else None,
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name") if audio else None,
        width=video.get("width"),
        height=video.get("height"),
    )
