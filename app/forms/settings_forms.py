"""
WTForms for System Settings tabs.

Each form maps to one settings tab. Values are read from / written
to AppConfig at runtime, not from environment variables, so changes
take effect immediately without a restart.
"""
from flask_wtf import FlaskForm
from wtforms import (
    StringField, IntegerField, BooleanField,
    SelectField, TextAreaField, PasswordField, SubmitField
)
from wtforms.validators import (
    DataRequired, Optional, Length, NumberRange,
    URL, ValidationError, Email
)


# ── General ───────────────────────────────────────────────────────────────────

class GeneralSettingsForm(FlaskForm):
    """App name, UI colours, session timeout, maintenance mode."""

    app_name = StringField(
        "Application Name",
        validators=[DataRequired(), Length(min=2, max=64)],
        render_kw={"placeholder": "Orbit"}
    )
    app_version = StringField(
        "Version Label",
        validators=[Optional(), Length(max=32)],
        render_kw={"placeholder": "1.0.0"}
    )
    primary_color = StringField(
        "Primary Colour (hex)",
        validators=[Optional(), Length(min=4, max=9)],
        render_kw={"type": "color", "style": "width:64px;height:38px;padding:2px;"}
    )
    accent_color = StringField(
        "Accent Colour (hex)",
        validators=[Optional(), Length(min=4, max=9)],
        render_kw={"type": "color", "style": "width:64px;height:38px;padding:2px;"}
    )
    session_timeout_minutes = IntegerField(
        "Session Idle Timeout (minutes)",
        validators=[Optional(), NumberRange(min=5, max=480)],
        default=30
    )
    maintenance_mode = BooleanField(
        "Enable Maintenance Mode",
        default=False
    )
    maintenance_message = TextAreaField(
        "Maintenance Message",
        validators=[Optional(), Length(max=512)],
        render_kw={"rows": 2,
                   "placeholder": "System is temporarily under maintenance…"}
    )
    items_per_page = IntegerField(
        "Table Rows per Page",
        validators=[Optional(), NumberRange(min=10, max=200)],
        default=25
    )


# ── Webex ─────────────────────────────────────────────────────────────────────

class WebexSettingsForm(FlaskForm):
    """Webex API credentials and organisation settings."""

    webex_access_token = PasswordField(
        "Webex Access Token",
        validators=[Optional(), Length(max=512)],
        render_kw={"placeholder": "Leave blank to keep existing token",
                   "autocomplete": "new-password"}
    )
    webex_org_id = StringField(
        "Webex Organisation ID",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Mx…"}
    )
    webex_calling_enabled = BooleanField(
        "Webex Calling Enabled",
        default=True
    )
    webex_cache_ttl = IntegerField(
        "Entity Cache TTL (seconds)",
        validators=[Optional(), NumberRange(min=60, max=3600)],
        default=300,
        render_kw={"placeholder": "300"}
    )
    webex_timeout = IntegerField(
        "API Request Timeout (seconds)",
        validators=[Optional(), NumberRange(min=5, max=60)],
        default=15
    )


# ── ServiceNow ────────────────────────────────────────────────────────────────

class SNOWSettingsForm(FlaskForm):
    """ServiceNow REST API integration settings."""

    snow_instance = StringField(
        "Instance URL",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "https://yourcompany.service-now.com"}
    )
    snow_username = StringField(
        "API Username",
        validators=[Optional(), Length(max=128)],
        render_kw={"placeholder": "orbit_integration"}
    )
    snow_password = PasswordField(
        "API Password",
        validators=[Optional(), Length(max=256)],
        render_kw={"placeholder": "Leave blank to keep existing password",
                   "autocomplete": "new-password"}
    )
    snow_webhook_secret = PasswordField(
        "Webhook Shared Secret",
        validators=[Optional(), Length(max=256)],
        render_kw={"placeholder": "Leave blank to keep existing secret",
                   "autocomplete": "new-password"}
    )
    snow_catalog_item_id = StringField(
        "Catalog Item SysID",
        validators=[Optional(), Length(max=64)],
        render_kw={"placeholder": "SysID of the Webex Calling catalog item"}
    )
    snow_assignment_group = StringField(
        "Assignment Group",
        validators=[Optional(), Length(max=128)],
        render_kw={"placeholder": "IT-Telephony"}
    )
    snow_fulfilled_state = IntegerField(
        "Fulfilled State Code",
        validators=[Optional(), NumberRange(min=1, max=10)],
        default=3
    )
    snow_failed_state = IntegerField(
        "Failed State Code",
        validators=[Optional(), NumberRange(min=1, max=10)],
        default=4
    )
    snow_auto_fulfill = BooleanField(
        "Auto-fulfill on webhook receipt",
        default=True
    )
    snow_send_welcome_email = BooleanField(
        "Send welcome email on fulfillment",
        default=True
    )
    snow_send_did_email = BooleanField(
        "Send DID assignment email on fulfillment",
        default=True
    )

    def validate_snow_instance(self, field):
        v = (field.data or "").strip()
        if v and not v.startswith("https://"):
            raise ValidationError("Instance URL must begin with https://")


# ── Email / SMTP ──────────────────────────────────────────────────────────────

class EmailSettingsForm(FlaskForm):
    """SMTP outbound email configuration."""

    smtp_host = StringField(
        "SMTP Host",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "smtp.office365.com"}
    )
    smtp_port = IntegerField(
        "SMTP Port",
        validators=[Optional(), NumberRange(min=1, max=65535)],
        default=587
    )
    smtp_use_tls = BooleanField("Use STARTTLS", default=True)
    smtp_use_ssl = BooleanField("Use SSL/TLS (port 465)", default=False)
    smtp_username = StringField(
        "SMTP Username",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "orbit@company.com"}
    )
    smtp_password = PasswordField(
        "SMTP Password",
        validators=[Optional(), Length(max=255)],
        render_kw={"placeholder": "Leave blank to keep existing password",
                   "autocomplete": "new-password"}
    )
    smtp_sender_name = StringField(
        "Sender Display Name",
        validators=[Optional(), Length(max=128)],
        render_kw={"placeholder": "Orbit Platform"}
    )
    smtp_sender_email = StringField(
        "Sender Email Address",
        validators=[Optional(), Email(), Length(max=255)],
        render_kw={"placeholder": "orbit@company.com"}
    )
    test_email_recipient = StringField(
        "Test Email Recipient",
        validators=[Optional(), Email(), Length(max=255)],
        render_kw={"placeholder": "admin@company.com"}
    )


# ── Security ──────────────────────────────────────────────────────────────────

class SecuritySettingsForm(FlaskForm):
    """Password policy and security hardening settings."""

    min_password_length = IntegerField(
        "Minimum Password Length",
        validators=[Optional(), NumberRange(min=8, max=64)],
        default=10
    )
    require_uppercase = BooleanField("Require Uppercase Letter", default=True)
    require_lowercase = BooleanField("Require Lowercase Letter", default=True)
    require_digit     = BooleanField("Require Digit", default=True)
    require_special   = BooleanField("Require Special Character", default=True)
    max_login_attempts = IntegerField(
        "Max Failed Login Attempts (before lockout)",
        validators=[Optional(), NumberRange(min=3, max=20)],
        default=5
    )
    lockout_duration_minutes = IntegerField(
        "Account Lockout Duration (minutes)",
        validators=[Optional(), NumberRange(min=1, max=1440)],
        default=15
    )
    session_timeout_minutes = IntegerField(
        "Session Idle Timeout (minutes)",
        validators=[Optional(), NumberRange(min=5, max=480)],
        default=30
    )
    force_https = BooleanField(
        "Force HTTPS / Secure Cookies",
        default=True
    )
    audit_retention_days = IntegerField(
        "Audit Log Retention (days)",
        validators=[Optional(), NumberRange(min=30, max=3650)],
        default=365,
        render_kw={"placeholder": "365"}
    )
    allow_api_tokens = BooleanField(
        "Allow API Token Authentication",
        default=True
    )
