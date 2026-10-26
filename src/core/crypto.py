"""Symmetric encryption for camera secrets stored in the database."""

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _fernet() -> Fernet:
    key = settings.CCTV_FIELD_ENCRYPTION_KEY
    if not key or key == "CHANGE_ME":
        raise ImproperlyConfigured("CCTV_FIELD_ENCRYPTION_KEY is not configured.")
    try:
        return Fernet(key.encode())
    except ValueError as exc:
        raise ImproperlyConfigured("CCTV_FIELD_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt(value: str) -> str:
    return _fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:
        raise ImproperlyConfigured("Stored secret cannot be decrypted with the current key.") from exc
