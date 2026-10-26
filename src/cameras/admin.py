from django import forms
from django.contrib import admin

from .models import Camera, CameraCredential


@admin.register(Camera)
class CameraAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "location", "camera_type", "stream_path", "recording_root", "enabled")
    list_filter = ("enabled", "camera_type")
    search_fields = ("name", "slug", "location")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("id", "created_at", "updated_at")


class CameraCredentialForm(forms.ModelForm):
    rtsp_url = forms.CharField(
        label="RTSP URL",
        required=True,
        widget=forms.PasswordInput(render_value=False),
        help_text="Write-only. Stored encrypted; never displayed or returned by the API.",
    )

    class Meta:
        model = CameraCredential
        fields = ("camera",)

    def clean_rtsp_url(self):
        value = self.cleaned_data["rtsp_url"].strip()
        if not value.lower().startswith(("rtsp://", "rtsps://")):
            raise forms.ValidationError("Must be an rtsp:// or rtsps:// URL.")
        return value

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.set_rtsp_url(self.cleaned_data["rtsp_url"])
        if commit:
            instance.save()
        return instance


@admin.register(CameraCredential)
class CameraCredentialAdmin(admin.ModelAdmin):
    """Superuser-only. The secret is write-only in the UI."""

    form = CameraCredentialForm
    list_display = ("camera", "updated_at")
    readonly_fields = ("updated_at",)

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
