"""
Call forwarding schedules for end users.
Each schedule triggers/reverts call forwarding via Webex API
at the defined times using the Celery Beat job.
"""
import enum
from app.extensions import db
from .mixins import TimestampMixin


class ForwardType(str, enum.Enum):
    ON_DEMAND  = "on_demand"    # Manual toggle (instant API call)
    SCHEDULED  = "scheduled"    # Time-based (APScheduler)


class CallForwardSchedule(TimestampMixin, db.Model):
    __tablename__ = "call_forward_schedules"

    id              = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id         = db.Column(db.Integer, db.ForeignKey("users.id",
                                ondelete="CASCADE"), nullable=False, index=True)

    name            = db.Column(db.String(128), nullable=False, default="My Schedule")
    forward_type    = db.Column(db.Enum(ForwardType), nullable=False,
                                default=ForwardType.SCHEDULED)

    # Destination
    forward_to      = db.Column(db.String(64), nullable=False)   # E.164 or extension

    # On-demand state
    is_active       = db.Column(db.Boolean, nullable=False, default=False)

    # Schedule (used for ForwardType.SCHEDULED)
    # Days: comma-separated 0=Mon…6=Sun  e.g. "0,1,2,3,4"
    days_of_week    = db.Column(db.String(20), nullable=True)
    start_time      = db.Column(db.Time, nullable=True)      # local time
    end_time        = db.Column(db.Time, nullable=True)
    timezone        = db.Column(db.String(64), nullable=True, default="UTC")
    schedule_enabled = db.Column(db.Boolean, nullable=False, default=True)

    # Webex sync
    webex_applied   = db.Column(db.Boolean, nullable=False, default=False)
    last_applied_at = db.Column(db.DateTime(timezone=True), nullable=True)

    # Relationship
    user = db.relationship("User", back_populates="forward_schedules")

    @property
    def days_list(self) -> list[int]:
        if not self.days_of_week:
            return []
        return [int(d) for d in self.days_of_week.split(",") if d.strip().isdigit()]

    DAY_NAMES = ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"]

    @property
    def days_display(self) -> str:
        return ", ".join(self.DAY_NAMES[d] for d in self.days_list)

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "name":           self.name,
            "forward_type":   self.forward_type.value,
            "forward_to":     self.forward_to,
            "is_active":      self.is_active,
            "days_of_week":   self.days_list,
            "start_time":     str(self.start_time) if self.start_time else None,
            "end_time":       str(self.end_time)   if self.end_time   else None,
            "timezone":       self.timezone,
            "schedule_enabled": self.schedule_enabled,
        }

    def __repr__(self) -> str:
        return f"<CallForwardSchedule user={self.user_id} to={self.forward_to}>"
