from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from core.views import stream_auth

admin.site.site_header = "Home CCTV administration"
admin.site.site_title = "Home CCTV admin"

urlpatterns = [
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("api/v1/", include("config.api_urls")),
    # Called by nginx auth_request for every HLS playlist/segment request.
    path("internal/stream-auth/", stream_auth, name="stream-auth"),
    path("", include("dashboard.urls")),
]
