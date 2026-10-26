from datetime import date, timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from recordings.indexer import sync_all, sync_camera
from recordings.models import Recording, StorageSnapshot

from .conftest import local_dt, set_age

pytestmark = pytest.mark.django_db
Status = Recording.Status


def slot_dir(root, day: date, hour: int):
    return root / day.isoformat() / f"{hour:02d}-{(hour + 1) % 24:02d}"


def test_complete_recording_indexed(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv", 5000)
    stats = sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert stats.created == 1
    assert (rec.recording_date, rec.start_hour, rec.status) == (date(2026, 10, 25), 13, Status.COMPLETE)
    assert rec.relative_file_path == "2026-10-25/13-14/recording.mkv"
    assert rec.file_size_bytes == 5000
    assert rec.duration_seconds == 3540.0
    assert (rec.video_codec, rec.audio_codec, rec.format) == ("h264", "aac", "matroska")
    # Without part history the start is estimated from the end of the slot.
    assert rec.start_time == local_dt(2026, 10, 25, 13, 1)
    assert rec.end_time == local_dt(2026, 10, 25, 14, 0)


def test_sync_is_idempotent_and_does_not_reprobe(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv")
    make_file(recordings_root / "2026-10-25_09-00-00.mkv")
    sync_camera(camera, probe=fake_probe)
    first = list(Recording.objects.order_by("relative_file_path").values())
    calls = len(fake_probe.calls)

    stats = sync_camera(camera, probe=fake_probe)

    assert len(fake_probe.calls) == calls
    assert stats.created == stats.updated == 0
    assert list(Recording.objects.order_by("relative_file_path").values()) == first


def test_changed_file_is_reprobed(camera, recordings_root, make_file, fake_probe):
    path = make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv", 100)
    sync_camera(camera, probe=fake_probe)
    path.write_bytes(b"\0" * 200)
    set_age(path, 3600)
    sync_camera(camera, probe=fake_probe)
    assert len(fake_probe.calls) == 2
    assert Recording.objects.get().file_size_bytes == 200


def test_midnight_slot_belongs_to_start_date(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "23-00" / "recording.mkv")
    sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert rec.recording_date == date(2026, 10, 25) and rec.start_hour == 23
    assert rec.slot_label == "23-00"
    assert rec.slot_end == local_dt(2026, 10, 26, 0, 0)
    assert rec.end_time == local_dt(2026, 10, 26, 0, 0)


def test_empty_file_is_corrupt_without_probing(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "10-11" / "recording.mkv", 0)
    sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert rec.status == Status.CORRUPT and "Empty" in rec.error_message
    assert fake_probe.calls == []


def test_unreadable_file_is_corrupt(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "10-11" / "recording.mkv", 10)
    fake_probe.fail["recording.mkv"] = "Invalid data found when processing input"
    sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert rec.status == Status.CORRUPT
    assert "Invalid data" in rec.error_message
    assert not rec.is_playable and rec.is_downloadable


def test_unsettled_final_file_is_not_treated_as_complete(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "10-11" / "recording.mkv", 10, age=1)
    stats = sync_camera(camera, probe=fake_probe)
    assert stats.skipped_unsettled == 1
    assert fake_probe.calls == []
    assert Recording.objects.get().status == Status.RECORDING


def test_current_hour_parts_are_recording_not_complete(camera, recordings_root, make_file, fake_probe):
    now = timezone.localtime()
    parts = slot_dir(recordings_root, now.date(), now.hour) / ".parts"
    make_file(parts / f"{now.hour:02d}-00-05-000001.mkv", 300, age=5)
    sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert rec.status == Status.RECORDING
    assert rec.error_message == ""
    assert rec.file_size_bytes == 300
    assert rec.start_time == local_dt(now.year, now.month, now.day, now.hour, 0, 5, 1)
    assert fake_probe.calls == []  # .parts files are never probed or exposed as finished


def test_network_interruption_parts_then_finalization(camera, recordings_root, make_file, fake_probe):
    """Recorder reconnects several times inside one hour, then concatenates the parts."""
    folder = recordings_root / "2026-10-25" / "17-18"
    make_file(folder / ".parts" / "17-05-45-500606.mkv", 100, age=7200)
    make_file(folder / ".parts" / "17-14-05-104483.mkv", 100, age=7200)
    make_file(folder / ".parts" / "17-25-21-864971.mkv", 100, age=7200)

    sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert rec.status == Status.RECORDING
    assert rec.error_message == "Awaiting finalization by the recorder"
    assert rec.start_time == local_dt(2026, 10, 25, 17, 5, 45, 500606)

    # Recorder merges parts into recording.mkv and removes .parts content.
    for part in (folder / ".parts").iterdir():
        part.unlink()
    make_file(folder / "recording.mkv", 300)
    fake_probe.duration = 1500.0
    sync_camera(camera, probe=fake_probe)

    rec.refresh_from_db()
    assert Recording.objects.count() == 1
    assert rec.status == Status.COMPLETE
    assert rec.start_time == local_dt(2026, 10, 25, 17, 5, 45, 500606)  # kept from part names
    assert rec.end_time <= local_dt(2026, 10, 25, 18, 0)


def test_past_hour_with_empty_parts_dir_is_not_listed(camera, recordings_root, fake_probe):
    (recordings_root / "2026-10-25" / "05-06" / ".parts").mkdir(parents=True)
    sync_camera(camera, probe=fake_probe)
    assert Recording.objects.count() == 0


def test_deleted_file_marked_missing(camera, recordings_root, make_file, fake_probe):
    path = make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv")
    sync_camera(camera, probe=fake_probe)
    path.unlink()
    stats = sync_camera(camera, probe=fake_probe)
    rec = Recording.objects.get()
    assert stats.marked_missing == 1 and rec.status == Status.MISSING


def test_missing_mount_does_not_mark_everything_missing(camera, recordings_root, make_file, fake_probe, settings, tmp_path):
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv")
    sync_camera(camera, probe=fake_probe)
    settings.CCTV_RECORDINGS_ROOT = tmp_path / "unmounted"
    stats = sync_camera(camera, probe=fake_probe)
    assert stats.root_missing
    assert Recording.objects.get().status == Status.COMPLETE


def test_legacy_files_are_indexed(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25_13-02-21.mkv", 100)
    make_file(recordings_root / "2026-10-26" / "2026-10-26_13-05-32.mkv", 0)
    make_file(recordings_root / "test.mkv", 100)
    fake_probe.duration = 30.0
    sync_camera(camera, probe=fake_probe)
    recs = {r.relative_file_path: r for r in Recording.objects.all()}
    assert set(recs) == {"2026-10-25_13-02-21.mkv", "2026-10-26/2026-10-26_13-05-32.mkv"}
    flat = recs["2026-10-25_13-02-21.mkv"]
    assert flat.source == Recording.Source.LEGACY and flat.start_hour == 13
    assert flat.start_time == local_dt(2026, 10, 25, 13, 2, 21)
    assert flat.end_time == flat.start_time + timedelta(seconds=30)
    assert recs["2026-10-26/2026-10-26_13-05-32.mkv"].status == Status.CORRUPT


def test_multiple_cameras_use_their_own_roots(camera, recordings_root, make_file, fake_probe):
    from cameras.models import Camera

    parking = Camera.objects.create(name="Parking", slug="parking", stream_path="parking", recording_root="parking")
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv")
    make_file(recordings_root / "parking" / "2026-10-25" / "13-14" / "recording.mkv")
    sync_camera(camera, probe=fake_probe)
    sync_camera(parking, probe=fake_probe)
    assert Recording.objects.filter(camera=camera).count() == 1
    assert Recording.objects.filter(camera=parking).count() == 1


def test_unique_hourly_slot_constraint(camera):
    common = dict(camera=camera, recording_date=date(2026, 10, 25), start_hour=13, status=Status.COMPLETE)
    Recording.objects.create(relative_file_path="2026-10-25/13-14/recording.mkv", **common)
    with pytest.raises(IntegrityError), transaction.atomic():
        Recording.objects.create(relative_file_path="2026-10-25/13-14/other.mkv", **common)
    # Legacy files may share a slot.
    Recording.objects.create(relative_file_path="a.mkv", source=Recording.Source.LEGACY, **common)


def test_sync_all_takes_storage_snapshot(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv", 4096)
    sync_all(probe=fake_probe)
    snap = StorageSnapshot.objects.get()
    assert snap.recordings_bytes == 4096
    assert snap.recording_count == 1
    assert snap.total_bytes > 0


def test_sync_command_runs(camera, recordings_root, make_file, monkeypatch, fake_probe):
    from django.core.management import call_command

    monkeypatch.setattr("core.media.probe", fake_probe)
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv")
    call_command("sync_recordings")
    call_command("sync_recordings")
    assert Recording.objects.count() == 1
    assert len(fake_probe.calls) == 1
