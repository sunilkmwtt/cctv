import uuid
from datetime import date

import pytest

from cameras.models import Camera, CameraCredential
from recordings.indexer import sync_camera
from recordings.models import Recording

pytestmark = pytest.mark.django_db
Status = Recording.Status


@pytest.fixture
def indexed(camera, recordings_root, make_file, fake_probe):
    make_file(recordings_root / "2026-10-25" / "13-14" / "recording.mkv", 2048)
    make_file(recordings_root / "2026-10-25" / "14-15" / "recording.mkv", 0)
    make_file(recordings_root / "2026-10-26" / "09-10" / "recording.mkv", 1024)
    sync_camera(camera, probe=fake_probe)
    return {r.relative_file_path: r for r in Recording.objects.all()}


PROTECTED = [
    "/api/v1/cameras/",
    "/api/v1/recordings/",
    "/api/v1/recordings/calendar/?camera_id=00000000-0000-0000-0000-000000000000&month=2026-10",
    "/api/v1/storage/",
    "/api/v1/schema/",
]


@pytest.mark.parametrize("url", PROTECTED)
def test_api_requires_authentication(client, url):
    response = client.get(url)
    assert response.status_code == 403
    if response["Content-Type"].startswith("application/json"):
        assert response.json()["error"]["status"] == 403


def test_recording_endpoints_require_authentication(client, indexed):
    rec = indexed["2026-10-25/13-14/recording.mkv"]
    for suffix in ("", "play/", "download/"):
        assert client.get(f"/api/v1/recordings/{rec.id}/{suffix}").status_code == 403


def test_camera_list_never_exposes_credentials(auth_client, camera, settings):
    settings.CCTV_FIELD_ENCRYPTION_KEY = "dGVzdC1vbmx5LWZlcm5ldC1rZXktMzItYnl0ZXMhISE="
    cred = CameraCredential(camera=camera)
    cred.set_rtsp_url("rtsp://admin:hunter2@192.168.1.50:8554/live")
    cred.save()
    response = auth_client.get("/api/v1/cameras/")
    assert response.status_code == 200
    body = response.content.decode()
    assert "hunter2" not in body and "rtsp://" not in body
    item = response.json()["results"][0]
    assert item["name"] == "Main Entrance"
    assert item["hls_url"] == "/stream/hls/entrance/index.m3u8"
    assert "credential" not in item and "rtsp_url" not in item
    # Encrypted at rest.
    assert "hunter2" not in CameraCredential.objects.get().rtsp_url_encrypted


def test_camera_detail_and_status_with_mediamtx_down(auth_client, camera):
    assert auth_client.get(f"/api/v1/cameras/{camera.id}/").json()["slug"] == "main-entrance"
    status = auth_client.get(f"/api/v1/cameras/{camera.id}/status/").json()
    assert status["online"] is False
    assert status["live"]["state"] == "unavailable"
    assert status["recorder"]["active"] is False


def test_unknown_camera_404(auth_client):
    response = auth_client.get(f"/api/v1/cameras/{uuid.uuid4()}/")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_recordings_filter_by_camera_and_date(auth_client, camera, indexed):
    other = Camera.objects.create(name="Parking", slug="parking", stream_path="parking", recording_root="parking")
    Recording.objects.create(camera=other, recording_date=date(2026, 10, 25), start_hour=1,
                             relative_file_path="2026-10-25/01-02/recording.mkv", status=Status.COMPLETE)

    data = auth_client.get(f"/api/v1/recordings/?camera_id={camera.id}&date=2026-10-25").json()
    assert data["count"] == 2
    assert {r["slot"] for r in data["results"]} == {"13-14", "14-15"}
    assert all(r["camera_id"] == str(camera.id) for r in data["results"])

    data = auth_client.get(f"/api/v1/recordings/?camera_id={other.id}").json()
    assert data["count"] == 1

    complete = auth_client.get("/api/v1/recordings/?status=complete").json()
    assert complete["count"] == 3


@pytest.mark.parametrize("query", ["date=26-10-2026", "camera_id=not-a-uuid", "status=weird"])
def test_recordings_filter_validation(auth_client, query):
    response = auth_client.get(f"/api/v1/recordings/?{query}")
    assert response.status_code == 400
    assert response.json()["error"]["status"] == 400


def test_api_ignores_path_parameters(auth_client, indexed):
    response = auth_client.get("/api/v1/recordings/?path=../../etc/passwd&relative_file_path=/etc/passwd")
    assert response.status_code == 200  # unknown params are ignored, never used as paths


def test_calendar(auth_client, camera, indexed):
    data = auth_client.get(f"/api/v1/recordings/calendar/?camera_id={camera.id}&month=2026-10").json()
    days = {d["date"]: d for d in data["days"]}
    assert days["2026-10-25"]["complete"] == 1 and days["2026-10-25"]["corrupt"] == 1
    assert days["2026-10-26"]["total"] == 1
    assert auth_client.get(f"/api/v1/recordings/calendar/?camera_id={camera.id}&month=2026-13").status_code == 400


def test_storage(auth_client, indexed):
    data = auth_client.get("/api/v1/storage/").json()
    assert data["total_bytes"] > 0 and data["available_bytes"] > 0
    assert data["recording_count"] == 3
    assert data["dates_with_recordings"] == ["2026-10-26", "2026-10-25"]


def test_download_streams_original_with_range(auth_client, indexed, recordings_root):
    rec = indexed["2026-10-25/13-14/recording.mkv"]
    (recordings_root / rec.relative_file_path).write_bytes(bytes(range(256)) * 8)

    response = auth_client.get(f"/api/v1/recordings/{rec.id}/download/")
    assert response.status_code == 200
    assert response["Content-Disposition"].startswith('attachment; filename="main-entrance_2026-10-25_13-14.mkv"')
    assert response["Content-Type"] == "video/x-matroska"
    assert b"".join(response.streaming_content) == bytes(range(256)) * 8

    partial = auth_client.get(f"/api/v1/recordings/{rec.id}/download/", HTTP_RANGE="bytes=10-19")
    assert partial.status_code == 206
    assert partial["Content-Range"] == "bytes 10-19/2048"
    assert b"".join(partial.streaming_content) == bytes(range(10, 20))

    assert auth_client.get(f"/api/v1/recordings/{rec.id}/download/", HTTP_RANGE="bytes=5000-").status_code == 416


def test_download_uses_x_accel_when_enabled(auth_client, indexed, settings):
    settings.CCTV_USE_X_ACCEL = True
    rec = indexed["2026-10-25/13-14/recording.mkv"]
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/download/")
    assert response.status_code == 200
    assert response["X-Accel-Redirect"] == "/protected/recordings/2026-10-25/13-14/recording.mkv"
    assert response.content == b""


def test_download_rejects_tampered_stored_path(auth_client, indexed):
    rec = indexed["2026-10-25/13-14/recording.mkv"]
    Recording.objects.filter(pk=rec.pk).update(relative_file_path="../../../etc/passwd.mkv")
    assert auth_client.get(f"/api/v1/recordings/{rec.id}/download/").status_code == 404


def test_download_status_codes(auth_client, camera, indexed, recordings_root):
    in_progress = Recording.objects.create(
        camera=camera, recording_date=date(2026, 10, 26), start_hour=17, status=Status.RECORDING,
        relative_file_path="2026-10-26/17-18/recording.mkv",
    )
    assert auth_client.get(f"/api/v1/recordings/{in_progress.id}/download/").status_code == 409

    rec = indexed["2026-10-26/09-10/recording.mkv"]
    (recordings_root / rec.relative_file_path).unlink()
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/download/")
    assert response.status_code == 410
    assert response.json()["error"]["code"] == "missing"


def test_corrupt_recording_still_downloadable_but_not_playable(auth_client, camera, indexed, recordings_root):
    rec = indexed["2026-10-25/13-14/recording.mkv"]
    Recording.objects.filter(pk=rec.pk).update(status=Status.CORRUPT, error_message="bad header")
    assert auth_client.get(f"/api/v1/recordings/{rec.id}/download/").status_code == 200
    response = auth_client.get(f"/api/v1/recordings/{rec.id}/play/")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "corrupt"
    body = auth_client.get(f"/api/v1/recordings/{rec.id}/").json()
    assert body["play_url"] is None and body["download_url"]
