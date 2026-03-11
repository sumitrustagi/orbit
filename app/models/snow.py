"""
ServiceNow inbound request tracking.
Each catalog fulfillment creates one record, tracking the
full lifecycle from receipt → WxC provisioning → email sent.
"""
import enum
from app.extensions import db
from .mixins import TimestampMixin


class SNOWRequestStatus(str, enum.Enum):
    RECEIVED    = "received"
    PROCESSING  = "processing"
    COMPLETED   = "completed"
    FAILED      = "failed"
    DUPLICATE   = "duplicate"


class ServiceNowRequest(TimestampMixin, db.Model):
    __tablename__ = "snow_requests"

    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # ServiceNow identifiers
    snow_request_id  = db.Column(db.String(64), unique=True, nullable=False, index=True)
    snow_task_id     = db.Column(db.String(64), nullable=True)
    snow_catalog_id  = db.Column(db.String(128), nullable=True)

    # Requestor / subject user
    requester_email  = db.Column(db.String(255), nullable=True)
    user_email       = db.Column(db.String(255), nullable=False, index=True)
    user_first_name  = db.Column(db.String(64),  nullable=True)
    user_last_name   = db.Column(db.String(64),  nullable=True)

    # Requested provisioning details (raw payload stored for audit)
    raw_payload      = db.Column(db.JSON, nullable=True)

    # Resolved provisioning
    assigned_did     = db.Column(db.String(30),  nullable=True)
    assigned_ext     = db.Column(db.String(20),  nullable=True)
    location_id      = db.Column(db.String(255), nullable=True)
    calling_access   = db.Column(db.String(64),  nullable=True)   # e.g. national, international
    hunt_group_id    = db.Column(db.String(255), nullable=True)
    call_queue_id    = db.Column(db.String(255), nullable=True)
    webex_person_id  = db.Column(db.String(255), nullable=True)

    # Lifecycle
    status           = db.Column(db.Enum(SNOWRequestStatus), nullable=False,
                                  default=SNOWRequestStatus.RECEIVED, index=True)
    status_detail    = db.Column(db.Text, nullable=True)
    email_sent       = db.Column(db.Boolean, nullable=False, default=False)
    email_sent_at    = db.Column(db.DateTime(timezone=True), nullable=True)
    completed_at     = db.Column(db.DateTime(timezone=True), nullable=True)

    # Celery task tracking
    celery_task_id   = db.Column(db.String(255), nullable=True)

    def to_dict(self) -> dict:
        return {
            "id":             self.id,
            "snow_request_id": self.snow_request_id,
            "user_email":     self.user_email,
            "assigned_did":   self.assigned_did,
            "assigned_ext":   self.assigned_ext,
            "status":         self.status.value,
            "email_sent":     self.email_sent,
            "created_at":     self.created_at.isoformat(),
            "completed_at":   self.completed_at.isoformat() if self.completed_at else None,
        }

    def __repr__(self) -> str:
        return f"<SNOWRequest {self.snow_request_id} [{self.status.value}]>"
