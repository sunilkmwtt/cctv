from django.contrib import admin

from .models import Recording, StorageSnapshot


@admin.register(Recording)
class RecordingAdmin(admin.ModelAdmin):
    """Read-only: recordings are owned by the indexer, which mirrors the filesystem."""

    list_display = ("camera", "recording_date", "slot_label", "status", "source", "duration_seconds", "file_size_bytes")
    list_filter = ("status", "source", "camera")
    date_hierarchy = "recording_date"
    search_fields = ("relative_file_path",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StorageSnapshot)
class StorageSnapshotAdmin(admin.ModelAdmin):
    list_display = ("recorded_at", "total_bytes", "used_bytes", "available_bytes", "recordings_bytes", "recording_count")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
