import enum
from app.extensions import db
from .mixins import TimestampMixin


class DIDStatus(str, enum.Enum):
    AVAILABLE  = "available"
    ASSIGNED   = "assigned"
    RESERVED   = "reserved"
    PORTING    = "porting"


class DIDPool(TimestampMixin, db.Model):
    """
    A block of DIDs defined by an admin for a specific WxC location.
    e.g.  +3222000100 → +3222000199 for Brussels-HQ
    """
    __tablename__ = "did_pools"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name          = db.Column(db.String(128), nullable=False)
    description   = db.Column(db.Text, nullable=True)

    # Webex location linkage
    location_id   = db.Column(db.String(255), nullable=False, index=True)
    location_name = db.Column(db.String(255), nullable=True)

    # Range boundaries (E.164 format, e.g. +3222000100)
    range_start   = db.Column(db.String(30), nullable=False)
    range_end     = db.Column(db.String(30), nullable=False)

    # Sync metadata
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_active      = db.Column(db.Boolean, nullable=False, default=True)

    # Relationships
    numbers = db.relationship(
        "DIDAssignment",
        back_populates="pool",
        lazy="dynamic",
        cascade="all, delete-orphan"
    )

    @property
    def total_count(self) -> int:
        return self.numbers.count()

    @property
    def available_count(self) -> int:
        return self.numbers.filter_by(status=DIDStatus.AVAILABLE).count()

    @property
    def assigned_count(self) -> int:
        return self.numbers.filter_by(status=DIDStatus.ASSIGNED).count()

    @property
    def available_numbers(self) -> list:
        return self.numbers.filter_by(status=DIDStatus.AVAILABLE).all()

    def to_dict(self) -> dict:
        return {
            "id":            self.id,
            "name":          self.name,
            "location_id":   self.location_id,
            "location_name": self.location_name,
            "range_start":   self.range_start,
            "range_end":     self.range_end,
            "total":         self.total_count,
            "available":     self.available_count,
            "assigned":      self.assigned_count,
            "last_synced":   self.last_synced_at.isoformat() if self.last_synced_at else None,
        }

    def __repr__(self) -> str:
        return f"<DIDPool {self.name} [{self.range_start}–{self.range_end}]>"


class AssignmentType(str, enum.Enum):
    USER             = "user"
    WORKSPACE        = "workspace"
    AUTO_ATTENDANT   = "auto_attendant"
    HUNT_GROUP       = "hunt_group"
    CALL_QUEUE       = "call_queue"
    VIRTUAL_EXTENSION = "virtual_extension"
    SINGLE_NUMBER_REACH = "single_number_reach"
    UNASSIGNED       = "unassigned"


class DIDAssignment(TimestampMixin, db.Model):
    """
    Individual DID number within a pool with its current assignment state.
    """
    __tablename__ = "did_assignments"

    id            = db.Column(db.Integer, primary_key=True, autoincrement=True)
    pool_id       = db.Column(db.Integer, db.ForeignKey("did_pools.id",
                              ondelete="CASCADE"), nullable=False, index=True)
    number        = db.Column(db.String(30), unique=True, nullable=False, index=True)
    status        = db.Column(db.Enum(DIDStatus), nullable=False,
                              default=DIDStatus.AVAILABLE)

    # What / who this number is assigned to
    assignment_type     = db.Column(db.Enum(AssignmentType), nullable=True,
                                     default=AssignmentType.UNASSIGNED)
    assigned_to_id      = db.Column(db.String(255), nullable=True)    # Webex entity ID
    assigned_to_name    = db.Column(db.String(255), nullable=True)
    assigned_to_email   = db.Column(db.String(255), nullable=True)
    assigned_at         = db.Column(db.DateTime(timezone=True), nullable=True)

    # Webex sync
    webex_number_id     = db.Column(db.String(255), nullable=True)
    notes               = db.Column(db.Text, nullable=True)

    pool = db.relationship("DIDPool", back_populates="numbers")

    def assign_to(self, entity_id: str, entity_name: str,
                  entity_email: str, assignment_type: AssignmentType) -> None:
        from datetime import datetime, timezone
        self.status          = DIDStatus.ASSIGNED
        self.assignment_type = assignment_type
        self.assigned_to_id   = entity_id
        self.assigned_to_name = entity_name
        self.assigned_to_email = entity_email
        self.assigned_at      = datetime.now(timezone.utc)

    def release(self) -> None:
        self.status          = DIDStatus.AVAILABLE
        self.assignment_type = AssignmentType.UNASSIGNED
        self.assigned_to_id   = None
        self.assigned_to_name = None
        self.assigned_to_email = None
        self.assigned_at      = None

    def to_dict(self) -> dict:
        return {
            "id":              self.id,
            "number":          self.number,
            "status":          self.status.value,
            "assignment_type": self.assignment_type.value if self.assignment_type else None,
            "assigned_to":     self.assigned_to_name,
            "assigned_email":  self.assigned_to_email,
            "assigned_at":     self.assigned_at.isoformat() if self.assigned_at else None,
        }

    def __repr__(self) -> str:
        return f"<DID {self.number} [{self.status.value}]>"
