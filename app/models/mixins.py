"""
Reusable SQLAlchemy mixins shared across all models.
"""
import hashlib
import json
from datetime import datetime, timezone

from app.extensions import db


class TimestampMixin:
    """Adds created_at and updated_at to any model."""
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False
    )


class SoftDeleteMixin:
    """Adds soft-delete support (deleted_at timestamp)."""
    deleted_at = db.Column(db.DateTime(timezone=True), nullable=True, default=None)

    def soft_delete(self):
        self.deleted_at = datetime.now(timezone.utc)

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None


class AuditHashMixin:
    """
    Adds a SHA-256 chain hash per row to detect tampering.
    Each row's hash includes the previous row's hash (blockchain-style).
    """
    row_hash = db.Column(db.String(64), nullable=True)

    def compute_hash(self, prev_hash: str = "") -> str:
        payload = json.dumps({
            "id":         getattr(self, "id", ""),
            "timestamp":  str(getattr(self, "timestamp", "")),
            "action":     getattr(self, "action", ""),
            "user_id":    str(getattr(self, "user_id", "")),
            "resource":   getattr(self, "resource_type", ""),
            "prev":       prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()
