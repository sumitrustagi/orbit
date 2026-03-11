"""
Fernet-based field-level encryption.
Import encrypt/decrypt everywhere sensitive values are stored.
"""
import os
from cryptography.fernet import Fernet, InvalidToken


def _cipher() -> Fernet:
    key = os.environ.get("FERNET_KEY", "").encode()
    if not key:
        raise RuntimeError("FERNET_KEY is not set in environment.")
    return Fernet(key)


def encrypt(plain: str) -> str:
    """Encrypt a plaintext string. Returns base64 ciphertext string."""
    if not plain:
        return ""
    return _cipher().encrypt(plain.encode()).decode()


def decrypt(ciphertext: str) -> str:
    """Decrypt a Fernet ciphertext string. Returns plaintext."""
    if not ciphertext:
        return ""
    try:
        return _cipher().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""


def encrypt_field(value: str | None) -> str | None:
    """SQLAlchemy-safe: handles None gracefully."""
    if value is None:
        return None
    return encrypt(value)


def decrypt_field(value: str | None) -> str | None:
    if value is None:
        return None
    return decrypt(value)
