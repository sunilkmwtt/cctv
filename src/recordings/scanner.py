"""Read-only discovery of recorder output. Pure filesystem logic, no database access.

Layouts understood (all relative to a camera's recording directory):

    YYYY-MM-DD/HH-HH/recording.mkv        finalized hourly recording (current recorder)
    YYYY-MM-DD/HH-HH/.parts/*.mkv         in-progress or awaiting finalization
    YYYY-MM-DD/YYYY-MM-DD_HH-MM-SS.mkv    legacy recorder, one file per session
    YYYY-MM-DD_HH-MM-SS.mkv               legacy recorder, flat in the root

Symlinks are never followed. Files that match none of the layouts are ignored.
"""

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from core.paths import VIDEO_EXTENSIONS

logger = logging.getLogger(__name__)

FINAL_NAME = "recording.mkv"
PARTS_DIR = ".parts"
DATE_DIR_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
HOUR_DIR_RE = re.compile(r"^(\d{2})-(\d{2})$")
LEGACY_NAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})\.(mkv|mp4)$", re.IGNORECASE)
PART_NAME_RE = re.compile(r"^(\d{2})-(\d{2})-(\d{2})-(\d{1,6})\.mkv$")


def parse_date_dir(name: str) -> date | None:
    match = DATE_DIR_RE.match(name)
    if not match:
        return None
    try:
        return date(*map(int, match.groups()))
    except ValueError:
        return None


def parse_hour_dir(name: str) -> int | None:
    """'13-14' -> 13, '23-00' -> 23. Anything inconsistent (e.g. '13-15') -> None."""
    match = HOUR_DIR_RE.match(name)
    if not match:
        return None
    start, end = map(int, match.groups())
    if 0 <= start <= 23 and end == (start + 1) % 24:
        return start
    return None


def parse_legacy_name(name: str) -> datetime | None:
    match = LEGACY_NAME_RE.match(name)
    if not match:
        return None
    try:
        return datetime(*map(int, match.groups()[:6]))
    except ValueError:
        return None


def parse_part_start(slot_date: date, name: str) -> datetime | None:
    """Recorder part files are named after their local start time: HH-MM-SS-ffffff.mkv."""
    match = PART_NAME_RE.match(name)
    if not match:
        return None
    hour, minute, second, micro = match.groups()
    try:
        return datetime.combine(slot_date, datetime.min.time()).replace(
            hour=int(hour), minute=int(minute), second=int(second), microsecond=int(micro.ljust(6, "0"))
        )
    except ValueError:
        return None


@dataclass(frozen=True)
class FileInfo:
    relative_path: str
    size: int
    mtime_ns: int


@dataclass
class HourlySlot:
    recording_date: date
    start_hour: int
    relative_path: str  # expected/actual "YYYY-MM-DD/HH-HH/recording.mkv"
    final: FileInfo | None = None
    parts_dir_exists: bool = False
    parts: list[FileInfo] = field(default_factory=list)
    first_part_start: datetime | None = None  # naive local time

    @property
    def parts_size(self) -> int:
        return sum(p.size for p in self.parts)

    @property
    def latest_part_mtime_ns(self) -> int | None:
        return max((p.mtime_ns for p in self.parts), default=None)


@dataclass
class LegacyFile:
    recording_date: date
    start_hour: int
    started_at: datetime  # naive local time
    file: FileInfo


@dataclass
class ScanResult:
    slots: list[HourlySlot] = field(default_factory=list)
    legacy: list[LegacyFile] = field(default_factory=list)
    total_bytes: int = 0
    ignored: list[str] = field(default_factory=list)


def _file_info(entry: os.DirEntry, relative: str) -> FileInfo:
    stat = entry.stat(follow_symlinks=False)
    return FileInfo(relative_path=relative, size=stat.st_size, mtime_ns=stat.st_mtime_ns)


def _entries(path: str) -> list[os.DirEntry]:
    try:
        with os.scandir(path) as it:
            return sorted(it, key=lambda e: e.name)
    except OSError as exc:
        logger.warning("Cannot list %s: %s", path, exc)
        return []


def _legacy(info: FileInfo, name: str, result: ScanResult) -> None:
    started = parse_legacy_name(name)
    if started is None:
        result.ignored.append(info.relative_path)
        return
    result.legacy.append(
        LegacyFile(recording_date=started.date(), start_hour=started.hour, started_at=started, file=info)
    )


def _scan_hour_dir(root: str, day: date, day_name: str, hour: int, hour_name: str, result: ScanResult) -> None:
    rel_dir = f"{day_name}/{hour_name}"
    slot = HourlySlot(recording_date=day, start_hour=hour, relative_path=f"{rel_dir}/{FINAL_NAME}")
    for entry in _entries(os.path.join(root, day_name, hour_name)):
        if entry.is_symlink():
            continue
        if entry.name == PARTS_DIR and entry.is_dir(follow_symlinks=False):
            slot.parts_dir_exists = True
            for part in _entries(entry.path):
                if part.is_symlink() or not part.is_file(follow_symlinks=False):
                    continue
                info = _file_info(part, f"{rel_dir}/{PARTS_DIR}/{part.name}")
                result.total_bytes += info.size
                if part.name.endswith(".mkv") and part.name != "merged.mkv":
                    slot.parts.append(info)
                    started = parse_part_start(day, part.name)
                    if started and (slot.first_part_start is None or started < slot.first_part_start):
                        slot.first_part_start = started
        elif entry.is_file(follow_symlinks=False):
            info = _file_info(entry, f"{rel_dir}/{entry.name}")
            result.total_bytes += info.size
            if entry.name == FINAL_NAME:
                slot.final = info
            elif os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS:
                result.ignored.append(info.relative_path)
    if slot.final or slot.parts_dir_exists:
        result.slots.append(slot)


def scan_camera_dir(root) -> ScanResult:
    root = str(root)
    result = ScanResult()
    for entry in _entries(root):
        if entry.is_symlink() or entry.name.startswith("."):
            continue
        if entry.is_dir(follow_symlinks=False):
            day = parse_date_dir(entry.name)
            if day is None:
                continue  # e.g. another camera's namespace folder
            for child in _entries(entry.path):
                if child.is_symlink() or child.name.startswith("."):
                    continue
                if child.is_dir(follow_symlinks=False):
                    hour = parse_hour_dir(child.name)
                    if hour is not None:
                        _scan_hour_dir(root, day, entry.name, hour, child.name, result)
                elif child.is_file(follow_symlinks=False):
                    info = _file_info(child, f"{entry.name}/{child.name}")
                    result.total_bytes += info.size
                    if os.path.splitext(child.name)[1].lower() in VIDEO_EXTENSIONS:
                        _legacy(info, child.name, result)
        elif entry.is_file(follow_symlinks=False):
            if os.path.splitext(entry.name)[1].lower() in VIDEO_EXTENSIONS:
                info = _file_info(entry, entry.name)
                result.total_bytes += info.size
                _legacy(info, entry.name, result)
    return result
