from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .live import camera_status
from .models import Camera
from .serializers import CameraSerializer


class CameraViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Camera.objects.all()
    serializer_class = CameraSerializer

    @extend_schema(responses=OpenApiTypes.OBJECT)
    @action(detail=True, methods=["get"])
    def status(self, request, pk=None):
        return Response(camera_status(self.get_object()))
