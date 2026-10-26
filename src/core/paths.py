"""Filesystem path validation. Every path built from stored or user data goes through here."""

from pathlib import Path, PurePosixPath

VIDEO_EXTENSIONS = frozenset({".mkv", ".mp4"})


class UnsafePathError(ValueError):
    """Raised when a path could escape its root or is otherwise not acceptable."""


def validate_relative_path(
    value: str,
    *,
    allow_empty: bool = False,
    allowed_extensions: frozenset[str] | None = None,
) -> PurePosixPath:
    """Validate a POSIX relative path made of plain, non-hidden components."""
    if value in ("", "."):
        if allow_empty:
            return PurePosixPath(".")
        raise UnsafePathError("Path must not be empty.")
    if "\x00" in value or "\\" in value:
        raise UnsafePathError("Path contains forbidden characters.")
    path = PurePosixPath(value)
    if path.is_absolute():
        raise UnsafePathError("Path must be relative.")
    for part in value.split("/"):
        # Rejects "..", ".", "" (double slashes) and hidden entries such as ".parts".
        if not part or part.startswith("."):
            raise UnsafePathError(f"Path component {part!r} is not allowed.")
    if allowed_extensions is not None and path.suffix.lower() not in allowed_extensions:
        raise UnsafePathError(f"File extension {path.suffix!r} is not allowed.")
    return path


def resolve_within(root: Path, relative: str, **validate_kwargs) -> Path:
    """Join `relative` onto `root` and guarantee the real path stays inside `root`.

    Symlinks are resolved before the containment check, so a link pointing outside
    the root is rejected as well.
    """
    rel = validate_relative_path(relative, **validate_kwargs)
    real_root = root.resolve()
    candidate = (real_root / rel).resolve()
    if candidate != real_root and not candidate.is_relative_to(real_root):
        raise UnsafePathError("Path escapes the configured root.")
    return candidate
