import pytest
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from cameras.models import Camera

pytestmark = pytest.mark.django_db


def test_no_missing_migrations():
    call_command("makemigrations", "--check", "--dry-run", verbosity=0)


def test_main_entrance_seeded_by_migration():
    camera = Camera.objects.get(slug="main-entrance")
    assert camera.name == "Main Entrance"
    assert camera.camera_type == "android_ip"
    assert camera.stream_protocol == "rtsp"
    assert camera.enabled and camera.recording_root == "" and camera.stream_path == "entrance"


@pytest.mark.django_db(transaction=True)
def test_migrations_roll_back_and_forward():
    executor = MigrationExecutor(connection)
    executor.migrate([("recordings", None)])
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())
    executor = MigrationExecutor(connection)
    assert not executor.migration_plan(executor.loader.graph.leaf_nodes())
    assert Camera.objects.filter(slug="main-entrance").exists()


def test_migrate_safe_command_is_idempotent():
    call_command("migrate_safe", verbosity=0)
    call_command("migrate_safe", verbosity=0)
