from django.urls import path

from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.live, name="live"),
    path("recordings/", views.recordings, name="recordings"),
    path("recordings/partials/calendar/", views.calendar_partial, name="calendar-partial"),
    path("recordings/partials/hours/", views.hours_partial, name="hours-partial"),
    path("recordings/partials/player/<uuid:recording_id>/", views.player_partial, name="player-partial"),
    path("cameras/", views.cameras, name="cameras"),
    path("cameras/partials/<uuid:camera_id>/status/", views.camera_status_partial, name="camera-status-partial"),
    path("storage/", views.storage, name="storage"),
]
