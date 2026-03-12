"""
CallForwardSchedule model.

A schedule defines:
  - Which Webex entity (user, workspace, hunt group, auto attendant) to forward
  - Which days of the week and time window to activate forwarding
  - The destination number to forward calls to
  - Whether it is currently active (applied to Webex) or reverted

On-demand forwarding (manual override) bypasses the schedule and
applies/reverts immediately, flagged with is_ondemand=True.

State transitions:
  INACTIVE → ACTIVE    (schedule tick or on-demand ON)
  ACTIVE   → INACTIVE  (schedule tick or on-demand OFF)
"""
import json
from datetime import datetime, timezone, time as dt_time
from enum import Enum as PyEnum

from sqlalchemy import (
    Integer, String, Text, Boolean, DateTime,
    Enum, JSON, ForeignKey, Time
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db


class ForwardType(PyEnum):
    ALWAYS          = "always"           # Forward all calls
    BUSY            = "busy"             # Forward when busy
    NO_ANSWER       = "no_answer"        # Forward when no answer
    SELECTIVE       = "selective"        # Forward based on schedule only


class EntityType(PyEnum):
    USER            = "user"
    WORKSPACE       = "workspace"
    HUNT_GROUP      = "hunt_group"
    AUTO_ATTENDANT  = "auto_attendant"
    CALL_QUEUE      = "call_queue"


class ScheduleStatus(PyEnum):
    INACTIVE        = "inactive"         # Not currently forwarding
    ACTIVE          = "active"           # Forwarding is live in Webex
    ERROR           = "error"            # Last apply/revert failed
    ONDEMAND        = "ondemand"         # On-demand override is active


# Day-of-week bitmask constants (ISO weekday: Mon=1 … Sun=7)
WEEKDAY_BITS = {
    "monday":    0b0000001,
    "tuesday":   0b0000010,
    "wednesday": 0b0000100,
    "thursday":  0b0001000,
    "friday":    0b0010000,
    "saturday":  0b0100000,
    "sunday":    0b1000000,
}


class CallForwardSchedule(db.Model):
    """
    A time-based (or on-demand) call forward rule for a Webex entity.
    """
    __tablename__ = "call_forward_schedules"

    id              : Mapped[int]                 = mapped_column(Integer, primary_key=True)

    # ── Entity being forwarded ─────────────────────────────────────────────
    entity_type     : Mapped[EntityType]          = mapped_column(
        Enum(EntityType), nullable=False
    )
    entity_id       : Mapped[str]                 = mapped_column(
        String(255), nullable=False, index=True
    )
    entity_name     : Mapped[str]                 = mapped_column(
        String(255), nullable=False
    )
    entity_email    : Mapped[str | None]          = mapped_column(String(255), nullable=True)

    # ── Forward configuration ──────────────────────────────────────────────
    forward_type    : Mapped[ForwardType]         = mapped_column(
        Enum(ForwardType), nullable=False, default=ForwardType.ALWAYS
    )
    destination     : Mapped[str]                 = mapped_column(
        String(64), nullable=False
    )                                              # E.164 or extension

    # ── Schedule window ────────────────────────────────────────────────────
    # Bitmask of active days (use WEEKDAY_BITS above)
    active_days     : Mapped[int]                 = mapped_column(
        Integer, nullable=False, default=0b0011111   # Mon–Fri
    )
    time_start      : Mapped[dt_time]             = mapped_column(
        Time, nullable=False, default=dt_time(18, 0)  # 18:00
    )
    time_end        : Mapped[dt_time]             = mapped_column(
        Time, nullable=False, default=dt_time(8, 0)   # 08:00 next day
    )
    timezone_name   : Mapped[str]                 = mapped_column(
        String(64), nullable=False, default="UTC"
    )

    # ── Flags ──────────────────────────────────────────────────────────────
    is_active       : Mapped[bool]                = mapped_column(
        Boolean, nullable=False, default=True
    )                                              # Schedule enabled/disabled globally
    is_ondemand     : Mapped[bool]                = mapped_column(
        Boolean, nullable=False, default=False
    )                                              # On-demand override currently active

    # ── Status ─────────────────────────────────────────────────────────────
    status          : Mapped[ScheduleStatus]      = mapped_column(
        Enum(ScheduleStatus), nullable=False,
        default=ScheduleStatus.INACTIVE, index=True
    )
    last_error      : Mapped[str | None]          = mapped_column(Text, nullable=True)

    # ── Metadata ───────────────────────────────────────────────────────────
    name            : Mapped[str]                 = mapped_column(
        String(255), nullable=False
    )
    description     : Mapped[str | None]          = mapped_column(Text, nullable=True)
    notes           : Mapped[str | None]          = mapped_column(Text, nullable=True)

    # ── Webex state tracking ───────────────────────────────────────────────
    # Snapshot of Webex's original forward settings — restored on revert
    original_settings : Mapped[dict | None]       = mapped_column(JSON, nullable=True)

    # ── Timestamps ─────────────────────────────────────────────────────────
    last_applied_at : Mapped[datetime | None]     = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_reverted_at: Mapped[datetime | None]     = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at      : Mapped[datetime]            = mapped_column(
        DateTime(timezone=True),
        nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at      : Mapped[datetime]            = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # ── Created by ─────────────────────────────────────────────────────────
    created_by_id   : Mapped[int | None]          = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_by      = relationship("User", foreign_keys=[created_by_id], lazy="select")

    # ── Bitmask helpers ────────────────────────────────────────────────────

    def is_day_active(self, iso_weekday: int) -> bool:
        """
        Return True if the given ISO weekday (1=Mon … 7=Sun)
        is set in the active_days bitmask.
        """
        day_names = [
            "monday","tuesday","wednesday",
            "thursday","friday","saturday","sunday"
        ]
        if iso_weekday < 1 or iso_weekday > 7:
            return False
        bit = WEEKDAY_BITS[day_names[iso_weekday - 1]]
        return bool(self.active_days & bit)

    @property
    def active_day_names(self) -> list[str]:
        """Return list of active day names for display."""
        return [
            name.title()
            for name, bit in WEEKDAY_BITS.items()
            if self.active_days & bit
        ]

    def set_days(self, day_name_list: list[str]) -> None:
        """Set active_days from a list of day name strings."""
        mask = 0
        for name in day_name_list:
            bit = WEEKDAY_BITS.get(name.lower())
            if bit:
                mask |= bit
        self.active_days = mask

    # ── Schedule window check ──────────────────────────────────────────────

    def is_in_window(self, now_local: datetime) -> bool:
        """
        Determine whether `now_local` falls within this schedule's
        active window.

        Handles overnight windows (e.g. 18:00 → 08:00 next day).
        """
        if not self.is_day_active(now_local.isoweekday()):
            # Also check previous day for overnight windows
            prev_iso = (now_local.isoweekday() - 2) % 7 + 1
            if not (self.time_start > self.time_end and
                    self.is_day_active(prev_iso)):
                return False

        current = now_local.time().replace(second=0, microsecond=0)
        s       = self.time_start
        e       = self.time_end

        if s <= e:
            # Same-day window (e.g. 09:00 → 17:00)
            return s <= current < e
        else:
            # Overnight window (e.g. 18:00 → 08:00)
            return current >= s or current < e

    # ── Serialisation ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id":               self.id,
            "name":             self.name,
            "entity_type":      self.entity_type.value,
            "entity_id":        self.entity_id,
            "entity_name":      self.entity_name,
            "entity_email":     self.entity_email,
            "forward_type":     self.forward_type.value,
            "destination":      self.destination,
            "active_days":      self.active_days,
            "active_day_names": self.active_day_names,
            "time_start":       self.time_start.strftime("%H:%M"),
            "time_end":         self.time_end.strftime("%H:%M"),
            "timezone_name":    self.timezone_name,
            "is_active":        self.is_active,
            "is_ondemand":      self.is_ondemand,
            "status":           self.status.value,
            "last_error":       self.last_error,
            "last_applied_at":  self.last_applied_at.isoformat()
                                if self.last_applied_at else None,
            "last_reverted_at": self.last_reverted_at.isoformat()
                                if self.last_reverted_at else None,
            "created_at":       self.created_at.isoformat(),
        }

    def __repr__(self) -> str:
        return (
            f"<CallForwardSchedule id={self.id} "
            f"name={self.name!r} "
            f"entity={self.entity_name!r} "
            f"status={self.status.value}>"
        )
