"""
Audit log model with SHA-256 chained integrity hashing.
Every write, read of sensitive data, login, and configuration
change generates an immutable audit record.
"""
import json
from datetime import datetime, timezone

from app.extensions import db
from .mixins import TimestampMixin, AuditHashMixin


class AuditLog(AuditHashMixin, db.Model):
    __tablename__ = "audit_logs"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # Who
    user_id       = db.Column(db.Integer, db.ForeignKey("users.id",
                              ondelete="SET NULL"), nullable=True, index=True)
    username      = db.Column(db.String(64),  nullable=True)   # denormalised for deleted users
    user_role     = db.Column(db.String(32),  nullable=True)
    ip_address    = db.Column(db.String(45),  nullable=True)
    user_agent    = db.Column(db.String(512), nullable=True)

    # What
    action        = db.Column(db.String(64),  nullable=False)  # CREATE, READ, UPDATE, DELETE, LOGIN, etc.
    resource_type = db.Column(db.String(64),  nullable=True)   # user, did_pool, auto_attendant…
    resource_id   = db.Column(db.String(255), nullable=True)
    resource_name = db.Column(db.String(255), nullable=True)

    # Payload diff (before/after for UPDATE, full payload for CREATE)
    payload_before = db.Column(db.JSON, nullable=True)
    payload_after  = db.Column(db.JSON, nullable=True)

    # Result
    status        = db.Column(db.String(16), nullable=False, default="success")  # success | failure
    status_detail = db.Column(db.Text, nullable=True)

    # HTTP context
    http_method   = db.Column(db.String(10), nullable=True)
    http_path     = db.Column(db.String(512), nullable=True)
    http_status   = db.Column(db.Integer, nullable=True)

    # Timestamp (separate from TimestampMixin for immutability)
    timestamp     = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        index=True
    )

    # Relationships
    user = db.relationship("User", back_populates="audit_logs")

    @classmethod
    def write(
        cls,
        action: str,
        user_id: int | None         = None,
        username: str               = "system",
        user_role: str              = "",
        ip_address: str             = "",
        user_agent: str             = "",
        resource_type: str          = "",
        resource_id: str            = "",
        resource_name: str          = "",
        payload_before: dict | None = None,
        payload_after: dict | None  = None,
        status: str                 = "success",
        status_detail: str          = "",
        http_method: str            = "",
        http_path: str              = "",
        http_status: int | None     = None,
    ) -> "AuditLog":
        """
        Create and persist an audit log entry with chained hash.
        Safe to call from anywhere — catches its own exceptions.
        """
        try:
            # Get previous hash for chain integrity
            last = cls.query.order_by(cls.id.desc()).first()
            prev_hash = last.row_hash if last else ""

            entry = cls(
                user_id=user_id,
                username=username,
                user_role=user_role,
                ip_address=ip_address,
                user_agent=user_agent,
                action=action,
                resource_type=resource_type,
                resource_id=str(resource_id),
                resource_name=resource_name,
                payload_before=payload_before,
                payload_after=payload_after,
                status=status,
                status_detail=status_detail,
                http_method=http_method,
                http_path=http_path,
                http_status=http_status,
            )
            db.session.add(entry)
            db.session.flush()   # get id before hash
            entry.row_hash = entry.compute_hash(prev_hash)
            db.session.commit()
            return entry
        except Exception as exc:
            db.session.rollback()
            # Never let audit failures break the calling operation
            import logging
            logging.getLogger(__name__).error(f"AuditLog.write failed: {exc}")
            return None

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "timestamp":     self.timestamp.isoformat(),
            "username":      self.username,
            "user_role":     self.user_role,
            "ip_address":    self.ip_address,
            "action":        self.action,
            "resource_type": self.resource_type,
            "resource_id":   self.resource_id,
            "resource_name": self.resource_name,
            "status":        self.status,
            "http_method":   self.http_method,
            "http_path":     self.http_path,
        }

    def __repr__(self) -> str:
        return f"<AuditLog [{self.action}] {self.username} on {self.resource_type}>"
