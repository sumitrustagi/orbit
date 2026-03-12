"""
Celery tasks for outbound email notifications.

Tasks:
  send_fulfillment_email(request_id)   — DID fulfillment confirmation to requester
  send_welcome_email(request_id)       — Welcome email with DID details
  send_failure_alert(request_id)       — Internal alert on permanent SNOW failure
  send_cf_alert(schedule_id, msg)      — Alert on call forward execution failure
"""
import logging
from celery import shared_task

from app.models.snow import SNOWRequest, RequestStatus
from app.models.call_forward import CallForwardSchedule
from app.models.app_config import AppConfig

logger = logging.getLogger(__name__)


def _email_enabled(key: str) -> bool:
    return AppConfig.get(key, "true") == "true"


# ═══════════════════════════════════════════════════════════════════════════════
# FULFILLMENT EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.notifications.send_fulfillment_email",
    queue="notifications",
    max_retries=3,
    default_retry_delay=30,
)
def send_fulfillment_email(self, request_id: int) -> dict:
    """
    Send the DID assignment confirmation email to the end-user.
    Controlled by SNOW_SEND_DID_EMAIL AppConfig flag.
    """
    if not _email_enabled("SNOW_SEND_DID_EMAIL"):
        return {"status": "disabled"}

    req = SNOWRequest.query.get(request_id)
    if not req or req.status != RequestStatus.FULFILLED:
        return {"status": "skipped"}

    try:
        from app.services.email_service import send_email
        app_name = AppConfig.get("APP_NAME", "Orbit")

        subject = f"Your Webex Calling DID has been assigned — {req.snow_number}"
        body    = _render_did_email(req, app_name)

        ok, msg = send_email(
            to      = req.requester_email,
            subject = subject,
            html    = body,
        )
        if not ok:
            raise RuntimeError(msg)

        logger.info(
            f"[Notify] DID confirmation sent to {req.requester_email} "
            f"for {req.snow_number}."
        )
        return {"status": "sent", "to": req.requester_email}

    except Exception as exc:
        logger.warning(f"[Notify] Fulfillment email failed: {exc}")
        raise self.retry(exc=exc)


def _render_did_email(req: SNOWRequest, app_name: str) -> str:
    return f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;">
      <div style="background:#1e40af;padding:24px 32px;border-radius:12px 12px 0 0;">
        <h2 style="color:#fff;margin:0;font-size:20px;">{app_name}</h2>
        <p style="color:#bfdbfe;margin:4px 0 0;font-size:13px;">
          Webex Calling Provisioning
        </p>
      </div>
      <div style="background:#f8fafc;padding:28px 32px;border:1px solid #e2e8f0;
                  border-top:none;border-radius:0 0 12px 12px;">
        <p style="font-size:15px;">Hello {req.requester_name or req.requester_email},</p>
        <p style="font-size:14px;color:#334155;">
          Your Webex Calling Direct Inward Dial number has been successfully assigned.
        </p>
        <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;
                    padding:16px 20px;margin:20px 0;">
          <table style="font-size:13px;color:#1e40af;border-collapse:collapse;width:100%;">
            <tr>
              <td style="padding:4px 0;font-weight:700;width:40%;">Request #</td>
              <td style="padding:4px 0;">{req.snow_number}</td>
            </tr>
            <tr>
              <td style="padding:4px 0;font-weight:700;">DID Number</td>
              <td style="padding:4px 0;font-family:monospace;">{req.assigned_did}</td>
            </tr>
            <tr>
              <td style="padding:4px 0;font-weight:700;">Extension</td>
              <td style="padding:4px 0;font-family:monospace;">
                {req.assigned_extension or 'N/A'}
              </td>
            </tr>
          </table>
        </div>
        <p style="font-size:13px;color:#64748b;">
          Your new number is already active in Webex Calling.
          If you have any questions, please raise a new ticket in ServiceNow.
        </p>
        <p style="font-size:12px;color:#94a3b8;border-top:1px solid #e2e8f0;
                  padding-top:16px;margin-top:24px;">
          This email was sent automatically by {app_name}.
          Please do not reply.
        </p>
      </div>
    </div>
    """


# ═══════════════════════════════════════════════════════════════════════════════
# WELCOME EMAIL
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.notifications.send_welcome_email",
    queue="notifications",
    max_retries=3,
    default_retry_delay=30,
)
def send_welcome_email(self, request_id: int) -> dict:
    """
    Send an onboarding welcome email. Controlled by SNOW_SEND_WELCOME_EMAIL.
    """
    if not _email_enabled("SNOW_SEND_WELCOME_EMAIL"):
        return {"status": "disabled"}

    req = SNOWRequest.query.get(request_id)
    if not req:
        return {"status": "not_found"}

    try:
        from app.services.email_service import send_email
        app_name = AppConfig.get("APP_NAME", "Orbit")

        subject = f"Welcome to Webex Calling — {app_name}"
        body    = f"""
        <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;
                    padding:24px;background:#f8fafc;border-radius:12px;">
          <h2 style="color:#1e40af;">Welcome, {req.requester_name or req.requester_email}!</h2>
          <p>Your Webex Calling account has been fully provisioned.</p>
          <p>Your assigned DID: <strong style="font-family:monospace;">
            {req.assigned_did}</strong></p>
          <p style="font-size:12px;color:#94a3b8;">
            Sent by {app_name} — do not reply.
          </p>
        </div>
        """
        ok, msg = send_email(to=req.requester_email, subject=subject, html=body)
        if not ok:
            raise RuntimeError(msg)

        return {"status": "sent"}
    except Exception as exc:
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# FAILURE ALERT
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.notifications.send_failure_alert",
    queue="notifications",
    max_retries=2,
)
def send_failure_alert(request_id: int) -> dict:
    """
    Send an internal failure alert to all superadmin email addresses
    when a SNOW request permanently fails.
    """
    req = SNOWRequest.query.get(request_id)
    if not req:
        return {"status": "not_found"}

    try:
        from app.services.email_service import send_email
        from app.models.user import User, UserRole
        app_name = AppConfig.get("APP_NAME", "Orbit")

        admins = User.query.filter_by(
            role=UserRole.SUPERADMIN, is_active=True
        ).all()
        recipients = [u.email for u in admins if u.email]
        if not recipients:
            return {"status": "no_recipients"}

        subject = f"[{app_name}] SNOW Request {req.snow_number} FAILED"
        body    = f"""
        <div style="font-family:system-ui,sans-serif;max-width:600px;
                    background:#fff3cd;border:1px solid #ffc107;
                    border-radius:8px;padding:24px;">
          <h3 style="color:#856404;">⚠ SNOW Request Permanently Failed</h3>
          <p><strong>Request:</strong> {req.snow_number}</p>
          <p><strong>Requester:</strong> {req.requester_email}</p>
          <p><strong>Retry Count:</strong> {req.retry_count}</p>
          <p><strong>Failure Reason:</strong><br/>
             <code style="font-size:12px;">{req.failure_reason or 'Unknown'}</code>
          </p>
          <p style="font-size:12px;color:#6c757d;">
            Please review in {app_name} → ServiceNow → Failed Requests.
          </p>
        </div>
        """
        for recipient in recipients:
            send_email(to=recipient, subject=subject, html=body)

        logger.warning(
            f"[Notify] Failure alert sent to {len(recipients)} admin(s) "
            f"for {req.snow_number}."
        )
        return {"status": "sent", "recipients": recipients}

    except Exception as exc:
        logger.error(f"[Notify] Failure alert error: {exc}")
        return {"status": "error", "error": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════════
# CALL FORWARD ALERT
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.notifications.send_cf_alert",
    queue="notifications",
)
def send_cf_alert(schedule_id: int, error_message: str) -> dict:
    """
    Send an internal alert when a call forward apply/revert fails.
    """
    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        return {"status": "not_found"}

    try:
        from app.services.email_service import send_email
        from app.models.user import User, UserRole
        app_name   = AppConfig.get("APP_NAME", "Orbit")
        admins     = User.query.filter_by(role=UserRole.SUPERADMIN, is_active=True).all()
        recipients = [u.email for u in admins if u.email]

        subject = f"[{app_name}] Call Forward Failure — {schedule.name}"
        body    = f"""
        <div style="font-family:system-ui,sans-serif;max-width:600px;
                    background:#f8d7da;border:1px solid #f5c2c7;
                    border-radius:8px;padding:24px;">
          <h3 style="color:#842029;">Call Forward Execution Failed</h3>
          <p><strong>Schedule:</strong> {schedule.name}</p>
          <p><strong>Entity:</strong> {schedule.webex_entity_name or schedule.webex_entity_id}</p>
          <p><strong>Error:</strong><br/>
             <code style="font-size:12px;">{error_message}</code>
          </p>
        </div>
        """
        for recipient in recipients:
            send_email(to=recipient, subject=subject, html=body)

        return {"status": "sent"}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
