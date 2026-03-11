"""
WTForms filter form for the audit log list view.
All fields are optional — any combination can be applied simultaneously.
"""
from flask_wtf import FlaskForm
from wtforms import (
    StringField, SelectField, DateField,
    IntegerField, SubmitField
)
from wtforms.validators import Optional, Length


ACTION_CHOICES = [
    ("", "All Actions"),
    ("LOGIN",                    "Login"),
    ("LOGIN_SSO",                "SSO Login"),
    ("LOGIN_FAILED",             "Failed Login"),
    ("LOGOUT",                   "Logout"),
    ("PASSWORD_RESET_REQUESTED", "Password Reset Requested"),
    ("PASSWORD_RESET_COMPLETED", "Password Reset Completed"),
    ("CREATE",                   "Create"),
    ("UPDATE",                   "Update"),
    ("DELETE",                   "Delete"),
    ("READ",                     "Read (sensitive)"),
    ("CALL_FORWARD_APPLIED",     "Call Forward Applied"),
    ("CALL_FORWARD_REVERTED",    "Call Forward Reverted"),
    ("CALL_FORWARD_ONDEMAND_ON", "On-Demand Forward ON"),
    ("CALL_FORWARD_ONDEMAND_OFF","On-Demand Forward OFF"),
    ("DID_ASSIGNED",             "DID Assigned"),
    ("DID_RELEASED",             "DID Released"),
    ("SNOW_REQUEST_RECEIVED",    "SNOW Request Received"),
    ("SNOW_REQUEST_COMPLETED",   "SNOW Request Completed"),
    ("SNOW_REQUEST_FAILED",      "SNOW Request Failed"),
    ("AUDIT_PURGE",              "Audit Purge"),
    ("CONFIG_UPDATED",           "Config Updated"),
]

RESOURCE_CHOICES = [
    ("",                    "All Resources"),
    ("user",                "User"),
    ("did_pool",            "DID Pool"),
    ("did_assignment",      "DID Assignment"),
    ("call_forward_schedule","Call Forward"),
    ("snow_request",        "ServiceNow Request"),
    ("auto_attendant",      "Auto Attendant"),
    ("hunt_group",          "Hunt Group"),
    ("call_queue",          "Call Queue"),
    ("app_config",          "App Config"),
    ("audit_log",           "Audit Log"),
]

STATUS_CHOICES = [
    ("",        "All Statuses"),
    ("success", "Success"),
    ("failure", "Failure"),
]

PER_PAGE_CHOICES = [
    (25,  "25 per page"),
    (50,  "50 per page"),
    (100, "100 per page"),
    (250, "250 per page"),
]


class AuditFilterForm(FlaskForm):
    """Audit log filter sidebar form."""

    class Meta:
        # Disable CSRF — GET form, no state mutation
        csrf = False

    search      = StringField(
        "Search",
        validators=[Optional(), Length(max=200)],
        render_kw={"placeholder": "username, IP, resource name…"}
    )
    action      = SelectField(
        "Action",
        choices=ACTION_CHOICES,
        validators=[Optional()]
    )
    resource    = SelectField(
        "Resource Type",
        choices=RESOURCE_CHOICES,
        validators=[Optional()]
    )
    status      = SelectField(
        "Status",
        choices=STATUS_CHOICES,
        validators=[Optional()]
    )
    username    = StringField(
        "Username",
        validators=[Optional(), Length(max=64)],
        render_kw={"placeholder": "exact or partial"}
    )
    ip_address  = StringField(
        "IP Address",
        validators=[Optional(), Length(max=45)],
        render_kw={"placeholder": "e.g. 10.0.0.1"}
    )
    date_from   = DateField(
        "From Date",
        validators=[Optional()],
        format="%Y-%m-%d"
    )
    date_to     = DateField(
        "To Date",
        validators=[Optional()],
        format="%Y-%m-%d"
    )
    per_page    = SelectField(
        "Per Page",
        choices=PER_PAGE_CHOICES,
        coerce=int,
        default=50,
        validators=[Optional()]
    )
