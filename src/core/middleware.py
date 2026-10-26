import logging
import time

from django.db import connection
from django.http import HttpResponse, JsonResponse

logger = logging.getLogger("cctv.requests")

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "media-src 'self' blob:",
        "worker-src 'self' blob:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)


class HealthCheckMiddleware:
    """Answer /healthz/ before host validation so container health checks work on any Host."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path != "/healthz/":
            return self.get_response(request)
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
        except Exception:  # noqa: BLE001 - any DB failure means unhealthy
            logger.warning("Health check: database unavailable")
            return JsonResponse({"status": "error", "database": "unavailable"}, status=503)
        return JsonResponse({"status": "ok"})


class RequestLogMiddleware:
    """One log line per request. Query strings and headers are not logged."""

    QUIET_PREFIXES = ("/static/", "/internal/stream-auth/")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        started = time.monotonic()
        response = self.get_response(request)
        if not request.path.startswith(self.QUIET_PREFIXES) or response.status_code >= 500:
            user = getattr(request, "user", None)
            username = user.get_username() if user is not None and user.is_authenticated else "-"
            logger.info(
                "%s %s %s %.0fms user=%s",
                request.method,
                request.path,
                response.status_code,
                (time.monotonic() - started) * 1000,
                username,
            )
        return response


class SecurityHeadersMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response: HttpResponse = self.get_response(request)
        response.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        response.setdefault("X-Content-Type-Options", "nosniff")
        return response
