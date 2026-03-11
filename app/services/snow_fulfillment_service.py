"""
ServiceNow fulfillment orchestrator.

For each incoming SNOW request this service:
  1. Parses and validates the payload
  2. Resolves the target user in Webex
  3. Picks and provisions a DID from the configured pool
  4. Updates the SNOW request item (fulfilled / failed)
  5. Sends welcome + DID assignment emails
  6. Writes a full audit trail
  7. Updates the SNOWRequest DB record throughout

All heavy operations run inside the Celery task (snow.py),
so this module is purely synchronous orchestration logic.
"""
import logging
from datetime import datetime, timezone
from typing import Tuple

from app.extensions import db
from app.models.snow import SNOWRequest, RequestStatus
from app.models.audit import AuditLog
from app.models.app_config import AppConfig
from app.models.did import DIDPool
from app.services import did_provision_service as did_svc
from app.services import snow_service as snow_api
from app.services.email_service import send_welcome_email, send_did_assignment_email
from app.services.webex_service import get_webex_client

logger = logging.getLogger(__name__)


def process_snow_request(snow_request_id: int) -> Tuple[bool, str]:
    """
    Main fulfillment entry point. Called by the Celery task with the
    SNOWRequest DB primary key.

    Returns (success, message).
    """
    req = SNOWRequest.query.get(snow_request_id)
    if not req:
        return False, f"SNOWRequest {snow_request_id} not found in DB."

    # Guard against double-processing
    if req.status in (RequestStatus.FULFILLED, RequestStatus.FAILED):
        return False, f"Request {req.snow_number} already {req.status.value}."

    req.transition(RequestStatus.PROCESSING)
    req.add_log("Fulfillment started by Orbit automation.")

    try:
        # ── Step 1: Resolve configuration ───────────────────────────────
        pool_id = int(AppConfig.get("DEFAULT_DID_POOL_ID", "0") or 0)
        if req.requested_did_pool_id:
            pool_id = req.requested_did_pool_id

        pool = DIDPool.query.get(pool_id) if pool_id else None
        if not pool or not pool.is_active:
            return _fail(req, "No active DID pool configured for auto-fulfillment.")

        # ── Step 2: Resolve Webex user ──────────────────────────────────
        user_email = req.requester_email
        if not user_email:
            return _fail(req, "Requester email is missing from the SNOW request.")

        req.add_log(f"Resolving Webex user: {user_email}")

        try:
            webex  = get_webex_client()
            person = webex.org.get_person_by_email(user_email)
        except Exception as exc:
            return _fail(req, f"Webex API error while looking up user: {exc}")

        if not person:
            return _fail(
                req,
                f"No Webex user found with email '{user_email}'. "
                f"Ensure the user exists in Webex Control Hub before provisioning."
            )

        req.webex_person_id = person.id
        req.add_log(f"Webex user resolved: {person.display_name} ({person.id})")

        # ── Step 3: Provision DID ────────────────────────────────────────
        req.add_log(f"Provisioning DID from pool '{pool.name}'…")

        ok, msg, assigned_number = did_svc.auto_provision_did(
            pool_id=pool.id,
            user_email=user_email,
            username="snow_automation",
            snow_request_id=req.snow_number,
        )

        if not ok or not assigned_number:
            return _fail(req, msg)

        req.assigned_did  = assigned_number
        req.add_log(f"DID {assigned_number} provisioned successfully.")

        # ── Step 4: Resolve extension (if available) ─────────────────────
        extension = ""
        try:
            person_detail = webex.org.get_person_by_id(person.id)
            extension     = getattr(person_detail, "extension", "") or ""
        except Exception:
            pass

        req.assigned_extension = extension

        # ── Step 5: Update SNOW ──────────────────────────────────────────
        req.add_log("Updating ServiceNow request…")

        if req.snow_sys_id:
            snow_ok, snow_msg = snow_api.fulfill_request(
                sys_id=req.snow_sys_id,
                did_number=assigned_number,
                extension=extension,
                table=_snow_table(req.snow_number),
            )
            if snow_ok:
                req.add_log("ServiceNow request marked as fulfilled.")
            else:
                req.add_log(f"⚠ SNOW update warning: {snow_msg}")
        else:
            req.add_log("⚠ No SNOW SysID — skipping SNOW state update.")

        # ── Step 6: Send emails ──────────────────────────────────────────
        _send_provisioning_emails(req, person, assigned_number, extension)

        # ── Step 7: Finalise ─────────────────────────────────────────────
        req.transition(RequestStatus.FULFILLED)
        req.fulfilled_at = datetime.now(timezone.utc)
        req.add_log(f"Fulfillment complete. DID: {assigned_number}.")

        AuditLog.write(
            action="SNOW_REQUEST_COMPLETED",
            username="snow_automation",
            resource_type="snow_request",
            resource_id=req.id,
            resource_name=req.snow_number,
            payload_after={
                "did":       assigned_number,
                "extension": extension,
                "email":     user_email,
                "pool":      pool.name,
            },
            status="success",
        )

        logger.info(
            f"[SNOWFulfill] {req.snow_number} fulfilled → "
            f"{assigned_number} for {user_email}"
        )
        return True, f"DID {assigned_number} assigned to {user_email}."

    except Exception as exc:
        logger.error(f"[SNOWFulfill] Unhandled error for {req.snow_number}: {exc}")
        return _fail(req, f"Unexpected error: {exc}")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fail(req: SNOWRequest, reason: str) -> Tuple[bool, str]:
    """Mark a SNOWRequest as failed, update SNOW, and write audit log."""
    req.transition(RequestStatus.FAILED)
    req.failure_reason = reason
    req.add_log(f"❌ FAILED: {reason}")

    if req.snow_sys_id:
        snow_api.fail_request(
            sys_id=req.snow_sys_id,
            reason=reason,
            table=_snow_table(req.snow_number),
        )

    AuditLog.write(
        action="SNOW_REQUEST_FAILED",
        username="snow_automation",
        resource_type="snow_request",
        resource_id=req.id,
        resource_name=req.snow_number,
        status="failure",
        status_detail=reason,
    )

    logger.error(f"[SNOWFulfill] {req.snow_number} FAILED: {reason}")
    return False, reason


def _snow_table(snow_number: str) -> str:
    """Determine SNOW table from request number prefix."""
    return "sc_req_item" if snow_number.upper().startswith("RITM") else "sc_request"


def _send_provisioning_emails(req, person, did: str, extension: str) -> None:
    """Send welcome and/or DID assignment emails based on AppConfig flags."""
    try:
        send_welcome  = AppConfig.get("SNOW_SEND_WELCOME_EMAIL", "true").lower() == "true"
        send_did_mail = AppConfig.get("SNOW_SEND_DID_EMAIL",     "true").lower() == "true"

        if send_welcome:
            send_welcome_email(
                to_email=req.requester_email,
                user_display_name=person.display_name,
                did=did,
                extension=extension,
            )
            req.add_log(f"Welcome email sent to {req.requester_email}.")

        if send_did_mail and not send_welcome:
            # Only send DID-specific email if welcome isn't already covering it
            send_did_assignment_email(
                to_email=req.requester_email,
                user_display_name=person.display_name,
                did=did,
                extension=extension,
            )
            req.add_log(f"DID assignment email sent to {req.requester_email}.")

    except Exception as exc:
        req.add_log(f"⚠ Email send failed (non-fatal): {exc}")
        logger.warning(f"[SNOWFulfill] Email error: {exc}")


def validate_webhook_signature(payload: bytes, signature: str) -> bool:
    """
    Validate HMAC-SHA256 webhook signature.

    ServiceNow sends X-Orbit-Signature: sha256=<hex_digest>
    Computed over the raw request body with SNOW_WEBHOOK_SECRET as key.
    """
    import hmac
    import hashlib

    secret = AppConfig.get("SNOW_WEBHOOK_SECRET", "")
    if not secret:
        # If no secret configured, skip verification (log a warning)
        logger.warning(
            "[SNOW Webhook] No webhook secret configured — "
            "accepting all requests. Configure SNOW_WEBHOOK_SECRET in System Settings."
        )
        return True

    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected, signature or "")


def parse_snow_payload(data: dict) -> dict:
    """
    Normalise a SNOW webhook payload into a flat dict for SNOWRequest creation.

    Supports both:
      - Full webhook body from SNOW REST integration
      - Simplified custom payload (just email + request number)
    """
    # Direct mapping
    snow_number = (
        data.get("number") or
        data.get("ritm_number") or
        data.get("request_number") or
        ""
    ).strip().upper()

    snow_sys_id = (
        data.get("sys_id") or
        data.get("sysId") or
        ""
    ).strip()

    # Requester — try nested requested_for object first, then flat fields
    req_for     = data.get("requested_for") or {}
    req_email   = (
        (req_for.get("email") if isinstance(req_for, dict) else None) or
        data.get("requester_email") or
        data.get("requested_for_email") or
        ""
    ).strip().lower()

    req_name    = (
        (req_for.get("display_value") if isinstance(req_for, dict) else None) or
        data.get("requester_name") or
        data.get("requested_for_name") or
        ""
    ).strip()

    # Optional pool override
    pool_id_raw = data.get("did_pool_id") or data.get("pool_id") or ""
    try:
        pool_id = int(pool_id_raw) if pool_id_raw else None
    except (ValueError, TypeError):
        pool_id = None

    short_desc = (
        data.get("short_description") or
        data.get("description") or
        f"Webex Calling provisioning for {req_email}"
    ).strip()

    return {
        "snow_number":           snow_number,
        "snow_sys_id":           snow_sys_id,
        "requester_email":       req_email,
        "requester_name":        req_name,
        "short_description":     short_desc,
        "requested_did_pool_id": pool_id,
        "raw_payload":           data,
    }
