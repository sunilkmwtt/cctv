import calendar
from datetime import date, datetime
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET

from cameras.live import camera_status
from cameras.models import Camera
from recordings.models import Recording
from recordings.services import hourly_slots, month_summary, storage_summary


def login_required_htmx(view):
    """Like login_required, but tells htmx to do a full-page redirect instead of swapping a login form."""

    @wraps(view)
    def wrapper(request, *args, **kwargs):
        if not request.user.is_authenticated and request.headers.get("HX-Request"):
            response = HttpResponse(status=401)
            response["HX-Redirect"] = f"{reverse('login')}?next={request.headers.get('HX-Current-URL', '/')}"
            return response
        return login_required(view)(request, *args, **kwargs)

    return wrapper


def _parse_date(value: str | None, default: date) -> date:
    try:
        return date.fromisoformat(value) if value else default
    except ValueError:
        return default


def _parse_month(value: str | None, default: date) -> date:
    try:
        return datetime.strptime(value, "%Y-%m").date() if value else default.replace(day=1)
    except ValueError:
        return default.replace(day=1)


def _selected_camera(request):
    cameras = list(Camera.objects.all())
    slug = request.GET.get("camera")
    selected = next((c for c in cameras if c.slug == slug), None)
    if selected is None:
        selected = next((c for c in cameras if c.enabled), cameras[0] if cameras else None)
    return cameras, selected


def _calendar_context(camera, month_start: date, selected: date) -> dict:
    summary = {row["date"]: row for row in month_summary(camera, month_start.year, month_start.month)} if camera else {}
    weeks = []
    for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_start.year, month_start.month):
        weeks.append([
            {"date": day, "in_month": day.month == month_start.month, "info": summary.get(day),
             "selected": day == selected, "today": day == timezone.localdate()}
            for day in week
        ])
    return {
        "camera": camera,
        "month": month_start,
        "weeks": weeks,
        "prev_month": _shift_month(month_start, -1),
        "next_month": _shift_month(month_start, 1),
        "selected_date": selected,
        "weekday_names": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
    }


def _shift_month(month_start: date, delta: int) -> date:
    index = month_start.year * 12 + month_start.month - 1 + delta
    return date(index // 12, index % 12 + 1, 1)


@require_GET
@login_required
def live(request):
    cameras = Camera.objects.filter(enabled=True)
    today = timezone.localdate()
    return render(request, "dashboard/live.html", {
        "cameras": cameras,
        "storage": storage_summary(),
        "today_count": Recording.objects.filter(recording_date=today, status=Recording.Status.COMPLETE).count(),
        "nav": "live",
    })


@require_GET
@login_required_htmx
def camera_status_partial(request, camera_id):
    camera = get_object_or_404(Camera, pk=camera_id)
    return render(request, "dashboard/partials/camera_status.html", {"camera": camera, "status": camera_status(camera)})


@require_GET
@login_required
def recordings(request):
    cameras, camera = _selected_camera(request)
    selected = _parse_date(request.GET.get("date"), timezone.localdate())
    context = {
        "cameras": cameras,
        "camera": camera,
        "selected_date": selected,
        "slots": hourly_slots(camera, selected) if camera else [],
        "nav": "recordings",
        **_calendar_context(camera, _parse_month(request.GET.get("month"), selected), selected),
    }
    return render(request, "dashboard/recordings.html", context)


@require_GET
@login_required_htmx
def calendar_partial(request):
    _, camera = _selected_camera(request)
    selected = _parse_date(request.GET.get("date"), timezone.localdate())
    month = _parse_month(request.GET.get("month"), selected)
    return render(request, "dashboard/partials/calendar.html", _calendar_context(camera, month, selected))


@require_GET
@login_required_htmx
def hours_partial(request):
    _, camera = _selected_camera(request)
    selected = _parse_date(request.GET.get("date"), timezone.localdate())
    return render(request, "dashboard/partials/hours.html", {
        "camera": camera, "selected_date": selected, "slots": hourly_slots(camera, selected) if camera else [],
    })


@require_GET
@login_required_htmx
def player_partial(request, recording_id):
    recording = get_object_or_404(Recording.objects.select_related("camera"), pk=recording_id)
    return render(request, "dashboard/partials/player.html", {"recording": recording})


@require_GET
@login_required
def cameras(request):
    items = [{"camera": c, "status": camera_status(c)} for c in Camera.objects.all()]
    return render(request, "dashboard/cameras.html", {"items": items, "nav": "cameras"})


@require_GET
@login_required
def storage(request):
    summary = storage_summary()
    total, used = summary["total_bytes"], summary["used_bytes"]
    used_percent = round(used * 100 / total, 1) if total else None
    return render(request, "dashboard/storage.html", {"storage": summary, "used_percent": used_percent, "nav": "storage"})
