"""
Setup Service — handles all business logic for the first-time setup wizard.
Called by setup.py routes; never imports Flask request objects directly.
"""
import os
import subprocess
import shutil
import socket
from pathlib import Path
from typing import Tuple, Dict, Any

import ldap3
import requests
from cryptography.fernet import Fernet
from dotenv import set_key, dotenv_values


ENV_FILE = Path(os.environ.get("ORBIT_HOME", "/opt/orbit")) / ".env"
CERT_DIR = Path(os.environ.get("ORBIT_HOME", "/opt/orbit")) / "certs"
UPLOAD_DIR = Path(os.environ.get("ORBIT_HOME", "/opt/orbit")) / "app/static/uploads"


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fernet() -> Fernet:
    key = os.environ.get("FERNET_KEY", "").encode()
    return Fernet(key)


def encrypt_value(plain: str) -> str:
    """Encrypt a string with Fernet symmetric encryption."""
    if not plain:
        return ""
    return _fernet().encrypt(plain.encode()).decode()


def write_env(key: str, value: str) -> None:
    """Safely write a key=value pair to the .env file."""
    set_key(str(ENV_FILE), key, value, quote_mode="never")
    os.environ[key] = value


# ── Step 1 — Branding ─────────────────────────────────────────────────────────

def save_logo(file_storage) -> Tuple[bool, str]:
    """
    Validate and save the uploaded logo.
    Returns (success, message_or_filename).
    """
    from PIL import Image
    import io

    MAX_BYTES = 200 * 1024        # 200 KB
    MAX_DIM   = (512, 512)
    LOGO_DIR  = UPLOAD_DIR / "logos"
    LOGO_DIR.mkdir(parents=True, exist_ok=True)

    data = file_storage.read()
    if len(data) > MAX_BYTES:
        return False, f"Logo exceeds 200 KB limit ({len(data)//1024} KB uploaded)."

    ext = Path(file_storage.filename).suffix.lower()
    if ext == ".svg":
        # SVGs are stored as-is (no PIL processing)
        dest = LOGO_DIR / "orbit_logo.svg"
        dest.write_bytes(data)
        return True, "logos/orbit_logo.svg"

    try:
        img = Image.open(io.BytesIO(data))
        if img.size[0] > MAX_DIM[0] or img.size[1] > MAX_DIM[1]:
            img.thumbnail(MAX_DIM, Image.LANCZOS)

        dest = LOGO_DIR / f"orbit_logo{ext}"
        img.save(dest, optimize=True)
        return True, f"logos/orbit_logo{ext}"
    except Exception as exc:
        return False, f"Invalid image file: {exc}"


def save_branding(data: Dict[str, Any], logo_path: str = "") -> None:
    """Persist branding settings to .env."""
    write_env("APP_NAME",       data.get("app_name", "Orbit"))
    write_env("COMPANY_NAME",   data.get("company_name", ""))
    write_env("PRIMARY_COLOR",  data.get("primary_color", "#1E40AF"))
    write_env("ACCENT_COLOR",   data.get("accent_color", "#3B82F6"))
    write_env("DEFAULT_TZ",     data.get("timezone", "UTC"))
    write_env("DEFAULT_LANG",   data.get("language", "en"))
    if logo_path:
        write_env("LOGO_PATH", logo_path)


# ── Step 2 — Network / TLS ────────────────────────────────────────────────────

def request_letsencrypt(fqdn: str, email: str) -> Tuple[bool, str]:
    """
    Run Certbot in non-interactive mode.
    Returns (success, message).
    """
    certbot = shutil.which("certbot")
    if not certbot:
        return False, "Certbot not found. Install it with: apt install certbot."

    cmd = [
        certbot, "certonly",
        "--nginx", "--non-interactive", "--agree-tos",
        "--email", email,
        "-d", fqdn,
        "--cert-path",    str(CERT_DIR / "orbit.crt"),
        "--key-path",     str(CERT_DIR / "orbit.key"),
        "--fullchain-path", str(CERT_DIR / "orbit-fullchain.crt"),
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            write_env("CERT_PATH", str(CERT_DIR / "orbit-fullchain.crt"))
            write_env("KEY_PATH",  str(CERT_DIR / "orbit.key"))
            return True, "Let's Encrypt certificate obtained successfully."
        return False, f"Certbot error: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, "Certbot timed out. Ensure port 80 is publicly accessible."
    except Exception as exc:
        return False, f"Certbot execution failed: {exc}"


def save_custom_cert(ca_file, cert_file, key_file) -> Tuple[bool, str]:
    """Save uploaded certificate files."""
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        ca_path   = CERT_DIR / "custom_ca.crt"
        cert_path = CERT_DIR / "orbit.crt"
        key_path  = CERT_DIR / "orbit.key"

        ca_file.save(str(ca_path))
        cert_file.save(str(cert_path))
        key_file.save(str(key_path))

        # Basic validation — check the cert can be read
        subprocess.run(
            ["openssl", "x509", "-noout", "-text", "-in", str(cert_path)],
            check=True, capture_output=True, timeout=10
        )
        subprocess.run(
            ["openssl", "rsa", "-noout", "-check", "-in", str(key_path)],
            check=True, capture_output=True, timeout=10
        )

        # Verify cert matches key
        cert_mod = subprocess.check_output(
            ["openssl", "x509", "-noout", "-modulus", "-in", str(cert_path)], timeout=5
        )
        key_mod = subprocess.check_output(
            ["openssl", "rsa", "-noout", "-modulus", "-in", str(key_path)], timeout=5
        )
        if cert_mod != key_mod:
            return False, "Certificate and private key do not match."

        write_env("CERT_PATH", str(cert_path))
        write_env("KEY_PATH",  str(key_path))
        return True, "Custom certificate validated and saved."
    except subprocess.CalledProcessError as exc:
        return False, f"Invalid certificate or key file: {exc.stderr.decode()}"
    except Exception as exc:
        return False, f"Certificate save failed: {exc}"


def save_network(fqdn: str, cert_mode: str) -> None:
    write_env("SERVER_FQDN", fqdn)
    write_env("CERT_MODE",   cert_mode)


# ── Step 3 — LDAP ─────────────────────────────────────────────────────────────

def test_ldap_connection(host: str, port: int, use_ssl: bool,
                         use_starttls: bool, bind_dn: str,
                         bind_password: str) -> Tuple[bool, str]:
    """
    Attempt an LDAP bind to validate connectivity and credentials.
    Returns (success, message).
    """
    try:
        tls = ldap3.Tls(validate=0) if (use_ssl or use_starttls) else None
        server = ldap3.Server(
            host, port=port, use_ssl=use_ssl, tls=tls,
            connect_timeout=5, get_info=ldap3.ALL
        )
        conn = ldap3.Connection(server, user=bind_dn, password=bind_password, auto_bind=False)

        if use_starttls:
            conn.open()
            conn.start_tls()
        else:
            conn.open()

        conn.bind()
        if conn.result["result"] == 0:
            conn.unbind()
            return True, "LDAP bind successful."
        return False, f"LDAP bind failed: {conn.result.get('description', 'unknown error')}"
    except ldap3.core.exceptions.LDAPSocketOpenError as exc:
        return False, f"Cannot reach LDAP server at {host}:{port} — {exc}"
    except Exception as exc:
        return False, f"LDAP error: {exc}"


def save_ldap(data: Dict[str, Any]) -> None:
    """Persist LDAP settings to .env (password encrypted)."""
    write_env("LDAP_ENABLED",        str(data.get("ldap_enabled", False)).lower())
    write_env("LDAP_HOST",           data.get("ldap_host", ""))
    write_env("LDAP_PORT",           str(data.get("ldap_port", 389)))
    write_env("LDAP_USE_SSL",        str(data.get("ldap_use_ssl", False)).lower())
    write_env("LDAP_USE_STARTTLS",   str(data.get("ldap_use_starttls", False)).lower())
    write_env("LDAP_BIND_DN",        data.get("ldap_bind_dn", ""))
    write_env("LDAP_BIND_PASSWORD",  encrypt_value(data.get("ldap_bind_password", "")))
    write_env("LDAP_BASE_DN",        data.get("ldap_base_dn", ""))
    write_env("LDAP_USER_FILTER",    data.get("ldap_user_filter", "(mail={username})"))
    write_env("LDAP_GROUP_BASE_DN",  data.get("ldap_group_base_dn", ""))
    write_env("LDAP_ADMIN_GROUP_DN", data.get("ldap_admin_group_dn", ""))
    write_env("LDAP_USER_GROUP_DN",  data.get("ldap_user_group_dn", ""))
    write_env("LDAP_ATTR_EMAIL",     data.get("ldap_attr_email", "mail"))
    write_env("LDAP_ATTR_FIRSTNAME", data.get("ldap_attr_firstname", "givenName"))
    write_env("LDAP_ATTR_LASTNAME",  data.get("ldap_attr_lastname", "sn"))
    write_env("LDAP_ATTR_USERNAME",  data.get("ldap_attr_username", "sAMAccountName"))


# ── Step 4 — SSO ──────────────────────────────────────────────────────────────

def save_sso(data: Dict[str, Any]) -> None:
    write_env("SSO_ENABLED",          str(data.get("sso_enabled", False)).lower())
    write_env("SSO_PROVIDER",         data.get("sso_provider", ""))
    write_env("SSO_PROTOCOL",         data.get("sso_protocol", "saml"))
    write_env("SAML_ENTITY_ID",       data.get("saml_entity_id", ""))
    write_env("SAML_SSO_URL",         data.get("saml_sso_url", ""))
    write_env("SAML_SLO_URL",         data.get("saml_slo_url", ""))
    write_env("OIDC_CLIENT_ID",       data.get("oidc_client_id", ""))
    write_env("OIDC_CLIENT_SECRET",   encrypt_value(data.get("oidc_client_secret", "")))
    write_env("OIDC_DISCOVERY_URL",   data.get("oidc_discovery_url", ""))
    write_env("SSO_ATTR_EMAIL",       data.get("sso_attr_email", "email"))
    write_env("SSO_ATTR_FIRSTNAME",   data.get("sso_attr_firstname", "given_name"))
    write_env("SSO_ATTR_LASTNAME",    data.get("sso_attr_lastname", "family_name"))
    write_env("SSO_ADMIN_GROUP",      data.get("sso_admin_group", ""))


# ── Step 5 — SMTP ─────────────────────────────────────────────────────────────

def test_smtp(host: str, port: int, use_tls: bool, use_ssl: bool,
              username: str, password: str,
              from_addr: str, to_addr: str) -> Tuple[bool, str]:
    """Send a test email. Returns (success, message)."""
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(
        "This is a test email from the Orbit Provisioning Platform.\n\n"
        "If you received this, SMTP is configured correctly.",
        "plain"
    )
    msg["Subject"] = "Orbit — SMTP Test Email"
    msg["From"]    = from_addr
    msg["To"]      = to_addr

    try:
        smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        with smtp_cls(host, port, timeout=10) as smtp:
            if use_tls and not use_ssl:
                smtp.starttls()
            if username:
                smtp.login(username, password)
            smtp.sendmail(from_addr, [to_addr], msg.as_string())
        return True, f"Test email sent to {to_addr}."
    except Exception as exc:
        return False, f"SMTP test failed: {exc}"


def save_smtp(data: Dict[str, Any]) -> None:
    write_env("SMTP_HOST",         data.get("smtp_host", ""))
    write_env("SMTP_PORT",         str(data.get("smtp_port", 587)))
    write_env("SMTP_USE_TLS",      str(data.get("smtp_use_tls", True)).lower())
    write_env("SMTP_USE_SSL",      str(data.get("smtp_use_ssl", False)).lower())
    write_env("SMTP_USERNAME",     data.get("smtp_username", ""))
    write_env("SMTP_PASSWORD",     encrypt_value(data.get("smtp_password", "")))
    write_env("SMTP_FROM_NAME",    data.get("smtp_from_name", "Orbit Platform"))
    write_env("SMTP_FROM",         data.get("smtp_from_address", ""))


# ── Step 6 — Admin Account ────────────────────────────────────────────────────

def create_platform_admin(data: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Create the first platform admin in the database.
    Must be called inside an active Flask app context.
    """
    from app.extensions import db, bcrypt
    from app.models.user import User, UserRole

    try:
        existing = User.query.filter(
            (User.username == data["admin_username"]) |
            (User.email == data["admin_email"])
        ).first()

        if existing:
            return False, "A user with that username or email already exists."

        pw_hash = bcrypt.generate_password_hash(data["admin_password"]).decode("utf-8")
        admin = User(
            username=data["admin_username"],
            email=data["admin_email"],
            first_name=data["admin_first_name"],
            last_name=data["admin_last_name"],
            password_hash=pw_hash,
            role=UserRole.PLATFORM_ADMIN,
            is_active=True,
            is_local=True,
            must_change_password=False,
        )
        db.session.add(admin)
        db.session.commit()
        return True, f"Platform admin '{data['admin_username']}' created."
    except Exception as exc:
        return False, f"Failed to create admin: {exc}"


# ── Step 7 — Webex Token ──────────────────────────────────────────────────────

def test_webex_token(token: str) -> Tuple[bool, str, Dict]:
    """
    Validate the Webex token by calling the /people/me endpoint.
    Returns (success, message, org_info).
    """
    try:
        resp = requests.get(
            "https://webexapis.com/v1/people/me",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10
        )
        if resp.status_code == 200:
            me = resp.json()
            # Fetch org info
            org_id = me.get("orgId", "")
            org_resp = requests.get(
                f"https://webexapis.com/v1/organizations/{org_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10
            )
            org_name = org_resp.json().get("displayName", "Unknown") if org_resp.ok else "Unknown"
            return True, f"Token valid. Authenticated as: {me.get('displayName')} | Org: {org_name}", {
                "display_name": me.get("displayName"),
                "email":        me.get("emails", [""])[0],
                "org_id":       org_id,
                "org_name":     org_name,
            }
        elif resp.status_code == 401:
            return False, "Token invalid or expired (HTTP 401).", {}
        else:
            return False, f"Webex API returned HTTP {resp.status_code}.", {}
    except requests.ConnectionError:
        return False, "Cannot reach Webex API. Check internet connectivity.", {}
    except Exception as exc:
        return False, f"Webex token test failed: {exc}", {}


def save_webex(token: str, org_id: str) -> None:
    write_env("WEBEX_ACCESS_TOKEN", encrypt_value(token))
    write_env("WEBEX_ORG_ID",       org_id)


# ── Completion — Nginx HTTPS Switch ───────────────────────────────────────────

def mark_setup_complete() -> None:
    """Mark setup as complete in .env and reload app config."""
    write_env("APP_STATE", "setup_complete")
    os.environ["APP_STATE"] = "setup_complete"
