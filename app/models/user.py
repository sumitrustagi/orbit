import enum
from datetime import datetime, timezone

from flask_login import UserMixin

from app.extensions import db
from .mixins import TimestampMixin, SoftDeleteMixin


class UserRole(str, enum.Enum):
    PLATFORM_ADMIN = "platform_admin"   # setup page + system config only
    GUI_ADMIN      = "gui_admin"        # full WxC CRUD, no setup page
    END_USER       = "end_user"         # self-service portal only


class AuthProvider(str, enum.Enum):
    LOCAL  = "local"
    LDAP   = "ldap"
    SAML   = "saml"
    OIDC   = "oidc"


class User(UserMixin, TimestampMixin, SoftDeleteMixin, db.Model):
    __tablename__ = "users"

    # ── Core identity ─────────────────────────────────────────────────────────
    id               = db.Column(db.Integer, primary_key=True, autoincrement=True)
    username         = db.Column(db.String(64),  unique=True, nullable=False, index=True)
    email            = db.Column(db.String(255), unique=True, nullable=False, index=True)
    first_name       = db.Column(db.String(64),  nullable=False, default="")
    last_name        = db.Column(db.String(64),  nullable=False, default="")
    display_name     = db.Column(db.String(128), nullable=True)

    # ── Auth ──────────────────────────────────────────────────────────────────
    password_hash    = db.Column(db.String(255), nullable=True)   # None for SSO-only users
    auth_provider    = db.Column(
        db.Enum(AuthProvider), nullable=False,
        default=AuthProvider.LOCAL
    )
    role             = db.Column(
        db.Enum(UserRole), nullable=False,
        default=UserRole.END_USER
    )
    is_active        = db.Column(db.Boolean, nullable=False, default=True)
    is_local         = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=False)

    # ── Profile ───────────────────────────────────────────────────────────────
    avatar_path      = db.Column(db.String(512), nullable=True)
    timezone         = db.Column(db.String(64),  nullable=True, default="UTC")
    language         = db.Column(db.String(10),  nullable=True, default="en")

    # ── Webex linkage ─────────────────────────────────────────────────────────
    webex_person_id  = db.Column(db.String(255), nullable=True, index=True)
    webex_extension  = db.Column(db.String(20),  nullable=True)
    webex_did        = db.Column(db.String(30),  nullable=True)
    webex_location_id = db.Column(db.String(255), nullable=True)

    # ── Password reset ────────────────────────────────────────────────────────
    reset_token      = db.Column(db.String(255), nullable=True)
    reset_token_expiry = db.Column(db.DateTime(timezone=True), nullable=True)

    # ── TOTP (2FA placeholder) ────────────────────────────────────────────────
    totp_secret      = db.Column(db.String(64),  nullable=True)
    totp_enabled     = db.Column(db.Boolean, nullable=False, default=False)

    # ── Login tracking ────────────────────────────────────────────────────────
    last_login_at    = db.Column(db.DateTime(timezone=True), nullable=True)
    last_login_ip    = db.Column(db.String(45),  nullable=True)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until     = db.Column(db.DateTime(timezone=True), nullable=True)

    # ── SSO ───────────────────────────────────────────────────────────────────
    sso_subject      = db.Column(db.String(512), nullable=True)   # SAML NameID / OIDC sub
    sso_provider     = db.Column(db.String(64),  nullable=True)

    # ── Relationships ─────────────────────────────────────────────────────────
    audit_logs       = db.relationship("AuditLog",           back_populates="user",
                                        lazy="dynamic", cascade="all, delete-orphan")
    forward_schedules = db.relationship("CallForwardSchedule", back_populates="user",
                                         lazy="dynamic", cascade="all, delete-orphan")

    # ── Flask-Login required ──────────────────────────────────────────────────
    def get_id(self) -> str:
        return str(self.id)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or self.username

    @property
    def is_platform_admin(self) -> bool:
        return self.role == UserRole.PLATFORM_ADMIN

    @property
    def is_gui_admin(self) -> bool:
        return self.role == UserRole.GUI_ADMIN

    @property
    def is_end_user(self) -> bool:
        return self.role == UserRole.END_USER

    @property
    def is_locked(self) -> bool:
        if self.locked_until:
            return datetime.now(timezone.utc) < self.locked_until
        return False

    def increment_failed_login(self) -> None:
        from datetime import timedelta
        self.failed_login_count += 1
        # Lock after 5 consecutive failures for 15 minutes
        if self.failed_login_count >= 5:
            self.locked_until = datetime.now(timezone.utc) + timedelta(minutes=15)

    def clear_failed_logins(self) -> None:
        self.failed_login_count = 0
        self.locked_until = None

    def update_last_login(self, ip: str) -> None:
        self.last_login_at = datetime.now(timezone.utc)
        self.last_login_ip = ip
        self.clear_failed_logins()

    def __repr__(self) -> str:
        return f"<User {self.username} [{self.role.value}]>"

    def to_dict(self) -> dict:
        return {
            "id":           self.id,
            "username":     self.username,
            "email":        self.email,
            "full_name":    self.full_name,
            "role":         self.role.value,
            "is_active":    self.is_active,
            "auth_provider": self.auth_provider.value,
            "webex_did":    self.webex_did,
            "webex_extension": self.webex_extension,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
            "created_at":   self.created_at.isoformat(),
        }
