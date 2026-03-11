"""
Authentication service — handles local login, LDAP bind, password
reset token generation/validation, and JIT user provisioning for SSO.
"""
import secrets
import string
from datetime import datetime, timezone, timedelta
from typing import Tuple

import ldap3
from flask import current_app
from flask_login import login_user, logout_user

from app.extensions import db, bcrypt
from app.models.user import User, UserRole, AuthProvider
from app.models.audit import AuditLog
from app.utils.decorators import _get_ip


# ── Local Auth ────────────────────────────────────────────────────────────────

def authenticate_local(username_or_email: str,
                        password: str,
                        remember: bool = False,
                        ip: str = "") -> Tuple[bool, str, User | None]:
    """
    Validate local username/password.
    Returns (success, message, user_or_None).
    """
    user = (
        User.query.filter(
            (User.username == username_or_email) |
            (User.email    == username_or_email)
        )
        .filter_by(auth_provider=AuthProvider.LOCAL, deleted_at=None)
        .first()
    )

    if not user:
        return False, "Invalid username or password.", None

    if not user.is_active:
        return False, "Your account has been disabled. Contact your administrator.", None

    if user.is_locked:
        remaining = int((user.locked_until - datetime.now(timezone.utc)).total_seconds() / 60)
        return False, f"Account locked after too many failed attempts. Try again in {remaining} minute(s).", None

    if not user.password_hash:
        return False, "This account uses SSO or LDAP for login.", None

    if not bcrypt.check_password_hash(user.password_hash, password):
        user.increment_failed_login()
        db.session.commit()
        AuditLog.write(
            action="LOGIN_FAILED",
            username=username_or_email,
            ip_address=ip,
            resource_type="user",
            resource_name=username_or_email,
            status="failure",
            status_detail="Invalid password",
        )
        if user.is_locked:
            return False, "Account locked after 5 failed attempts. Try again in 15 minutes.", None
        return False, "Invalid username or password.", None

    user.update_last_login(ip)
    db.session.commit()
    login_user(user, remember=remember)

    AuditLog.write(
        action="LOGIN",
        user_id=user.id,
        username=user.username,
        user_role=user.role.value,
        ip_address=ip,
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
    )
    return True, "Login successful.", user


# ── LDAP Auth ─────────────────────────────────────────────────────────────────

def authenticate_ldap(username: str,
                       password: str,
                       ip: str = "") -> Tuple[bool, str, User | None]:
    """
    Authenticate against LDAP, then JIT-provision or update the local user record.
    """
    import os
    from app.utils.crypto import decrypt

    host      = os.environ.get("LDAP_HOST", "")
    port      = int(os.environ.get("LDAP_PORT", 389))
    use_ssl   = os.environ.get("LDAP_USE_SSL", "false").lower() == "true"
    starttls  = os.environ.get("LDAP_USE_STARTTLS", "false").lower() == "true"
    bind_dn   = os.environ.get("LDAP_BIND_DN", "")
    bind_pw   = decrypt(os.environ.get("LDAP_BIND_PASSWORD", ""))
    base_dn   = os.environ.get("LDAP_BASE_DN", "")
    user_filter = os.environ.get("LDAP_USER_FILTER", "(mail={username})")

    attr_email = os.environ.get("LDAP_ATTR_EMAIL", "mail")
    attr_fn    = os.environ.get("LDAP_ATTR_FIRSTNAME", "givenName")
    attr_ln    = os.environ.get("LDAP_ATTR_LASTNAME", "sn")

    try:
        tls = ldap3.Tls(validate=0) if (use_ssl or starttls) else None
        server = ldap3.Server(host, port=port, use_ssl=use_ssl,
                               tls=tls, connect_timeout=5)

        # Service bind to find the user DN
        svc_conn = ldap3.Connection(server, user=bind_dn, password=bind_pw, auto_bind=False)
        svc_conn.open()
        if starttls:
            svc_conn.start_tls()
        svc_conn.bind()

        search_filter = user_filter.replace("{username}", ldap3.utils.conv.escape_filter_chars(username))
        svc_conn.search(
            base_dn, search_filter,
            attributes=[attr_email, attr_fn, attr_ln, "dn"]
        )

        if not svc_conn.entries:
            svc_conn.unbind()
            return False, "User not found in directory.", None

        entry    = svc_conn.entries[0]
        user_dn  = entry.entry_dn
        email    = str(entry[attr_email].value) if attr_email in entry else username
        fn       = str(entry[attr_fn].value)    if attr_fn    in entry else ""
        ln       = str(entry[attr_ln].value)    if attr_ln    in entry else ""
        svc_conn.unbind()

        # User bind to verify password
        user_conn = ldap3.Connection(server, user=user_dn, password=password, auto_bind=False)
        user_conn.open()
        if starttls:
            user_conn.start_tls()
        if not user_conn.bind():
            return False, "Invalid LDAP password.", None
        user_conn.unbind()

        # JIT-provision or update the local user record
        user = _jit_provision(
            email=email,
            username=username,
            first_name=fn,
            last_name=ln,
            auth_provider=AuthProvider.LDAP,
            sso_subject=user_dn,
        )
        user.update_last_login(ip)
        db.session.commit()
        login_user(user)

        AuditLog.write(
            action="LOGIN",
            user_id=user.id, username=user.username,
            user_role=user.role.value, ip_address=ip,
            resource_type="user", resource_id=user.id,
            resource_name=user.username,
        )
        return True, "LDAP login successful.", user

    except ldap3.core.exceptions.LDAPSocketOpenError:
        return False, "Cannot reach LDAP server. Contact your administrator.", None
    except Exception as exc:
        current_app.logger.error(f"LDAP auth error: {exc}")
        return False, "Directory authentication error. Contact your administrator.", None


# ── SSO JIT Provisioning ──────────────────────────────────────────────────────

def provision_sso_user(email: str, first_name: str, last_name: str,
                        sso_subject: str, provider: str,
                        groups: list[str] | None = None,
                        ip: str = "") -> Tuple[bool, str, User | None]:
    """
    Called from SAML ACS / OIDC callback after successful IdP authentication.
    JIT-provisions user if not existing, updates on subsequent logins.
    """
    import os
    admin_group = os.environ.get("SSO_ADMIN_GROUP", "")

    role = UserRole.GUI_ADMIN if (groups and admin_group and admin_group in groups) \
           else UserRole.END_USER

    try:
        user = _jit_provision(
            email=email,
            username=email.split("@")[0].lower().replace(".", "_"),
            first_name=first_name,
            last_name=last_name,
            auth_provider=AuthProvider.SAML if provider == "saml" else AuthProvider.OIDC,
            sso_subject=sso_subject,
            sso_provider=provider,
            role_override=role,
        )
        user.update_last_login(ip)
        db.session.commit()
        login_user(user)

        AuditLog.write(
            action="LOGIN_SSO",
            user_id=user.id, username=user.username,
            user_role=user.role.value, ip_address=ip,
            resource_type="user", resource_id=user.id,
        )
        return True, "SSO login successful.", user
    except Exception as exc:
        current_app.logger.error(f"SSO provision error: {exc}")
        return False, f"SSO provisioning failed: {exc}", None


def _jit_provision(email: str, username: str, first_name: str,
                    last_name: str, auth_provider: AuthProvider,
                    sso_subject: str = "", sso_provider: str = "",
                    role_override: UserRole | None = None) -> User:
    """Create or update a user record from an external auth source."""
    user = User.query.filter_by(email=email).first()
    if user is None:
        user = User(
            username=_unique_username(username),
            email=email,
            first_name=first_name,
            last_name=last_name,
            auth_provider=auth_provider,
            role=role_override or UserRole.END_USER,
            is_active=True,
            is_local=False,
            sso_subject=sso_subject,
            sso_provider=sso_provider,
        )
        db.session.add(user)
    else:
        user.first_name    = first_name or user.first_name
        user.last_name     = last_name  or user.last_name
        user.sso_subject   = sso_subject or user.sso_subject
        user.auth_provider = auth_provider
        if role_override:
            user.role = role_override
    db.session.flush()
    return user


def _unique_username(base: str) -> str:
    username = base[:60]
    if not User.query.filter_by(username=username).first():
        return username
    for i in range(2, 999):
        candidate = f"{username[:57]}{i}"
        if not User.query.filter_by(username=candidate).first():
            return candidate
    return base + secrets.token_hex(4)


# ── Password Reset ────────────────────────────────────────────────────────────

def generate_reset_token(email: str) -> Tuple[bool, str]:
    """
    Generate a secure password reset token for a local account.
    Returns (success, message). Sends email via email_service.
    """
    from app.services.email_service import send_password_reset_email

    user = User.query.filter_by(
        email=email.lower().strip(),
        auth_provider=AuthProvider.LOCAL,
        deleted_at=None
    ).first()

    if not user:
        # Return True anyway — do not leak whether email exists
        return True, "If that email exists, a reset link has been sent."

    token = secrets.token_urlsafe(48)
    user.reset_token        = bcrypt.generate_password_hash(token).decode()
    user.reset_token_expiry = datetime.now(timezone.utc) + timedelta(hours=2)
    db.session.commit()

    send_password_reset_email(user, token)

    AuditLog.write(
        action="PASSWORD_RESET_REQUESTED",
        user_id=user.id, username=user.username,
        resource_type="user", resource_id=user.id,
    )
    return True, "If that email exists, a reset link has been sent."


def validate_reset_token(token: str, email: str) -> Tuple[bool, str, User | None]:
    """Verify a password reset token is valid and unexpired."""
    user = User.query.filter_by(
        email=email.lower().strip(),
        auth_provider=AuthProvider.LOCAL,
        deleted_at=None
    ).first()

    if not user or not user.reset_token or not user.reset_token_expiry:
        return False, "Invalid or expired reset link.", None

    if datetime.now(timezone.utc) > user.reset_token_expiry:
        return False, "This reset link has expired. Please request a new one.", None

    if not bcrypt.check_password_hash(user.reset_token, token):
        return False, "Invalid reset link.", None

    return True, "Token valid.", user


def complete_password_reset(user: User, new_password: str) -> None:
    """Apply the new password and invalidate the token."""
    user.password_hash      = bcrypt.generate_password_hash(new_password).decode()
    user.reset_token        = None
    user.reset_token_expiry = None
    user.must_change_password = False
    user.clear_failed_logins()
    db.session.commit()

    AuditLog.write(
        action="PASSWORD_RESET_COMPLETED",
        user_id=user.id, username=user.username,
        resource_type="user", resource_id=user.id,
    )


# ── Flask-Login user loader ───────────────────────────────────────────────────

def register_user_loader(login_manager_instance) -> None:
    @login_manager_instance.user_loader
    def load_user(user_id: str) -> User | None:
        return User.query.filter_by(
            id=int(user_id), deleted_at=None
        ).first()
