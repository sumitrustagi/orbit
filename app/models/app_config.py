"""
Key-value store for all first-time setup settings.
All sensitive values are encrypted before storage.
"""
from app.extensions import db
from app.utils.crypto import encrypt_field, decrypt_field
from .mixins import TimestampMixin

# Keys that must be encrypted at rest
ENCRYPTED_KEYS = {
    "LDAP_BIND_PASSWORD", "SMTP_PASSWORD", "WEBEX_ACCESS_TOKEN",
    "OIDC_CLIENT_SECRET", "SAML_CERTIFICATE",
}


class AppConfig(TimestampMixin, db.Model):
    __tablename__ = "app_config"

    id    = db.Column(db.Integer, primary_key=True, autoincrement=True)
    key   = db.Column(db.String(128), unique=True, nullable=False, index=True)
    value = db.Column(db.Text, nullable=True)

    @classmethod
    def get(cls, key: str, default: str = "") -> str:
        """Retrieve and auto-decrypt a config value."""
        row = cls.query.filter_by(key=key).first()
        if row is None:
            return default
        val = row.value or ""
        if key in ENCRYPTED_KEYS:
            val = decrypt_field(val) or ""
        return val

    @classmethod
    def set(cls, key: str, value: str) -> None:
        """Upsert a config value, encrypting sensitive keys."""
        stored = encrypt_field(value) if key in ENCRYPTED_KEYS else value
        row = cls.query.filter_by(key=key).first()
        if row:
            row.value = stored
        else:
            row = cls(key=key, value=stored)
            db.session.add(row)
        db.session.commit()

    @classmethod
    def get_all(cls) -> dict:
        """Return all config as a dict (sensitive values decrypted)."""
        return {
            row.key: (decrypt_field(row.value) if row.key in ENCRYPTED_KEYS else row.value)
            for row in cls.query.all()
        }

    @classmethod
    def bulk_set(cls, data: dict) -> None:
        """Upsert multiple keys in a single transaction."""
        for key, value in data.items():
            stored = encrypt_field(str(value)) if key in ENCRYPTED_KEYS else str(value)
            row = cls.query.filter_by(key=key).first()
            if row:
                row.value = stored
            else:
                db.session.add(cls(key=key, value=stored))
        db.session.commit()

    def __repr__(self) -> str:
        safe = "***" if self.key in ENCRYPTED_KEYS else self.value
        return f"<AppConfig {self.key}={safe}>"
