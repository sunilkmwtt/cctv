from django.urls import path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.routers import SimpleRouter

from cameras.api import CameraViewSet
from recordings.api import RecordingViewSet, StorageView

router = SimpleRouter()
router.register("cameras", CameraViewSet, basename="camera")
router.register("recordings", RecordingViewSet, basename="recording")

urlpatterns = [
    path("storage/", StorageView.as_view(), name="storage"),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
    *router.urls,
]
