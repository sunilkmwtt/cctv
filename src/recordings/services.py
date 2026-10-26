"""Query helpers shared by the API and the HTML views."""

import calendar
import shutil
from datetime import date

from django.conf import settings
from django.db.models import Count, Q

from .models import Recording, StorageSnapshot

Status = Recording.Status


def month_summary(camera, year: int, month: int) -> list[dict]:
    first = date(year, month, 1)
    last = date(year, month, calendar.monthrange(year, month)[1])
    rows = (
        Recording.objects.filter(camera=camera, recording_date__range=(first, last))
        .values("recording_date")
        .annotate(
            total=Count("id"),
            complete=Count("id", filter=Q(status=Status.COMPLETE)),
            recording=Count("id", filter=Q(status=Status.RECORDING)),
            corrupt=Count("id", filter=Q(status=Status.CORRUPT)),
            missing=Count("id", filter=Q(status=Status.MISSING)),
        )
        .order_by("recording_date")
    )
    return [{**row, "date": row.pop("recording_date")} for row in rows]


def hourly_slots(camera, day: date) -> list[dict]:
    """24 slots (00-01 .. 23-00) with the hourly recording plus any legacy files in that hour."""
    slots = [
        {"hour": h, "label": f"{h:02d}-{(h + 1) % 24:02d}", "recording": None, "legacy": []} for h in range(24)
    ]
    for rec in Recording.objects.filter(camera=camera, recording_date=day).select_related("camera"):
        slot = slots[rec.start_hour]
        if rec.source == Recording.Source.HOURLY:
            slot["recording"] = rec
        else:
            slot["legacy"].append(rec)
    for slot in slots:
        items = ([slot["recording"]] if slot["recording"] else []) + slot["legacy"]
        statuses = {r.status for r in items}
        for state in (Status.COMPLETE, Status.RECORDING, Status.CORRUPT, Status.MISSING):
            if state in statuses:
                slot["state"] = state
                break
        else:
            slot["state"] = "empty"
        slot["items"] = items
    return slots


def storage_summary() -> dict:
    snapshot = StorageSnapshot.objects.first()
    try:
        usage = shutil.disk_usage(settings.CCTV_RECORDINGS_ROOT)
        disk = {"total_bytes": usage.total, "used_bytes": usage.used, "available_bytes": usage.free}
    except OSError:
        disk = (
            {"total_bytes": snapshot.total_bytes, "used_bytes": snapshot.used_bytes,
             "available_bytes": snapshot.available_bytes}
            if snapshot else {"total_bytes": None, "used_bytes": None, "available_bytes": None}
        )
    visible = Recording.objects.exclude(status=Status.MISSING)
    dates = list(visible.order_by("-recording_date").values_list("recording_date", flat=True).distinct())
    return {
        **disk,
        "recordings_bytes": snapshot.recordings_bytes if snapshot else None,
        "recordings_bytes_measured_at": snapshot.recorded_at if snapshot else None,
        "recording_count": visible.count(),
        "complete_count": visible.filter(status=Status.COMPLETE).count(),
        "dates_with_recordings": dates,
    }
