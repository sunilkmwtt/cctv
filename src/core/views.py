from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.http import require_GET

from cameras.models import Camera


@require_GET
def stream_auth(request):
    """nginx auth_request target guarding every HLS playlist and segment.

    nginx passes the original URI in X-Original-URI. 204 allows, 401/403 deny.
    """
    if not request.user.is_authenticated:
        return HttpResponse(status=401)
    original = request.headers.get("X-Original-URI", "")
    prefix = settings.CCTV_HLS_PUBLIC_PREFIX
    if not original.startswith(prefix):
        return HttpResponse(status=403)
    stream_path = original[len(prefix):].split("/", 1)[0].split("?", 1)[0]
    if not stream_path or not Camera.objects.filter(stream_path=stream_path, enabled=True).exists():
        return HttpResponse(status=403)
    response = HttpResponse(status=204)
    response["Cache-Control"] = "no-store"
    return response
