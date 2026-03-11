import os
from urllib.parse import urlencode, quote_plus

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, session, flash, current_app, abort, jsonify
)
from flask_login import login_required, logout_user, current_user
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

from app.extensions import limiter
from app.forms.auth_forms import LoginForm, ForgotPasswordForm, ResetPasswordForm
from app.services import auth_service as svc
from app.models.audit import AuditLog
from app.utils.decorators import _get_ip

auth_bp = Blueprint("auth", __name__,
                     template_folder="../templates/auth",
                     url_prefix="/auth")


# ── Login ─────────────────────────────────────────────────────────────────────

@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("20 per minute", key_func=get_remote_address)
def login():
    if current_user.is_authenticated:
        return redirect(_post_login_redirect(current_user))

    form       = LoginForm()
    sso_enabled = os.environ.get("SSO_ENABLED", "false").lower() == "true"
    sso_provider = os.environ.get("SSO_PROVIDER", "")
    ldap_enabled = os.environ.get("LDAP_ENABLED", "false").lower() == "true"

    if form.validate_on_submit():
        username = form.username.data.strip()
        password = form.password.data
        remember = form.remember_me.data
        ip       = _get_ip()

        # Try LDAP first if enabled and user has no local password
        if ldap_enabled:
            ok, msg, user = svc.authenticate_ldap(username, password, ip)
            if ok:
                flash(f"Welcome back, {user.full_name}!", "success")
                return redirect(_post_login_redirect(user))

        # Fall through to local auth
        ok, msg, user = svc.authenticate_local(username, password, remember, ip)
        if ok:
            if user.must_change_password:
                flash("Your password must be changed before continuing.", "warning")
                return redirect(url_for("auth.force_change_password"))
            flash(f"Welcome back, {user.full_name}!", "success")
            return redirect(_post_login_redirect(user))

        flash(msg, "danger")

    return render_template(
        "login.html",
        form=form,
        sso_enabled=sso_enabled,
        sso_provider=sso_provider,
        ldap_enabled=ldap_enabled,
    )


# ── Logout ────────────────────────────────────────────────────────────────────

@auth_bp.route("/logout")
@login_required
def logout():
    AuditLog.write(
        action="LOGOUT",
        user_id=current_user.id,
        username=current_user.username,
        user_role=current_user.role.value,
        ip_address=_get_ip(),
        resource_type="user",
        resource_id=current_user.id,
    )
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))


# ── Force password change ─────────────────────────────────────────────────────

@auth_bp.route("/change-password", methods=["GET", "POST"])
@login_required
def force_change_password():
    from app.forms.auth_forms import ChangePasswordForm
    form = ChangePasswordForm()

    if form.validate_on_submit():
        from app.extensions import bcrypt
        if not bcrypt.check_password_hash(
            current_user.password_hash, form.current_password.data
        ):
            flash("Current password is incorrect.", "danger")
            return render_template("change_password.html", form=form)

        svc.complete_password_reset(current_user, form.new_password.data)
        flash("Password changed successfully.", "success")
        return redirect(_post_login_redirect(current_user))

    return render_template("change_password.html", form=form)


# ── Forgot password ───────────────────────────────────────────────────────────

@auth_bp.route("/forgot-password", methods=["GET", "POST"])
@limiter.limit("5 per hour", key_func=get_remote_address)
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("admin.dashboard"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        ok, msg = svc.generate_reset_token(form.email.data)
        flash(msg, "info")
        return redirect(url_for("auth.login"))

    return render_template("forgot_password.html", form=form)


# ── Reset password ────────────────────────────────────────────────────────────

@auth_bp.route("/reset-password", methods=["GET", "POST"])
@limiter.limit("10 per hour", key_func=get_remote_address)
def reset_password():
    token = request.args.get("token", "")
    email = request.args.get("email", "")

    if not token or not email:
        flash("Invalid reset link.", "danger")
        return redirect(url_for("auth.login"))

    ok, msg, user = svc.validate_reset_token(token, email)
    if not ok:
        flash(msg, "danger")
        return redirect(url_for("auth.forgot_password"))

    form = ResetPasswordForm()
    if form.validate_on_submit():
        svc.complete_password_reset(user, form.new_password.data)
        flash("Password reset successfully. Please log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("reset_password.html", form=form, token=token, email=email)


# ── SAML 2.0 ──────────────────────────────────────────────────────────────────

@auth_bp.route("/saml/login")
def saml_login():
    """Redirect browser to IdP for SAML authentication."""
    sso_url = os.environ.get("SAML_SSO_URL", "")
    if not sso_url:
        flash("SAML SSO is not configured.", "danger")
        return redirect(url_for("auth.login"))

    fqdn = os.environ.get("SERVER_FQDN", request.host)
    acs_url = f"https://{fqdn}/auth/saml/acs"

    # Build minimal AuthnRequest redirect
    params = {
        "SAMLRequest":  _build_authn_request(acs_url),
        "RelayState":   request.args.get("next", "/"),
    }
    return redirect(f"{sso_url}?{urlencode(params)}")


@auth_bp.route("/saml/acs", methods=["POST"])
def saml_acs():
    """
    SAML Assertion Consumer Service — receives POST from IdP.
    Full python3-saml processing is wired here.
    """
    try:
        from onelogin.saml2.auth import OneLogin_Saml2_Auth
        req  = _prepare_saml_request(request)
        auth = OneLogin_Saml2_Auth(req, custom_base_path=_saml_settings_path())
        auth.process_response()

        errors = auth.get_errors()
        if errors:
            current_app.logger.error(f"SAML ACS errors: {errors}")
            flash("SSO authentication failed. Contact your administrator.", "danger")
            return redirect(url_for("auth.login"))

        attrs       = auth.get_attributes()
        name_id     = auth.get_nameid()
        sso_provider = os.environ.get("SSO_PROVIDER", "saml")

        email    = _saml_attr(attrs, os.environ.get("SSO_ATTR_EMAIL", "email"), name_id)
        fname    = _saml_attr(attrs, os.environ.get("SSO_ATTR_FIRSTNAME", "given_name"), "")
        lname    = _saml_attr(attrs, os.environ.get("SSO_ATTR_LASTNAME", "family_name"), "")
        groups   = attrs.get("groups", [])

        ok, msg, user = svc.provision_sso_user(
            email=email, first_name=fname, last_name=lname,
            sso_subject=name_id, provider="saml",
            groups=groups, ip=_get_ip()
        )
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("auth.login"))

        flash(f"Welcome, {user.full_name}!", "success")
        relay = request.form.get("RelayState", "/")
        return redirect(relay if relay.startswith("/") else "/")

    except ImportError:
        flash("SAML library not installed. Run: pip install python3-saml", "danger")
        return redirect(url_for("auth.login"))
    except Exception as exc:
        current_app.logger.error(f"SAML ACS exception: {exc}")
        flash("SSO error. Contact your administrator.", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/saml/metadata")
def saml_metadata():
    """Serve SP metadata XML for the IdP admin to configure."""
    fqdn = os.environ.get("SERVER_FQDN", request.host)
    from flask import Response
    xml = f"""<?xml version="1.0"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
  entityID="https://{fqdn}/auth/saml/metadata">
  <md:SPSSODescriptor
    AuthnRequestsSigned="false"
    WantAssertionsSigned="true"
    protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:NameIDFormat>urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress</md:NameIDFormat>
    <md:AssertionConsumerService
      Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
      Location="https://{fqdn}/auth/saml/acs"
      index="1"/>
  </md:SPSSODescriptor>
</md:EntityDescriptor>"""
    return Response(xml, mimetype="application/xml",
                    headers={"Content-Disposition": "attachment; filename=orbit-sp-metadata.xml"})


# ── OIDC ──────────────────────────────────────────────────────────────────────

@auth_bp.route("/oidc/login")
def oidc_login():
    """Redirect to IdP OIDC authorisation endpoint."""
    from authlib.integrations.flask_client import OAuth
    try:
        oauth = _get_oauth()
        redirect_uri = url_for("auth.oidc_callback", _external=True, _scheme="https")
        return oauth.orbit_oidc.authorize_redirect(redirect_uri)
    except Exception as exc:
        current_app.logger.error(f"OIDC login error: {exc}")
        flash("OIDC SSO not configured correctly.", "danger")
        return redirect(url_for("auth.login"))


@auth_bp.route("/auth/oidc/callback")
def oidc_callback():
    """Handle OIDC token exchange and user provisioning."""
    try:
        oauth   = _get_oauth()
        token   = oauth.orbit_oidc.authorize_access_token()
        userinfo = token.get("userinfo") or oauth.orbit_oidc.userinfo(token=token)

        email  = userinfo.get(os.environ.get("SSO_ATTR_EMAIL", "email"), "")
        fname  = userinfo.get(os.environ.get("SSO_ATTR_FIRSTNAME", "given_name"), "")
        lname  = userinfo.get(os.environ.get("SSO_ATTR_LASTNAME", "family_name"), "")
        sub    = userinfo.get("sub", email)
        groups = userinfo.get("groups", [])

        ok, msg, user = svc.provision_sso_user(
            email=email, first_name=fname, last_name=lname,
            sso_subject=sub, provider="oidc",
            groups=groups, ip=_get_ip()
        )
        if not ok:
            flash(msg, "danger")
            return redirect(url_for("auth.login"))

        flash(f"Welcome, {user.full_name}!", "success")
        return redirect(_post_login_redirect(user))

    except Exception as exc:
        current_app.logger.error(f"OIDC callback error: {exc}")
        flash("OIDC authentication failed.", "danger")
        return redirect(url_for("auth.login"))


# ── Session heartbeat (AJAX — keeps session alive on user activity) ────────────

@auth_bp.route("/heartbeat", methods=["POST"])
@login_required
def heartbeat():
    """Called every 5 minutes by frontend JS to refresh session timestamp."""
    from datetime import datetime, timezone
    session["_last_activity"] = datetime.now(timezone.utc).timestamp()
    timeout = current_app.config["SESSION_TIMEOUT_MINUTES"]
    return jsonify({"status": "ok", "timeout_minutes": timeout})


# ── Helpers ───────────────────────────────────────────────────────────────────

def _post_login_redirect(user) -> str:
    """Route user to the correct landing page based on role."""
    from app.models.user import UserRole
    next_url = request.args.get("next")
    if next_url and next_url.startswith("/"):
        return next_url
    if user.role == UserRole.PLATFORM_ADMIN:
        return url_for("system.dashboard")
    if user.role == UserRole.GUI_ADMIN:
        return url_for("admin.dashboard")
    return url_for("portal.dashboard")


def _saml_attr(attrs: dict, key: str, fallback: str) -> str:
    val = attrs.get(key, [fallback])
    return val[0] if isinstance(val, list) and val else str(val) if val else fallback


def _saml_settings_path() -> str:
    import os
    return os.path.join(current_app.root_path, "saml")


def _prepare_saml_request(req) -> dict:
    return {
        "https":          "on",
        "http_host":      req.host,
        "script_name":    req.path,
        "get_data":       req.args.copy(),
        "post_data":      req.form.copy(),
    }


def _build_authn_request(acs_url: str) -> str:
    """Minimal base64-encoded AuthnRequest for redirect binding."""
    import base64, zlib, uuid
    from datetime import datetime, timezone
    xml = (
        f'<samlp:AuthnRequest xmlns:samlp="urn:oasis:names:tc:SAML:2.0:protocol" '
        f'ID="id{uuid.uuid4().hex}" Version="2.0" '
        f'IssueInstant="{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}" '
        f'AssertionConsumerServiceURL="{acs_url}" '
        f'ProtocolBinding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST">'
        f'<saml:Issuer xmlns:saml="urn:oasis:names:tc:SAML:2.0:assertion">'
        f'https://{os.environ.get("SERVER_FQDN","localhost")}/auth/saml/metadata'
        f'</saml:Issuer></samlp:AuthnRequest>'
    )
    compressed = zlib.compress(xml.encode())[2:-4]
    return base64.b64encode(compressed).decode()


def _get_oauth():
    """Lazily configure Authlib OAuth for OIDC."""
    from authlib.integrations.flask_client import OAuth
    from app.utils.crypto import decrypt

    oauth = OAuth(current_app)
    oauth.register(
        name="orbit_oidc",
        client_id=os.environ.get("OIDC_CLIENT_ID", ""),
        client_secret=decrypt(os.environ.get("OIDC_CLIENT_SECRET", "")),
        server_metadata_url=os.environ.get("OIDC_DISCOVERY_URL", ""),
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth
