import os

import pytest

from core.paths import VIDEO_EXTENSIONS, UnsafePathError, resolve_within, validate_relative_path


@pytest.mark.parametrize(
    "value",
    [
        "../etc/passwd",
        "2026-10-26/../../etc/passwd",
        "/etc/passwd",
        "2026-10-26//13-14/recording.mkv",
        "2026-10-26/13-14/.parts/13-00-00-000000.mkv",
        ".hidden.mkv",
        "./recording.mkv",
        "2026-10-26\\13-14\\recording.mkv",
        "recording.mkv\x00.txt",
        "",
    ],
)
def test_rejects_unsafe_relative_paths(value):
    with pytest.raises(UnsafePathError):
        validate_relative_path(value, allowed_extensions=VIDEO_EXTENSIONS)


def test_rejects_disallowed_extension():
    with pytest.raises(UnsafePathError):
        validate_relative_path("2026-10-26/13-14/recording.sh", allowed_extensions=VIDEO_EXTENSIONS)


def test_accepts_normal_recording_path():
    path = validate_relative_path("2026-10-26/23-00/recording.mkv", allowed_extensions=VIDEO_EXTENSIONS)
    assert str(path) == "2026-10-26/23-00/recording.mkv"


def test_empty_allowed_for_camera_root():
    assert str(validate_relative_path("", allow_empty=True)) == "."


def test_resolve_within_blocks_symlink_escape(tmp_path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.mkv").write_text("x")
    os.symlink(outside, root / "2026-10-26")
    with pytest.raises(UnsafePathError):
        resolve_within(root, "2026-10-26/secret.mkv", allowed_extensions=VIDEO_EXTENSIONS)


def test_resolve_within_returns_path_inside_root(tmp_path):
    (tmp_path / "a").mkdir()
    assert resolve_within(tmp_path, "a/b.mkv") == (tmp_path / "a" / "b.mkv").resolve()
