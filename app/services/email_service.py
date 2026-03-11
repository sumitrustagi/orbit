"""
Centralised email helper using Flask-Mail + Jinja2 HTML templates.
All emails are branded with the logo and colours from AppConfig.
"""
from flask import current_app, render_template
from flask_mail import Message
from app.extensions import mail
from app.models.app_config import AppConfig


def _base_context() -> dict:
    return {
        "app_name":      AppConfig.get("APP_NAME", "Orbit"),
        "company_name":  AppConfig.get("COMPANY_NAME", ""),
        "primary_color": AppConfig.get("PRIMARY_COLOR", "#1E40AF"),
        "accent_color":  AppConfig.get("ACCENT_COLOR", "#3B82F6"),
        "logo_path":     AppConfig.get("LOGO_PATH", ""),
        "server_fqdn":   AppConfig.get("SERVER_FQDN", "localhost"),
    }


def send_email(to: str, subject: str, template: str, **kwargs) -> bool:
    """
    Render a Jinja2 email template and send it.
    Returns True on success.
    """
    try:
        ctx  = {**_base_context(), **kwargs}
        html = render_template(f"email/{template}.html", **ctx)
        msg  = Message(
            subject=subject,
            recipients=[to],
            html=html,
            sender=(
                AppConfig.get("SMTP_FROM_NAME", "Orbit Platform"),
                AppConfig.get("SMTP_FROM", current_app.config["MAIL_DEFAULT_SENDER"])
            )
        )
        mail.send(msg)
        return True
    except Exception as exc:
        current_app.logger.error(f"Email send failed to {to}: {exc}")
        return False


def send_password_reset_email(user, token: str) -> bool:
    fqdn      = AppConfig.get("SERVER_FQDN", "localhost")
    reset_url = f"https://{fqdn}/auth/reset-password?token={token}&email={user.email}"
    return send_email(
        to=user.email,
        subject="Orbit — Password Reset Request",
        template="password_reset",
        user=user,
        reset_url=reset_url,
        expires_hours=2,
    )


def send_welcome_email(user, did: str, extension: str) -> bool:
    fqdn      = AppConfig.get("SERVER_FQDN", "localhost")
    login_url = f"https://{fqdn}/auth/login"
    return send_email(
        to=user.email,
        subject=f"Welcome to {AppConfig.get('APP_NAME','Orbit')} — Your Calling Details",
        template="welcome",
        user=user,
        did=did,
        extension=extension,
        login_url=login_url,
    )


def send_did_assignment_email(user, did: str, extension: str,
                               calling_access: str) -> bool:
    return send_email(
        to=user.email,
        subject="Your Webex Calling Number Has Been Assigned",
        template="did_assignment",
        user=user,
        did=did,
        extension=extension,
        calling_access=calling_access,
    )
