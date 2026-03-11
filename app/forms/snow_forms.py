"""
WTForms for ServiceNow integration configuration and manual fulfillment.
"""
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, BooleanField,
    TextAreaField, SubmitField, IntegerField
)
from wtforms.validators import (
    DataRequired, Optional, URL, Length,
    NumberRange, ValidationError
)


class SNOWConfigForm(FlaskForm):
    """
    System Settings → ServiceNow Integration configuration.
    Saved to AppConfig KV store.
    """
    snow_instance = StringField(
        "ServiceNow Instance URL",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "https://yourcompany.service-now.com"}
    )
    snow_username = StringField(
        "API Username",
        validators=[DataRequired(), Length(max=128)],
        render_kw={"placeholder": "orbit_integration"}
    )
    snow_password = StringField(
        "API Password",
        validators=[Optional(), Length(max=256)],
        render_kw={"placeholder": "Leave blank to keep existing password", "type": "password"}
    )
    snow_catalog_item_id = StringField(
        "Catalog Item SysID",
        validators=[Optional(), Length(max=64)],
        render_kw={"placeholder": "Webex Calling provisioning catalog item SysID"}
    )
    snow_webhook_secret = StringField(
        "Webhook Shared Secret",
        validators=[Optional(), Length(max=256)],
        render_kw={
            "placeholder": "Leave blank to keep existing secret",
            "type": "password"
        }
    )
    default_did_pool_id = SelectField(
        "Default DID Pool",
        coerce=int,
        validators=[Optional()],
        choices=[]   # Populated at runtime from DIDPool table
    )
    snow_assignment_group = StringField(
        "Assignment Group (for escalation)",
        validators=[Optional(), Length(max=128)],
        render_kw={"placeholder": "e.g. IT-Telephony"}
    )
    snow_fulfilled_state = IntegerField(
        "Fulfilled State Code",
        validators=[Optional(), NumberRange(min=1, max=10)],
        default=3,
        render_kw={"placeholder": "3"}
    )
    snow_failed_state = IntegerField(
        "Failed/Rejected State Code",
        validators=[Optional(), NumberRange(min=1, max=10)],
        default=4,
        render_kw={"placeholder": "4"}
    )
    auto_fulfill = BooleanField(
        "Auto-fulfill incoming requests",
        default=True
    )
    send_welcome_email = BooleanField(
        "Send welcome email on fulfillment",
        default=True
    )
    send_did_email = BooleanField(
        "Send DID assignment email on fulfillment",
        default=True
    )

    def validate_snow_instance(self, field):
        v = field.data or ""
        if v and not v.startswith("https://"):
            raise ValidationError(
                "Instance URL must start with https://"
            )
        if v and not v.startswith("https://"):
            raise ValidationError("Must be a valid HTTPS URL.")


class ManualFulfillForm(FlaskForm):
    """
    Manually trigger or re-try fulfillment for a specific SNOW request.
    Shown on the request detail page.
    """
    snow_request_id = StringField(
        "SNOW Request ID",
        validators=[DataRequired(), Length(max=64)]
    )
    user_email = StringField(
        "User Email (Webex)",
        validators=[DataRequired(), Length(max=255)],
        render_kw={"placeholder": "user@company.com"}
    )
    did_pool_id = SelectField(
        "DID Pool",
        coerce=int,
        validators=[DataRequired()],
        choices=[]
    )
    notes = TextAreaField(
        "Notes",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2, "placeholder": "Reason for manual fulfillment…"}
    )
