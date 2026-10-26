import logging
from pathlib import PurePosixPath

from django.conf import settings
from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from cameras.models import Camera
from core.exceptions import ServiceError
from core.fileserve import serve_file, x_accel_join
from core.paths import UnsafePathError

from .models import Recording
from .playback import PlaybackCache, PlaybackError, PlaybackState
from .serializers import CalendarQuerySerializer, RecordingFilterSerializer, RecordingSerializer
from .services import month_summary, storage_summary

logger = logging.getLogger(__name__)


class RecordingViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = RecordingSerializer

    def get_queryset(self):
        queryset = Recording.objects.select_related("camera")
        if self.action != "list":
            return queryset
        filters = RecordingFilterSerializer(data=self.request.query_params)
        filters.is_valid(raise_exception=True)
        data = filters.validated_data
        if "camera_id" in data:
            queryset = queryset.filter(camera_id=data["camera_id"])
        if "date" in data:
            queryset = queryset.filter(recording_date=data["date"])
        if "status" in data:
            queryset = queryset.filter(status=data["status"])
        return queryset.order_by("-recording_date", "start_hour", "start_time")

    @extend_schema(parameters=[
        OpenApiParameter("camera_id", OpenApiTypes.UUID), OpenApiParameter("date", OpenApiTypes.DATE),
        OpenApiParameter("status", OpenApiTypes.STR),
    ])
    def list(self, request, *args, **kwargs):
        return super().list(request, *args, **kwargs)

    def _source_path(self, recording: Recording):
        try:
            path = recording.absolute_path()
        except UnsafePathError:
            logger.error("Rejected unsafe stored path for recording %s", recording.id)
            raise Http404
        if not path.is_file():
            raise ServiceError("The recording file is no longer on disk.", status_code=410, code="missing")
        return path

    @extend_schema(
        description="Streams a browser-compatible MP4 (supports Range). Returns 202 while the "
        "MKV is being remuxed; retry after the Retry-After seconds.",
        responses={(200, "video/mp4"): OpenApiTypes.BINARY, 202: OpenApiTypes.OBJECT},
    )
    @action(detail=True, methods=["get"])
    def play(self, request, pk=None):
        recording = self.get_object()
        cache = PlaybackCache()
        try:
            result = cache.get_or_start(recording)
        except UnsafePathError:
            raise Http404
        except PlaybackError as exc:
            raise ServiceError(str(exc), status_code=exc.http_status, code=exc.code)
        if result.state != PlaybackState.READY:
            response = Response(
                {"status": result.state.value, "detail": "The video is being prepared for playback."},
                status=status.HTTP_202_ACCEPTED,
            )
            response["Retry-After"] = "3"
            response["Cache-Control"] = "no-store"
            return response
        return serve_file(
            request, result.path,
            x_accel_path=x_accel_join(settings.CCTV_X_ACCEL_CACHE_PREFIX, result.path.name),
            filename=f"{PurePosixPath(recording.download_filename()).stem}.mp4",
            content_type="video/mp4",
        )

    @extend_schema(responses={(200, "application/octet-stream"): OpenApiTypes.BINARY})
    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        recording = self.get_object()
        if not recording.is_downloadable:
            if recording.status == Recording.Status.RECORDING:
                raise ServiceError("This hour is still being recorded.", status_code=409, code="recording_in_progress")
            raise ServiceError("The recording file is not available.", status_code=410, code="missing")
        path = self._source_path(recording)
        return serve_file(
            request, path,
            x_accel_path=x_accel_join(
                settings.CCTV_X_ACCEL_RECORDINGS_PREFIX, recording.camera.recording_root, recording.relative_file_path
            ),
            filename=recording.download_filename(),
            attachment=True,
        )

    @extend_schema(parameters=[CalendarQuerySerializer], responses=OpenApiTypes.OBJECT)
    @action(detail=False, methods=["get"])
    def calendar(self, request):
        query = CalendarQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        try:
            camera = Camera.objects.get(pk=query.validated_data["camera_id"])
        except Camera.DoesNotExist:
            raise Http404
        year, month = map(int, query.validated_data["month"].split("-"))
        return Response({
            "camera_id": str(camera.id),
            "month": query.validated_data["month"],
            "days": month_summary(camera, year, month),
        })


class StorageView(APIView):
    @extend_schema(responses=OpenApiTypes.OBJECT)
    def get(self, request):
        return Response(storage_summary())
