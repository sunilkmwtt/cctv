"""Seed the initial camera. Its recordings live directly in the recordings mount root."""

from django.db import migrations

MAIN_ENTRANCE = {
    "slug": "main-entrance",
    "name": "Main Entrance",
    "location": "Main entrance",
    "camera_type": "android_ip",
    "stream_protocol": "rtsp",
    "stream_path": "entrance",
    "recording_root": "",
    "enabled": True,
}


def create_main_entrance(apps, schema_editor):
    Camera = apps.get_model("cameras", "Camera")
    defaults = {k: v for k, v in MAIN_ENTRANCE.items() if k != "slug"}
    Camera.objects.get_or_create(slug=MAIN_ENTRANCE["slug"], defaults=defaults)


class Migration(migrations.Migration):
    dependencies = [("cameras", "0001_initial")]

    # Reverse is a no-op: never delete a camera (and its recording index) automatically.
    operations = [migrations.RunPython(create_main_entrance, migrations.RunPython.noop)]
