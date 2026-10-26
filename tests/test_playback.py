import shutil
import subprocess
import time
from datetime import date

import pytest

from recordings.models import Recording
from recordings.playback import PlaybackCache, PlaybackError, PlaybackState, _try_lock

from .conftest import requires_ffmpeg

pytestmark = pytest.mark.django_db
Status = Recording.Status


def make_recording(camera, root, source, *, hour=13, video="h264", audio="aac", status=Status.COMPLETE):
    rel = f"2026-10-25/{hour:02d}-{hour + 1:02d}/recording.mkv"
    target = root / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    if source is not None:
        shutil.copy(source, target)
    return Recording.objects.create(
        camera=camera, recording_date=date(2026, 10, 25), start_hour=hour, relative_file_path=rel,
        status=status, video_codec=video, audio_codec=audio, file_size_bytes=target.stat().st_size if source else 0,
    )


def wait_ready(cache, recording, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        status = cache.get_or_start(recording)
        if status.state == PlaybackState.READY:
            return status
        time.sleep(0.2)
    raise AssertionError("remux did not finish")


@requires_ffmpeg
def test_remux_to_mp4_preserves_streams_and_never_touches_source(camera, recordings_root, sample_mkv):
    rec = make_recording(camera, recordings_root, sample_mkv)
    source = recordings_root / rec.relative_file_path
    before = (source.read_bytes(), source.stat().st_mtime_ns)

    cache = PlaybackCache()
    assert cache.get_or_start(rec).state in (PlaybackState.PROCESSING, PlaybackState.READY)
    ready = wait_ready(cache, rec)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_name:format=format_name", "-of", "csv=p=0",
         str(ready.path)], capture_output=True, text=True, check=True,
    ).stdout
    assert "h264" in probe and "aac" in probe and "mp4" in probe
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert ready.path.parent == cache.cache_dir
    assert not str(ready.path).startswith(str(recordings_root))


@requires_ffmpeg
def test_play_endpoint_202_then_206(auth_client, camera, recordings_root, sample_mkv):
    rec = make_recording(camera, recordings_root, sample_mkv)
    first = auth_client.get(f"/api/v1/recordings/{rec.id}/play/")
    assert first.status_code in (202, 200)
    if first.status_code == 202:
        assert first["Retry-After"]
        assert first.json()["status"] in ("processing", "queued")
    wait_ready(PlaybackCache(), rec)
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/play/", HTTP_RANGE="bytes=0-99")
    assert response.status_code == 206
    assert response["Content-Type"] == "video/mp4"
    assert len(b"".join(response.streaming_content)) == 100


def test_concurrent_requests_do_not_start_duplicate_jobs(camera, recordings_root, make_file):
    src = make_file(recordings_root / "src.mkv", 10)
    rec = make_recording(camera, recordings_root, src)
    cache = PlaybackCache()
    cache.lock_dir.mkdir(parents=True)
    key = cache.cache_key(rec, (recordings_root / rec.relative_file_path).stat())
    held = _try_lock(cache.lock_dir / f"{key}.lock")
    try:
        assert cache.get_or_start(rec).state == PlaybackState.PROCESSING
    finally:
        held.close()


def test_only_one_remux_at_a_time(camera, recordings_root, make_file):
    src = make_file(recordings_root / "src.mkv", 10)
    rec = make_recording(camera, recordings_root, src)
    cache = PlaybackCache()
    cache.lock_dir.mkdir(parents=True)
    slot = _try_lock(cache.lock_dir / "remux.slot")
    try:
        assert cache.get_or_start(rec).state == PlaybackState.QUEUED
    finally:
        slot.close()


def test_unsupported_codec(camera, recordings_root, make_file):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 10), video="mpeg4")
    with pytest.raises(PlaybackError) as exc:
        PlaybackCache().get_or_start(rec)
    assert exc.value.http_status == 415


def test_non_browser_audio_is_transcoded_but_video_copied(camera, recordings_root, make_file):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 10), audio="pcm_alaw")
    command = PlaybackCache().build_command(rec, recordings_root / rec.relative_file_path)
    assert command[command.index("-c:v") + 1] == "copy"
    assert command[command.index("-c:a") + 1] == "aac"
    assert "shell" not in command


@pytest.mark.parametrize(("status", "code"), [(Status.RECORDING, 409), (Status.MISSING, 410), (Status.CORRUPT, 422)])
def test_playback_errors_by_status(camera, recordings_root, make_file, status, code):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 10), status=status)
    with pytest.raises(PlaybackError) as exc:
        PlaybackCache().get_or_start(rec)
    assert exc.value.http_status == code


def test_source_deleted_after_indexing(auth_client, camera, recordings_root, make_file):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 10))
    (recordings_root / rec.relative_file_path).unlink()
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/play/")
    assert response.status_code == 410


@requires_ffmpeg
def test_broken_file_reports_conversion_failure(auth_client, camera, recordings_root, make_file):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 4096))
    cache = PlaybackCache()
    cache.get_or_start(rec)
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            cache.get_or_start(rec)
        except PlaybackError as exc:
            assert exc.code == "remux_failed" and exc.http_status == 422
            break
        time.sleep(0.2)
    else:
        raise AssertionError("expected failure marker")
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/play/")
    assert response.status_code == 422
    assert "Conversion failed" in response.json()["error"]["detail"]


def test_cache_is_bounded_lru(tmp_path):
    cache = PlaybackCache(cache_dir=tmp_path / "c", max_bytes=250)
    cache.cache_dir.mkdir()
    for index, name in enumerate(["a", "b", "c"]):
        path = cache.cache_dir / f"{name}.mp4"
        path.write_bytes(b"x" * 100)
        import os

        os.utime(path, (1000 + index, 1000 + index))
    cache.enforce_limit()
    assert sorted(p.name for p in cache.cache_dir.glob("*.mp4")) == ["b.mp4", "c.mp4"]
    cache.enforce_limit(extra=100)
    assert sorted(p.name for p in cache.cache_dir.glob("*.mp4")) == ["c.mp4"]


def test_recording_larger_than_cache_is_rejected(camera, recordings_root, make_file):
    rec = make_recording(camera, recordings_root, make_file(recordings_root / "s.mkv", 1000))
    with pytest.raises(PlaybackError) as exc:
        PlaybackCache(max_bytes=10).get_or_start(rec)
    assert exc.value.http_status == 507
