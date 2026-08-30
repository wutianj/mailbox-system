import hashlib
import secrets
import base64

from cryptography.fernet import Fernet

from .config import settings


def generate_token(byte_count: int = 24) -> str:
    return secrets.token_urlsafe(byte_count)


def generate_secret(byte_count: int = 18) -> str:
    return secrets.token_urlsafe(byte_count)


def hash_value(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def verify_value(value: str, expected_hash: str) -> bool:
    return secrets.compare_digest(hash_value(value), expected_hash)


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.app_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_value(value: str) -> str:
    return _fernet().decrypt(value.encode("ascii")).decode("utf-8")
