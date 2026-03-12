"""
System Settings Blueprint.

Routes:
  GET  /admin/settings/                → Redirect to General tab
  GET  /admin/settings/general         → General settings
  POST /admin/settings/general         → Save general settings
  GET  /admin/settings/webex           → Webex API settings
  POST /admin/settings/webex           → Save Webex settings
  GET  /admin/settings/snow            → SNOW settings
  POST /admin/settings/snow            → Save SNOW settings
  GET  /admin/settings/email           → SMTP settings
  POST /admin/settings/email           → Save SMTP settings
  GET  /admin/settings/security        → Security policy settings
  POST /admin/settings/security        → Save security settings
  POST /admin/settings/test-webex      → AJAX: test Webex connection
  POST /admin/settings/test-snow       → AJAX: test SNOW connection
  POST /admin/settings/test-email      → AJAX: send test email
  GET  /admin/settings/env             → Read-only environment variable viewer
"""
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, jsonify, current_app
)
from flask_login import login_required

from app.utils.decorators import superadmin_required, _get_ip
from app.models.app_config import AppConfig
from app.models.audit import AuditLog
from app.forms.settings_forms import (
    GeneralSettingsForm, WebexSettingsForm,
    SNOWSettingsForm, EmailSettingsForm, SecuritySettingsForm
)
from app.utils.crypto import encrypt, decrypt
from flask_login import current_user

settings_bp = Blueprint(
    "settings", __name__,
    template_folder="../templates/settings",
    url_prefix="/admin/settings"
)

# ── Tabs metadata (used to render the shared nav) ────────────────────────────
TABS = [
    ("general",  "bi-sliders",           "General"),
    ("webex",    "bi-cloud-check",        "Webex API"),
    ("snow",     "bi-ticket",            "ServiceNow"),
    ("email",    "bi-envelope",          "Email / SMTP"),
    ("security", "bi-shield-lock",       "Security"),
    ("env",      "bi-terminal",          "Environment"),
]


def _write_audit(section: str, before: dict, after: dict):
    AuditLog.write(
        action="SETTINGS_UPDATE",
        user_id=current_user.id,
        username=current_user.username,
        user_role=current_user.role.value,
        ip_address=_get_ip(),
        resource_type="app_config",
        resource_name=f"settings.{section}",
        payload_before=before,
        payload_after=after,
        status="success",
    )


# ── Root redirect ─────────────────────────────────────────────────────────────

@settings_bp.route("/")
@login_required
@superadmin_required
def index():
    return redirect(url_for("settings.general"))


# ═════════════════════════════════════════════════════════════════════════════
# GENERAL
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/general", methods=["GET", "POST"])
@login_required
@superadmin_required
def general():
    form = GeneralSettingsForm()

    if request.method == "GET":
        form.app_name.data                = AppConfig.get("APP_NAME",    "Orbit")
        form.app_version.data             = AppConfig.get("APP_VERSION", "1.0.0")
        form.primary_color.data           = AppConfig.get("PRIMARY_COLOR",  "#1E40AF")
        form.accent_color.data            = AppConfig.get("ACCENT_COLOR",   "#3B82F6")
        form.session_timeout_minutes.data = int(AppConfig.get("SESSION_TIMEOUT_MINUTES", "30"))
        form.maintenance_mode.data        = AppConfig.get("MAINTENANCE_MODE", "false") == "true"
        form.maintenance_message.data     = AppConfig.get("MAINTENANCE_MESSAGE", "")
        form.items_per_page.data          = int(AppConfig.get("ITEMS_PER_PAGE", "25"))

    if form.validate_on_submit():
        before = AppConfig.get_all()

        AppConfig.set("APP_NAME",                 form.app_name.data.strip())
        AppConfig.set("APP_VERSION",              (form.app_version.data or "").strip())
        AppConfig.set("PRIMARY_COLOR",            form.primary_color.data or "#1E40AF")
        AppConfig.set("ACCENT_COLOR",             form.accent_color.data  or "#3B82F6")
        AppConfig.set("SESSION_TIMEOUT_MINUTES",  str(form.session_timeout_minutes.data or 30))
        AppConfig.set("MAINTENANCE_MODE",         "true" if form.maintenance_mode.data else "false")
        AppConfig.set("MAINTENANCE_MESSAGE",      (form.maintenance_message.data or "").strip())
        AppConfig.set("ITEMS_PER_PAGE",           str(form.items_per_page.data or 25))

        _write_audit("general", before, AppConfig.get_all())
        flash("General settings saved.", "success")
        return redirect(url_for("settings.general"))

    return render_template(
        "general.html", form=form,
        active_tab="general", tabs=TABS
    )


# ═════════════════════════════════════════════════════════════════════════════
# WEBEX
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/webex", methods=["GET", "POST"])
@login_required
@superadmin_required
def webex():
    form = WebexSettingsForm()

    if request.method == "GET":
        form.webex_org_id.data         = AppConfig.get("WEBEX_ORG_ID", "")
        form.webex_calling_enabled.data= AppConfig.get("WEBEX_CALLING_ENABLED","true") == "true"
        form.webex_cache_ttl.data      = int(AppConfig.get("WEBEX_CACHE_TTL", "300"))
        form.webex_timeout.data        = int(AppConfig.get("WEBEX_TIMEOUT", "15"))
        # Token is never shown — placeholder only

    if form.validate_on_submit():
        before = {
            "WEBEX_ORG_ID":           AppConfig.get("WEBEX_ORG_ID"),
            "WEBEX_CALLING_ENABLED":  AppConfig.get("WEBEX_CALLING_ENABLED"),
            "WEBEX_CACHE_TTL":        AppConfig.get("WEBEX_CACHE_TTL"),
        }

        if form.webex_access_token.data:
            AppConfig.set(
                "WEBEX_ACCESS_TOKEN",
                encrypt(form.webex_access_token.data.strip()),
                encrypted=True,
                description="Webex API access token (encrypted)"
            )
        AppConfig.set("WEBEX_ORG_ID",          (form.webex_org_id.data or "").strip())
        AppConfig.set("WEBEX_CALLING_ENABLED",
                      "true" if form.webex_calling_enabled.data else "false")
        AppConfig.set("WEBEX_CACHE_TTL",       str(form.webex_cache_ttl.data or 300))
        AppConfig.set("WEBEX_TIMEOUT",         str(form.webex_timeout.data or 15))

        _write_audit("webex", before, {"WEBEX_ORG_ID": AppConfig.get("WEBEX_ORG_ID")})
        flash("Webex settings saved.", "success")
        return redirect(url_for("settings.webex"))

    token_set = bool(AppConfig.get("WEBEX_ACCESS_TOKEN"))
    return render_template(
        "webex.html", form=form,
        active_tab="webex", tabs=TABS, token_set=token_set
    )


# ═════════════════════════════════════════════════════════════════════════════
# SERVICENOW
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/snow", methods=["GET", "POST"])
@login_required
@superadmin_required
def snow():
    form = SNOWSettingsForm()

    if request.method == "GET":
        form.snow_instance.data          = AppConfig.get("SNOW_INSTANCE", "")
        form.snow_username.data          = AppConfig.get("SNOW_USERNAME", "")
        form.snow_catalog_item_id.data   = AppConfig.get("SNOW_CATALOG_ITEM_ID", "")
        form.snow_assignment_group.data  = AppConfig.get("SNOW_ASSIGNMENT_GROUP", "")
        form.snow_fulfilled_state.data   = int(AppConfig.get("SNOW_FULFILLED_STATE", "3"))
        form.snow_failed_state.data      = int(AppConfig.get("SNOW_FAILED_STATE", "4"))
        form.snow_auto_fulfill.data      = AppConfig.get("SNOW_AUTO_FULFILL","true") == "true"
        form.snow_send_welcome_email.data= AppConfig.get("SNOW_SEND_WELCOME_EMAIL","true") == "true"
        form.snow_send_did_email.data    = AppConfig.get("SNOW_SEND_DID_EMAIL","true") == "true"

    if form.validate_on_submit():
        before = {
            "SNOW_INSTANCE":  AppConfig.get("SNOW_INSTANCE"),
            "SNOW_USERNAME":  AppConfig.get("SNOW_USERNAME"),
        }

        AppConfig.set("SNOW_INSTANCE",          (form.snow_instance.data or "").strip().rstrip("/"))
        AppConfig.set("SNOW_USERNAME",          (form.snow_username.data or "").strip())
        AppConfig.set("SNOW_CATALOG_ITEM_ID",   (form.snow_catalog_item_id.data or "").strip())
        AppConfig.set("SNOW_ASSIGNMENT_GROUP",  (form.snow_assignment_group.data or "").strip())
        AppConfig.set("SNOW_FULFILLED_STATE",   str(form.snow_fulfilled_state.data or 3))
        AppConfig.set("SNOW_FAILED_STATE",      str(form.snow_failed_state.data or 4))
        AppConfig.set("SNOW_AUTO_FULFILL",      "true" if form.snow_auto_fulfill.data else "false")
        AppConfig.set("SNOW_SEND_WELCOME_EMAIL","true" if form.snow_send_welcome_email.data else "false")
        AppConfig.set("SNOW_SEND_DID_EMAIL",    "true" if form.snow_send_did_email.data else "false")

        if form.snow_password.data:
            AppConfig.set(
                "SNOW_PASSWORD",
                encrypt(form.snow_password.data),
                encrypted=True,
                description="ServiceNow API password (encrypted)"
            )
        if form.snow_webhook_secret.data:
            AppConfig.set(
                "SNOW_WEBHOOK_SECRET",
                encrypt(form.snow_webhook_secret.data),
                encrypted=True,
                description="SNOW webhook shared secret (encrypted)"
            )

        _write_audit("snow", before, {"SNOW_INSTANCE": AppConfig.get("SNOW_INSTANCE")})
        flash("ServiceNow settings saved.", "success")
        return redirect(url_for("settings.snow"))

    webhook_url = url_for("snow.webhook_receiver", _external=True)
    return render_template(
        "snow.html", form=form,
        active_tab="snow", tabs=TABS,
        webhook_url=webhook_url,
    )


# ═════════════════════════════════════════════════════════════════════════════
# EMAIL
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/email", methods=["GET", "POST"])
@login_required
@superadmin_required
def email():
    form = EmailSettingsForm()

    if request.method == "GET":
        form.smtp_host.data         = AppConfig.get("SMTP_HOST", "")
        form.smtp_port.data         = int(AppConfig.get("SMTP_PORT", "587"))
        form.smtp_use_tls.data      = AppConfig.get("SMTP_USE_TLS", "true") == "true"
        form.smtp_use_ssl.data      = AppConfig.get("SMTP_USE_SSL", "false") == "true"
        form.smtp_username.data     = AppConfig.get("SMTP_USERNAME", "")
        form.smtp_sender_name.data  = AppConfig.get("SMTP_SENDER_NAME", "Orbit")
        form.smtp_sender_email.data = AppConfig.get("SMTP_SENDER_EMAIL", "")

    if form.validate_on_submit():
        before = {"SMTP_HOST": AppConfig.get("SMTP_HOST")}

        AppConfig.set("SMTP_HOST",         (form.smtp_host.data or "").strip())
        AppConfig.set("SMTP_PORT",         str(form.smtp_port.data or 587))
        AppConfig.set("SMTP_USE_TLS",      "true" if form.smtp_use_tls.data else "false")
        AppConfig.set("SMTP_USE_SSL",      "true" if form.smtp_use_ssl.data else "false")
        AppConfig.set("SMTP_USERNAME",     (form.smtp_username.data or "").strip())
        AppConfig.set("SMTP_SENDER_NAME",  (form.smtp_sender_name.data or "Orbit").strip())
        AppConfig.set("SMTP_SENDER_EMAIL", (form.smtp_sender_email.data or "").strip())

        if form.smtp_password.data:
            AppConfig.set(
                "SMTP_PASSWORD",
                encrypt(form.smtp_password.data),
                encrypted=True,
                description="SMTP password (encrypted)"
            )

        _write_audit("email", before, {"SMTP_HOST": AppConfig.get("SMTP_HOST")})
        flash("Email settings saved.", "success")
        return redirect(url_for("settings.email"))

    pwd_set = bool(AppConfig.get("SMTP_PASSWORD"))
    return render_template(
        "email.html", form=form,
        active_tab="email", tabs=TABS, pwd_set=pwd_set
    )


# ═════════════════════════════════════════════════════════════════════════════
# SECURITY
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/security", methods=["GET", "POST"])
@login_required
@superadmin_required
def security():
    form = SecuritySettingsForm()

    if request.method == "GET":
        form.min_password_length.data       = int(AppConfig.get("MIN_PASSWORD_LENGTH", "10"))
        form.require_uppercase.data         = AppConfig.get("REQUIRE_UPPERCASE",  "true") == "true"
        form.require_lowercase.data         = AppConfig.get("REQUIRE_LOWERCASE",  "true") == "true"
        form.require_digit.data             = AppConfig.get("REQUIRE_DIGIT",      "true") == "true"
        form.require_special.data           = AppConfig.get("REQUIRE_SPECIAL",    "true") == "true"
        form.max_login_attempts.data        = int(AppConfig.get("MAX_LOGIN_ATTEMPTS",        "5"))
        form.lockout_duration_minutes.data  = int(AppConfig.get("LOCKOUT_DURATION_MINUTES",  "15"))
        form.session_timeout_minutes.data   = int(AppConfig.get("SESSION_TIMEOUT_MINUTES",   "30"))
        form.force_https.data               = AppConfig.get("FORCE_HTTPS",        "true") == "true"
        form.audit_retention_days.data      = int(AppConfig.get("AUDIT_RETENTION_DAYS",      "365"))
        form.allow_api_tokens.data          = AppConfig.get("ALLOW_API_TOKENS",   "true") == "true"

    if form.validate_on_submit():
        before = {"MAX_LOGIN_ATTEMPTS": AppConfig.get("MAX_LOGIN_ATTEMPTS")}

        AppConfig.set("MIN_PASSWORD_LENGTH",      str(form.min_password_length.data or 10))
        AppConfig.set("REQUIRE_UPPERCASE",        "true" if form.require_uppercase.data else "false")
        AppConfig.set("REQUIRE_LOWERCASE",        "true" if form.require_lowercase.data else "false")
        AppConfig.set("REQUIRE_DIGIT",            "true" if form.require_digit.data else "false")
        AppConfig.set("REQUIRE_SPECIAL",          "true" if form.require_special.data else "false")
        AppConfig.set("MAX_LOGIN_ATTEMPTS",       str(form.max_login_attempts.data or 5))
        AppConfig.set("LOCKOUT_DURATION_MINUTES", str(form.lockout_duration_minutes.data or 15))
        AppConfig.set("SESSION_TIMEOUT_MINUTES",  str(form.session_timeout_minutes.data or 30))
        AppConfig.set("FORCE_HTTPS",              "true" if form.force_https.data else "false")
        AppConfig.set("AUDIT_RETENTION_DAYS",     str(form.audit_retention_days.data or 365))
        AppConfig.set("ALLOW_API_TOKENS",         "true" if form.allow_api_tokens.data else "false")

        _write_audit("security", before, {"MAX_LOGIN_ATTEMPTS": AppConfig.get("MAX_LOGIN_ATTEMPTS")})
        flash("Security settings saved.", "success")
        return redirect(url_for("settings.security"))

    return render_template(
        "security.html", form=form,
        active_tab="security", tabs=TABS
    )


# ═════════════════════════════════════════════════════════════════════════════
# ENVIRONMENT (read-only)
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/env", methods=["GET"])
@login_required
@superadmin_required
def env():
    import os
    safe_keys = {
        "FLASK_ENV", "FLASK_DEBUG", "REDIS_URL",
        "CELERY_BROKER_URL", "DATABASE_URL",
        "SNOW_INSTANCE", "WEBEX_ORG_ID",
    }
    env_vars = {
        k: (v if k in safe_keys else "***")
        for k, v in sorted(os.environ.items())
        if not k.startswith("_")
    }
    db_config = AppConfig.get_all()
    return render_template(
        "env.html",
        active_tab="env", tabs=TABS,
        env_vars=env_vars,
        db_config=db_config,
    )


# ═════════════════════════════════════════════════════════════════════════════
# AJAX: Connection tests
# ═════════════════════════════════════════════════════════════════════════════

@settings_bp.route("/test-webex", methods=["POST"])
@login_required
@superadmin_required
def test_webex():
    try:
        from app.services.webex_service import get_webex_client
        webex = get_webex_client()
        org   = webex.org
        return jsonify({
            "success": True,
            "message": f"Connected — Org: {getattr(org,'name','unknown')}",
        })
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@settings_bp.route("/test-snow", methods=["POST"])
@login_required
@superadmin_required
def test_snow():
    try:
        from app.services.snow_service import test_connection
        ok, msg = test_connection()
        return jsonify({"success": ok, "message": msg})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})


@settings_bp.route("/test-email", methods=["POST"])
@login_required
@superadmin_required
def test_email():
    recipient = request.json.get("recipient", "").strip()
    if not recipient:
        return jsonify({"success": False, "message": "No recipient provided."})
    try:
        from app.services.email_service import send_test_email
        ok, msg = send_test_email(recipient)
        return jsonify({"success": ok, "message": msg})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)})
