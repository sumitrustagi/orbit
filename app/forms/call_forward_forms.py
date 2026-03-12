"""
WTForms for call forward schedule management.
"""
import re
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, SelectMultipleField,
    TimeField, TextAreaField, BooleanField,
    HiddenField, SubmitField, widgets
)
from wtforms.validators import (
    DataRequired, Length, Optional, ValidationError
)

from app.models.call_forward import ForwardType, EntityType

E164_OR_EXT_RE = re.compile(r"^(\+[1-9]\d{6,14}|\d{2,10})$")

COMMON_TIMEZONES = [
    ("UTC",                  "UTC"),
    ("Europe/Brussels",      "Europe/Brussels (CET/CEST)"),
    ("Europe/London",        "Europe/London (GMT/BST)"),
    ("Europe/Amsterdam",     "Europe/Amsterdam"),
    ("Europe/Paris",         "Europe/Paris"),
    ("Europe/Berlin",        "Europe/Berlin"),
    ("Europe/Madrid",        "Europe/Madrid"),
    ("America/New_York",     "America/New_York (ET)"),
    ("America/Chicago",      "America/Chicago (CT)"),
    ("America/Denver",       "America/Denver (MT)"),
    ("America/Los_Angeles",  "America/Los_Angeles (PT)"),
    ("Asia/Dubai",           "Asia/Dubai (GST)"),
    ("Asia/Singapore",       "Asia/Singapore (SGT)"),
    ("Australia/Sydney",     "Australia/Sydney (AEST)"),
]

WEEKDAY_CHOICES = [
    ("monday",    "Mon"),
    ("tuesday",   "Tue"),
    ("wednesday", "Wed"),
    ("thursday",  "Thu"),
    ("friday",    "Fri"),
    ("saturday",  "Sat"),
    ("sunday",    "Sun"),
]

FORWARD_TYPE_CHOICES = [
    (ForwardType.ALWAYS.value,    "Always — forward all calls"),
    (ForwardType.BUSY.value,      "Busy — forward when line is busy"),
    (ForwardType.NO_ANSWER.value, "No Answer — forward when unanswered"),
    (ForwardType.SELECTIVE.value, "Selective — use Webex selective rules"),
]

ENTITY_TYPE_CHOICES = [
    (EntityType.USER.value,           "User"),
    (EntityType.WORKSPACE.value,      "Workspace"),
    (EntityType.HUNT_GROUP.value,     "Hunt Group"),
    (EntityType.AUTO_ATTENDANT.value, "Auto Attendant"),
    (EntityType.CALL_QUEUE.value,     "Call Queue"),
]


class CheckboxMultipleField(SelectMultipleField):
    """SelectMultipleField rendered as inline checkboxes."""
    widget        = widgets.ListWidget(prefix_label=False)
    option_widget = widgets.CheckboxInput()


class CallForwardScheduleForm(FlaskForm):
    """Create or edit a call forward schedule."""

    name = StringField(
        "Schedule Name",
        validators=[DataRequired(), Length(min=2, max=255)],
        render_kw={"placeholder": "e.g. After-Hours Redirect — Brussels"}
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2, "placeholder": "Optional notes…"}
    )

    # ── Target entity ──────────────────────────────────────────────────────
    entity_type = SelectField(
        "Entity Type",
        choices=ENTITY_TYPE_CHOICES,
        validators=[DataRequired()]
    )
    entity_id = StringField(
        "Webex Entity ID",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "Webex person / workspace / group ID or email"}
    )
    entity_name = StringField(
        "Display Name",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "Human-readable label"}
    )
    entity_email = StringField(
        "Entity Email",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "user@company.com (optional)"}
    )

    # ── Forward config ─────────────────────────────────────────────────────
    forward_type = SelectField(
        "Forward Mode",
        choices=FORWARD_TYPE_CHOICES,
        validators=[DataRequired()]
    )
    destination = StringField(
        "Forward Destination",
        validators=[DataRequired(), Length(max=64)],
        render_kw={"placeholder": "+3222000199 or extension"}
    )

    # ── Schedule window ────────────────────────────────────────────────────
    active_days = CheckboxMultipleField(
        "Active Days",
        choices=WEEKDAY_CHOICES,
        validators=[DataRequired(message="Select at least one day.")]
    )
    time_start = TimeField(
        "Forward From",
        format="%H:%M",
        validators=[DataRequired()],
    )
    time_end = TimeField(
        "Forward Until",
        format="%H:%M",
        validators=[DataRequired()],
    )
    timezone_name = SelectField(
        "Timezone",
        choices=COMMON_TIMEZONES,
        default="UTC",
        validators=[DataRequired()]
    )
    is_active = BooleanField(
        "Schedule Enabled",
        default=True
    )

    def validate_destination(self, field):
        v = (field.data or "").strip()
        if v and not E164_OR_EXT_RE.match(v):
            raise ValidationError(
                "Destination must be an E.164 number (+3222000199) or "
                "a numeric extension (2–10 digits)."
            )

    def validate_time_end(self, field):
        if self.time_start.data and field.data:
            if self.time_start.data == field.data:
                raise ValidationError(
                    "Forward Until time must differ from Forward From time."
                )


class OnDemandToggleForm(FlaskForm):
    """
    Minimal form for the on-demand forward toggle button.
    CSRF-protected POST — no additional fields needed.
    """
    schedule_id = HiddenField(validators=[DataRequired()])
    action      = HiddenField(validators=[DataRequired()])
    # action: "on" or "off"
