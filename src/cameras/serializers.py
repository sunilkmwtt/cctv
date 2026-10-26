from rest_framework import serializers

from .models import Camera


class CameraSerializer(serializers.ModelSerializer):
    """Public camera metadata. Credentials are never part of this representation."""

    hls_url = serializers.CharField(read_only=True)

    class Meta:
        model = Camera
        fields = [
            "id",
            "name",
            "slug",
            "location",
            "camera_type",
            "stream_protocol",
            "stream_path",
            "hls_url",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields
