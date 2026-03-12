"""
WTForms for admin user management.
"""
import re
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, BooleanField,
    PasswordField, TextAreaField, HiddenField, SubmitField
)
from wtforms.validators import (
    DataRequired, Email, Length, Optional,
    EqualTo, ValidationError
)

from app.models.user import UserRole

ROLE_CHOICES = [
    (UserRole.SUPERADMIN.value, "Super Admin — full access including user management"),
    (UserRole.ADMIN.value,      "Admin — full operational access, no user management"),
    (UserRole.READONLY.value,   "Read-Only — view dashboards and reports only"),
]

# Minimum password requirements
_PW_RE = re.compile(
    r"^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)(?=.*[^a-zA-Z0-9]).{10,}$"
)


def _validate_password_strength(form, field):
    """Shared password strength validator."""
    v = field.data or ""
    if v and not _PW_RE.match(v):
        raise ValidationError(
            "Password must be at least 10 characters and include "
            "uppercase, lowercase, a digit, and a special character."
        )


class CreateUserForm(FlaskForm):
    """Create a new admin user account."""

    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=3, max=64)],
        render_kw={"placeholder": "e.g. j.smith", "autocomplete": "off"}
    )
    email = StringField(
        "Email Address",
        validators=[DataRequired(), Email(), Length(max=255)],
        render_kw={"placeholder": "user@company.com"}
    )
    full_name = StringField(
        "Full Name",
        validators=[Optional(), Length(max=128)],
        render_kw={"placeholder": "Jane Smith"}
    )
    role = SelectField(
        "Role",
        choices=ROLE_CHOICES,
        default=UserRole.ADMIN.value,
        validators=[DataRequired()]
    )
    password = PasswordField(
        "Password",
        validators=[DataRequired(), Length(min=10, max=128), _validate_password_strength]
    )
    confirm_password = PasswordField(
        "Confirm Password",
        validators=[
            DataRequired(),
            EqualTo("password", message="Passwords must match.")
        ]
    )
    is_active = BooleanField("Account Active", default=True)
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2, "placeholder": "Optional internal notes…"}
    )

    def validate_username(self, field):
        from app.models.user import User
        if User.query.filter_by(username=field.data.strip().lower()).first():
            raise ValidationError("That username is already taken.")

    def validate_email(self, field):
        from app.models.user import User
        if User.query.filter_by(email=field.data.strip().lower()).first():
            raise ValidationError("That email address is already registered.")


class EditUserForm(FlaskForm):
    """Edit an existing admin user account."""

    username = StringField(
        "Username",
        validators=[DataRequired(), Length(min=3, max=64)],
        render_kw={"autocomplete": "off"}
    )
    email = StringField(
        "Email Address",
        validators=[DataRequired(), Email(), Length(max=255)]
    )
    full_name = StringField(
        "Full Name",
        validators=[Optional(), Length(max=128)]
    )
    role = SelectField(
        "Role",
        choices=ROLE_CHOICES,
        validators=[DataRequired()]
    )
    is_active = BooleanField("Account Active")
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2}
    )

    # Populated at instantiation to exclude the current user from uniqueness checks
    _user_id: int = 0

    def validate_username(self, field):
        from app.models.user import User
        existing = User.query.filter_by(
            username=field.data.strip().lower()
        ).first()
        if existing and existing.id != self._user_id:
            raise ValidationError("That username is already taken.")

    def validate_email(self, field):
        from app.models.user import User
        existing = User.query.filter_by(
            email=field.data.strip().lower()
        ).first()
        if existing and existing.id != self._user_id:
            raise ValidationError("That email address is already registered.")


class ChangePasswordForm(FlaskForm):
    """Admin-initiated password reset for another user (no current password needed)."""

    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=10, max=128), _validate_password_strength]
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[
            DataRequired(),
            EqualTo("new_password", message="Passwords must match.")
        ]
    )
    force_change = BooleanField(
        "Require user to change password on next login",
        default=True
    )


class SelfChangePasswordForm(FlaskForm):
    """Logged-in admin changing their own password."""

    current_password = PasswordField(
        "Current Password",
        validators=[DataRequired()]
    )
    new_password = PasswordField(
        "New Password",
        validators=[DataRequired(), Length(min=10, max=128), _validate_password_strength]
    )
    confirm_password = PasswordField(
        "Confirm New Password",
        validators=[
            DataRequired(),
            EqualTo("new_password", message="Passwords must match.")
        ]
    )
