"""
WTForms for DID pool creation/editing and manual DID assignment.
"""
import re
from flask_wtf import FlaskForm
from wtforms import (
    StringField, TextAreaField, SelectField,
    BooleanField, SubmitField, HiddenField
)
from wtforms.validators import (
    DataRequired, Length, Optional, Regexp, ValidationError
)


E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def validate_e164(form, field):
    if field.data and not E164_RE.match(field.data.strip()):
        raise ValidationError(
            "Must be a valid E.164 number (e.g. +3222000100)."
        )


class DIDPoolForm(FlaskForm):
    name = StringField(
        "Pool Name",
        validators=[DataRequired(), Length(min=2, max=128)],
        render_kw={"placeholder": "e.g. Brussels HQ — Direct Lines"}
    )
    description = TextAreaField(
        "Description",
        validators=[Optional(), Length(max=512)],
        render_kw={
            "rows": 2,
            "placeholder": "Optional notes about this pool…"
        }
    )
    location_id = SelectField(
        "Webex Location",
        validators=[DataRequired()],
        choices=[]   # Populated at runtime from Webex API
    )
    range_start = StringField(
        "Range Start (E.164)",
        validators=[DataRequired(), validate_e164],
        render_kw={"placeholder": "+3222000100"}
    )
    range_end = StringField(
        "Range End (E.164)",
        validators=[DataRequired(), validate_e164],
        render_kw={"placeholder": "+3222000199"}
    )
    is_active = BooleanField("Pool Active", default=True)

    def validate_range_end(self, field):
        start = self.range_start.data or ""
        end   = field.data or ""
        if start and end:
            try:
                s = int(start.lstrip("+"))
                e = int(end.lstrip("+"))
                if e < s:
                    raise ValidationError(
                        "Range end must be greater than or equal to range start."
                    )
                if (e - s + 1) > 10_000:
                    raise ValidationError(
                        f"Range too large ({e - s + 1} numbers). Maximum is 10,000 per pool."
                    )
            except ValueError:
                pass   # E.164 validator will catch invalid formats


class ManualAssignForm(FlaskForm):
    """
    Manually assign a specific DID to a Webex entity.
    Used in the pool_detail assign modal.
    """
    number = HiddenField(
        "DID Number",
        validators=[DataRequired()]
    )
    assignment_type = SelectField(
        "Assign To",
        validators=[DataRequired()],
        choices=[
            ("user",              "User"),
            ("workspace",         "Workspace"),
            ("auto_attendant",    "Auto Attendant"),
            ("hunt_group",        "Hunt Group"),
            ("call_queue",        "Call Queue"),
            ("virtual_extension", "Virtual Extension"),
        ]
    )
    webex_entity_id = StringField(
        "Webex Entity ID",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "Webex person / entity ID or email"}
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2, "placeholder": "Optional notes…"}
    )


class BulkReleaseForm(FlaskForm):
    """Hidden form for confirming bulk release of all numbers in a pool."""
    pool_id  = HiddenField(validators=[DataRequired()])
    confirm  = HiddenField(default="yes")
