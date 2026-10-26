import io
import json
import urllib.error
from datetime import date

import pytest
from django.core.management import call_command

from cameras import live
from cameras.models import Camera, CameraCredential
from recordings.models import Recording

pytestmark = pytest.mark.django_db
TEST_KEY = "dGVzdC1vbmx5LWZlcm5ldC1rZXktMzItYnl0ZXMhISE="


# --- nginx auth_request endpoint -------------------------------------------------
def test_stream_auth_rejects_anonymous(client, camera):
    response = client.get("/internal/stream-auth/", HTTP_X_ORIGINAL_URI="/stream/hls/entrance/index.m3u8")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "uri", ["/stream/hls/entrance/index.m3u8", "/stream/hls/entrance/seg12.mp4", "/stream/hls/entrance/x.m3u8?a=1"]
)
def test_stream_auth_allows_playlists_and_every_segment(auth_client, camera, uri):
    assert auth_client.get("/internal/stream-auth/", HTTP_X_ORIGINAL_URI=uri).status_code == 204


@pytest.mark.parametrize("uri", ["/stream/hls/unknown/index.m3u8", "/stream/hls/", "/admin/", ""])
def test_stream_auth_rejects_unknown_paths(auth_client, camera, uri):
    assert auth_client.get("/internal/stream-auth/", HTTP_X_ORIGINAL_URI=uri).status_code == 403


def test_stream_auth_rejects_disabled_camera(auth_client, camera):
    Camera.objects.filter(pk=camera.pk).update(enabled=False)
    response = auth_client.get("/internal/stream-auth/", HTTP_X_ORIGINAL_URI="/stream/hls/entrance/index.m3u8")
    assert response.status_code == 403


# --- MediaMTX status / source failures ----------------------------------------------
class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_stream_ready(monkeypatch):
    payload = {"ready": True, "tracks": ["H264", "MPEG-4 Audio"], "readers": [{"type": "hlsMuxer"}]}
    monkeypatch.setattr(live.urllib.request, "urlopen", lambda *a, **k: FakeResponse(json.dumps(payload).encode()))
    status = live._fetch_stream_status("entrance")
    assert status.state == "ready" and status.ready and status.readers == 1


def test_stream_source_down_is_standby(monkeypatch):
    """Camera unreachable: MediaMTX reports the path but not ready."""
    monkeypatch.setattr(live.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b'{"ready": false}'))
    assert live._fetch_stream_status("entrance").state == "standby"


def test_stream_path_unknown_to_mediamtx(monkeypatch):
    def raise_404(*a, **k):
        raise urllib.error.HTTPError("u", 404, "not found", {}, None)

    monkeypatch.setattr(live.urllib.request, "urlopen", raise_404)
    assert live._fetch_stream_status("entrance").state == "standby"


def test_mediamtx_down(monkeypatch):
    def refuse(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(live.urllib.request, "urlopen", refuse)
    assert live._fetch_stream_status("entrance").state == "unavailable"


def test_mediamtx_garbage_response(monkeypatch):
    monkeypatch.setattr(live.urllib.request, "urlopen", lambda *a, **k: FakeResponse(b"<html>"))
    assert live._fetch_stream_status("entrance").state == "unavailable"


def test_recorder_activity_detects_current_part(camera, recordings_root, make_file):
    from django.utils import timezone

    now = timezone.localtime()
    folder, slot = live.current_slot_dir(camera, now)
    assert slot == f"{now.hour:02d}-{(now.hour + 1) % 24:02d}"
    make_file(folder / ".parts" / "x.mkv", 10, age=5)
    assert live.recorder_activity(camera).active
    make_file(folder / ".parts" / "x.mkv", 10, age=600)
    assert not live.recorder_activity(camera).active


# --- HTML views -------------------------------------------------------------------------
PAGES = ["/", "/recordings/", "/cameras/", "/storage/"]


@pytest.mark.parametrize("url", PAGES)
def test_pages_redirect_anonymous_to_login(client, url):
    response = client.get(url)
    assert response.status_code == 302 and response["Location"].startswith("/login/")


@pytest.mark.parametrize("url", PAGES)
def test_pages_render(auth_client, camera, url):
    response = auth_client.get(url)
    assert response.status_code == 200
    assert "Content-Security-Policy" in response
    assert "Main Entrance" in response.content.decode() or url == "/storage/"


def test_recordings_page_shows_24_slots(auth_client, camera):
    Recording.objects.create(camera=camera, recording_date=date(2026, 10, 25), start_hour=23,
                             relative_file_path="2026-10-25/23-00/recording.mkv", status="complete",
                             duration_seconds=3599, file_size_bytes=10)
    html = auth_client.get("/recordings/?date=2026-10-25").content.decode()
    assert html.count('class="hour ') == 24
    assert "23-00" in html and "00-01" in html
    assert "59m 59s" in html


def test_htmx_partial_with_expired_session(client, camera):
    response = client.get("/recordings/partials/hours/?date=2026-10-25", HTTP_HX_REQUEST="true")
    assert response.status_code == 401
    assert response["HX-Redirect"].startswith("/login/")


def test_player_partial(auth_client, camera):
    rec = Recording.objects.create(camera=camera, recording_date=date(2026, 10, 25), start_hour=1,
                                   relative_file_path="2026-10-25/01-02/recording.mkv", status="complete",
                                   file_size_bytes=10)
    html = auth_client.get(f"/recordings/partials/player/{rec.id}/").content.decode()
    assert f"/api/v1/recordings/{rec.id}/play/" in html
    assert f"/api/v1/recordings/{rec.id}/download/" in html


def test_login_flow(client, user):
    assert client.get("/login/").status_code == 200
    response = client.post("/login/", {"username": "viewer", "password": "correct-horse-battery"})
    assert response.status_code == 302
    assert client.get("/").status_code == 200
    assert client.get("/logout/").status_code == 405  # logout must be POST (CSRF-protected)
    client.post("/logout/")
    assert client.get("/").status_code == 302


def test_healthz(client):
    response = client.get("/healthz/", HTTP_HOST="not-in-allowed-hosts")
    assert response.status_code == 200 and response.json()["status"] == "ok"


def test_internal_auth_endpoint_rejects_post(auth_client):
    assert auth_client.post("/internal/stream-auth/").status_code in (403, 405)


# --- credentials ------------------------------------------------------------------------
def test_export_mediamtx_env(camera, settings, capsys):
    settings.CCTV_FIELD_ENCRYPTION_KEY = TEST_KEY
    cred = CameraCredential(camera=camera)
    cred.set_rtsp_url("rtsp://u:p@10.0.0.4:8554/live")
    cred.save()
    call_command("export_mediamtx_env")
    assert capsys.readouterr().out.strip() == "MTX_PATHS_ENTRANCE_SOURCE=rtsp://u:p@10.0.0.4:8554/live"


def test_credential_admin_is_superuser_only(client, django_user_model, camera):
    staff = django_user_model.objects.create_user("staff", password="x" * 12, is_staff=True)
    client.force_login(staff)
    assert client.get("/admin/cameras/cameracredential/").status_code == 403
