import os
from datetime import date, datetime

import pytest

from recordings.scanner import parse_date_dir, parse_hour_dir, parse_legacy_name, parse_part_start, scan_camera_dir


@pytest.mark.parametrize(
    ("name", "expected"),
    [("00-01", 0), ("13-14", 13), ("22-23", 22), ("23-00", 23)],
)
def test_parse_hour_dir_valid(name, expected):
    assert parse_hour_dir(name) == expected


@pytest.mark.parametrize("name", ["13-15", "24-01", "23-24", "1-2", "ab-cd", "13-14-15", "00-00"])
def test_parse_hour_dir_invalid(name):
    assert parse_hour_dir(name) is None


def test_parse_date_dir():
    assert parse_date_dir("2026-10-26") == date(2026, 10, 26)
    assert parse_date_dir("2026-02-30") is None
    assert parse_date_dir("2026-10-26_13-05-32.mkv") is None
    assert parse_date_dir("parking") is None


def test_parse_legacy_and_part_names():
    assert parse_legacy_name("2026-10-25_13-02-21.mkv") == datetime(2026, 10, 25, 13, 2, 21)
    assert parse_legacy_name("test.mkv") is None
    assert parse_part_start(date(2026, 10, 26), "17-05-45-500606.mkv") == datetime(2026, 10, 26, 17, 5, 45, 500606)
    assert parse_part_start(date(2026, 10, 26), "merged.mkv") is None


def test_scan_real_layout(recordings_root, make_file):
    """Mirrors what is actually on the server (inspected 2026-10-26)."""
    r = recordings_root
    make_file(r / "2026-10-25_13-02-21.mkv", 100)                       # legacy flat
    make_file(r / "test.mkv", 100)                                       # unrecognised
    make_file(r / "2026-10-26" / "2026-10-26_13-05-32.mkv", 0)           # legacy, empty
    make_file(r / "2026-10-26" / "2026-10-26_13-09-17.mkv", 500)         # legacy in date dir
    make_file(r / "2026-10-26" / "13-14" / "recording.mkv", 1000)        # finalized
    make_file(r / "2026-10-26" / "17-18" / ".parts" / "17-05-45-500606.mkv", 50)
    make_file(r / "2026-10-26" / "17-18" / ".parts" / "17-07-34-675541.mkv", 70)
    make_file(r / "2026-10-26" / "17-18" / ".parts" / "files.txt", 10)   # concat manifest
    make_file(r / "2026-10-26" / "23-00" / "recording.mkv", 10)
    (r / "2026-10-26" / "not-an-hour").mkdir()
    (r / "parking").mkdir()                                              # another camera's namespace

    result = scan_camera_dir(r)

    slots = {(s.recording_date, s.start_hour): s for s in result.slots}
    assert set(slots) == {(date(2026, 10, 26), 13), (date(2026, 10, 26), 17), (date(2026, 10, 26), 23)}
    assert slots[(date(2026, 10, 26), 13)].final.size == 1000
    in_progress = slots[(date(2026, 10, 26), 17)]
    assert in_progress.final is None
    assert len(in_progress.parts) == 2
    assert in_progress.first_part_start == datetime(2026, 10, 26, 17, 5, 45, 500606)
    assert slots[(date(2026, 10, 26), 23)].relative_path == "2026-10-26/23-00/recording.mkv"

    legacy = sorted(l.file.relative_path for l in result.legacy)
    assert legacy == [
        "2026-10-25_13-02-21.mkv",
        "2026-10-26/2026-10-26_13-05-32.mkv",
        "2026-10-26/2026-10-26_13-09-17.mkv",
    ]
    assert "test.mkv" in result.ignored
    assert result.total_bytes == 100 + 100 + 0 + 500 + 1000 + 50 + 70 + 10 + 10


def test_scan_ignores_symlinks(recordings_root, tmp_path, make_file):
    outside = tmp_path / "outside"
    make_file(outside / "13-14" / "recording.mkv", 10)
    day = recordings_root / "2026-10-26"
    day.mkdir()
    os.symlink(outside / "13-14", day / "13-14")
    os.symlink(outside / "13-14" / "recording.mkv", recordings_root / "2026-10-25_10-00-00.mkv")
    result = scan_camera_dir(recordings_root)
    assert result.slots == [] and result.legacy == []


def test_scan_missing_root_is_empty(tmp_path):
    result = scan_camera_dir(tmp_path / "nope")
    assert result.slots == [] and result.total_bytes == 0
