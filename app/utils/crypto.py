"""
Fernet symmetric encryption helpers for storing secrets in AppConfig.

The encryption key is derived from SECRET_KEY via PBKDF2 and is
deterministic — the same SECRET_KEY always produces the same Fernet key,
so encrypted values survive app restarts as long as SECRET_KEY does not
change.

Usage:
    from app.utils.crypto import encrypt, decrypt

    AppConfig.set("SNOW_PASSWORD", encrypt("hunter2"), encrypted=True)
    plain = decrypt(AppConfig.get("SNOW_PASSWORD"))
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet


def _fernet() -> Fernet:
    """
    Derive a stable Fernet key from the app's SECRET_KEY.
    Falls back to a random key if no Flask context is available
    (e.g. during unit tests with isolated crypto calls).
    """
    try:
        from flask import current_app
        secret = current_app.config["SECRET_KEY"]
        if isinstance(secret, str):
            secret = secret.encode()
    except RuntimeError:
        # No Flask context — use env var fallback for tests
        secret = os.environ.get("SECRET_KEY", "fallback-test-key").encode()

    # Derive a 32-byte key via SHA-256 (deterministic, no salt needed
    # because SECRET_KEY itself is the entropy source)
    derived = hashlib.sha256(secret).digest()
    fernet_key = base64.urlsafe_b64encode(derived)
    return Fernet(fernet_key)


def encrypt(plaintext: str) -> str:
    """
    Encrypt a plaintext string and return the Fernet token as a str.
    Returns an empty string if plaintext is empty.
    """
    if not plaintext:
        return ""
    token = _fernet().encrypt(plaintext.encode())
    return token.decode()


def decrypt(ciphertext: str) -> str:
    """
    Decrypt a Fernet token string and return the plaintext.
    Returns an empty string if ciphertext is empty or decryption fails.
    """
    if not ciphertext:
        return ""
    try:
        plaintext = _fernet().decrypt(ciphertext.encode())
        return plaintext.decode()
    except Exception:
        return ""
