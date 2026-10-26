from rest_framework import serializers

from .models import Recording


class RecordingSerializer(serializers.ModelSerializer):
    camera_id = serializers.UUIDField(source="camera.id", read_only=True)
    slot = serializers.CharField(source="slot_label", read_only=True)
    play_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    class Meta:
        model = Recording
        fields = [
            "id", "camera_id", "recording_date", "start_hour", "slot", "start_time", "end_time",
            "relative_file_path", "source", "file_size_bytes", "duration_seconds", "format",
            "video_codec", "audio_codec", "status", "error_message", "play_url", "download_url",
            "created_at", "updated_at",
        ]
        read_only_fields = fields

    def get_play_url(self, obj) -> str | None:
        return f"/api/v1/recordings/{obj.id}/play/" if obj.is_playable else None

    def get_download_url(self, obj) -> str | None:
        return f"/api/v1/recordings/{obj.id}/download/" if obj.is_downloadable else None


class RecordingFilterSerializer(serializers.Serializer):
    camera_id = serializers.UUIDField(required=False)
    date = serializers.DateField(required=False)
    status = serializers.ChoiceField(choices=Recording.Status.choices, required=False)


class CalendarQuerySerializer(serializers.Serializer):
    camera_id = serializers.UUIDField()
    month = serializers.RegexField(r"^\d{4}-(0[1-9]|1[0-2])$", help_text="YYYY-MM")
