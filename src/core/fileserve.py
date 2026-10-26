"""Serve large files without loading them into memory.

In production nginx does the transfer via X-Accel-Redirect (with native Range support).
Without nginx (development/tests) Django streams the file in chunks and honours a
single HTTP Range.
"""

import mimetypes
import re
from pathlib import Path
from urllib.parse import quote

from django.conf import settings
from django.http import HttpResponse, StreamingHttpResponse
from django.utils.http import http_date

CHUNK_SIZE = 256 * 1024
_RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")

mimetypes.add_type("video/x-matroska", ".mkv")
mimetypes.add_type("video/mp4", ".mp4")


def _content_disposition(filename: str, attachment: bool) -> str:
    kind = "attachment" if attachment else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode() or "video"
    return f"{kind}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def parse_range(header: str | None, size: int) -> tuple[int, int] | None | bool:
    """Return (start, end) inclusive, None for "no/ignored range", False if unsatisfiable."""
    if not header:
        return None
    match = _RANGE_RE.match(header.strip())
    if not match:
        return None  # Multi-range or malformed: serve the full body.
    first, last = match.groups()
    if first == "" and last == "":
        return None
    if first == "":
        length = int(last)
        if length == 0:
            return False
        start, end = max(size - length, 0), size - 1
    else:
        start = int(first)
        end = int(last) if last else size - 1
        end = min(end, size - 1)
    if start >= size or start > end:
        return False
    return start, end


def _iter_file(path: Path, start: int, length: int):
    with open(path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(CHUNK_SIZE, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


def serve_file(
    request,
    path: Path,
    *,
    x_accel_path: str | None,
    filename: str,
    attachment: bool = False,
    content_type: str | None = None,
):
    content_type = content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    stat = path.stat()

    if settings.CCTV_USE_X_ACCEL and x_accel_path:
        response = HttpResponse(content_type=content_type)
        response["X-Accel-Redirect"] = quote(x_accel_path)
        response["Content-Disposition"] = _content_disposition(filename, attachment)
        response["Cache-Control"] = "private, max-age=0"
        return response

    size = stat.st_size
    byte_range = parse_range(request.headers.get("Range"), size)
    if byte_range is False:
        response = HttpResponse(status=416)
        response["Content-Range"] = f"bytes */{size}"
        return response
    if byte_range is None:
        start, end, status = 0, size - 1, 200
    else:
        (start, end), status = byte_range, 206

    length = max(end - start + 1, 0)
    response = StreamingHttpResponse(_iter_file(path, start, length), status=status, content_type=content_type)
    response["Content-Length"] = str(length)
    response["Accept-Ranges"] = "bytes"
    response["Last-Modified"] = http_date(stat.st_mtime)
    response["Content-Disposition"] = _content_disposition(filename, attachment)
    response["Cache-Control"] = "private, max-age=0"
    if status == 206:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response


def x_accel_join(prefix: str, *parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p and p.strip("/") not in ("", "."))
    return prefix + joined if joined else prefix
