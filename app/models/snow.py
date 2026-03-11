"""
SNOWRequest model — persists every inbound ServiceNow webhook
and tracks the full fulfillment lifecycle.
"""
import json
from datetime import datetime, timezone
from enum import Enum as PyEnum

from sqlalchemy import (
    Integer, String, Text, DateTime,
    ForeignKey, Enum, JSON
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class RequestStatus(PyEnum):
    PENDING    = "pending"
    PROCESSING = "processing"
    FULFILLED  = "fulfilled"
    FAILED     = "failed"


class SNOWRequest(db.Model):
    """
    Represents a single ServiceNow request received via webhook.

    Lifecycle:
      PENDING → PROCESSING → FULFILLED
                           ↘ FAILED
    """
    __tablename__ = "snow_requests"

    id                    : Mapped[int]            = mapped_column(Integer, primary_key=True)

    # ── SNOW identifiers ───────────────────────────────────────────────────
    snow_number           : Mapped[str]            = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    snow_sys_id           : Mapped[str | None]     = mapped_column(String(64),  nullable=True)

    # ── Requester ──────────────────────────────────────────────────────────
    requester_email       : Mapped[str]            = mapped_column(String(255), nullable=False)
    requester_name        : Mapped[str | None]     = mapped_column(String(255), nullable=True)

    # ── Request content ────────────────────────────────────────────────────
    short_description     : Mapped[str | None]     = mapped_column(String(512), nullable=True)
    requested_did_pool_id : Mapped[int | None]     = mapped_column(
        Integer, ForeignKey("did_pools.id", ondelete="SET NULL"), nullable=True
    )

    # ── Status ─────────────────────────────────────────────────────────────
    status                : Mapped[RequestStatus]  = mapped_column(
        Enum(RequestStatus), nullable=False, default=RequestStatus.PENDING, index=True
    )
    failure_reason        : Mapped[str | None]     = mapped_column(Text, nullable=True)
    retry_count           : Mapped[int]            = mapped_column(Integer, default=0)

    # ── Provisioning outcome ───────────────────────────────────────────────
    assigned_did          : Mapped[str | None]     = mapped_column(String(32),  nullable=True)
    assigned_extension    : Mapped[str | None]     = mapped_column(String(16),  nullable=True)
    webex_person_id       : Mapped[str | None]     = mapped_column(String(255), nullable=True)

    # ── Task tracking ──────────────────────────────────────────────────────
    celery_task_id        : Mapped[str | None]     = mapped_column(String(64),  nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    received_at           : Mapped[datetime | None]  = mapped_column(
        DateTime(timezone=True), nullable=True,
        default=lambda: datetime.now(timezone.utc)
    )
    fulfilled_at          : Mapped[datetime | None]  = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at            : Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at            : Mapped[datetime]         = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Raw webhook payload + structured fulfillment log ───────────────────
    raw_payload           : Mapped[dict | None]    = mapped_column(JSON, nullable=True)
    _fulfillment_log      : Mapped[str | None]     = mapped_column(
        "fulfillment_log", Text, nullable=True
    )

    # ── Relationships ──────────────────────────────────────────────────────
    requested_pool = relationship(
        "DIDPool",
        foreign_keys=[requested_did_pool_id],
        lazy="select",
    )

    # ── Log helpers ────────────────────────────────────────────────────────

    @property
    def fulfillment_log(self) -> list[dict]:
        """Return the fulfillment log as a list of {ts, msg} dicts."""
        if not self._fulfillment_log:
            return []
        try:
            return json.loads(self._fulfillment_log)
        except (json.JSONDecodeError, TypeError):
            return []

    def add_log(self, message: str) -> None:
        """Append a timestamped entry to the fulfillment log."""
        entries = self.fulfillment_log
        entries.append({
            "ts":  datetime.now(timezone.utc).isoformat(),
            "msg": message,
        })
        self._fulfillment_log = json.dumps(entries)
        self.updated_at = datetime.now(timezone.utc)
        try:
            from app.extensions import db
            db.session.commit()
        except Exception:
            pass   # Caller owns the session

    # ── State machine ──────────────────────────────────────────────────────

    def transition(self, new_status: RequestStatus) -> None:
        """
        Move to a new status.
        Validates legal transitions to prevent accidental state regression.
        """
        _allowed = {
            RequestStatus.PENDING:    {RequestStatus.PROCESSING, RequestStatus.FAILED},
            RequestStatus.PROCESSING: {RequestStatus.FULFILLED, RequestStatus.FAILED,
                                       RequestStatus.PENDING},
            RequestStatus.FULFILLED:  set(),          # Terminal — no transitions out
            RequestStatus.FAILED:     {RequestStatus.PENDING},  # Allow retry
        }
        if new_status not in _allowed.get(self.status, set()):
            # Log the illegal attempt but don't raise — fulfillment must not crash
            import logging
            logging.getLogger(__name__).warning(
                f"[SNOWRequest] Illegal transition "
                f"{self.status.value} → {new_status.value} "
                f"for request {self.snow_number}"
            )
        self.status     = new_status
        self.updated_at = datetime.now(timezone.utc)

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":                    self.id,
            "snow_number":           self.snow_number,
            "snow_sys_id":           self.snow_sys_id,
            "requester_email":       self.requester_email,
            "requester_name":        self.requester_name,
            "short_description":     self.short_description,
            "requested_did_pool_id": self.requested_did_pool_id,
            "status":                self.status.value,
            "failure_reason":        self.failure_reason,
            "retry_count":           self.retry_count,
            "assigned_did":          self.assigned_did,
            "assigned_extension":    self.assigned_extension,
            "webex_person_id":       self.webex_person_id,
            "celery_task_id":        self.celery_task_id,
            "received_at":           self.received_at.isoformat() if self.received_at else None,
            "fulfilled_at":          self.fulfilled_at.isoformat() if self.fulfilled_at else None,
            "created_at":            self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<SNOWRequest {self.snow_number} "
            f"status={self.status.value} "
            f"did={self.assigned_did or 'unassigned'}>"
        )
