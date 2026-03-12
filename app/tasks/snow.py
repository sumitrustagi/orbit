"""
Celery tasks for ServiceNow request processing.

Tasks:
  process_snow_request(request_id)   — Main fulfillment pipeline for one request
  retry_failed_requests()            — Beat: retry all retryable failed requests
  update_snow_ticket(request_id)     — Update RITM state in ServiceNow after fulfillment
"""
import logging
from datetime import datetime, timezone

from celery import shared_task
from celery.exceptions import MaxRetriesExceededError

from app.extensions import db
from app.models.snow import SNOWRequest, RequestStatus
from app.models.did  import DID, DIDPool, DIDStatus
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

# Maximum automatic retries before a request is permanently marked failed
MAX_AUTO_RETRIES = 5


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN FULFILLMENT PIPELINE
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.snow.process_snow_request",
    max_retries=MAX_AUTO_RETRIES,
    default_retry_delay=90,
    queue="snow",
    acks_late=True,
)
def process_snow_request(self, request_id: int) -> dict:
    """
    Fulfill a single SNOW request end-to-end:

    1. Load and validate the SNOWRequest
    2. Find an available DID in the requested pool
    3. Assign the DID in Webex Calling via the Webex service
    4. Update DID status → ASSIGNED
    5. Mark request → FULFILLED
    6. Update the SNOW RITM state via REST
    7. Send confirmation emails
    8. Write audit log

    On any failure the task retries with exponential back-off.
    After MAX_AUTO_RETRIES the request is marked FAILED.
    """
    req = SNOWRequest.query.get(request_id)
    if not req:
        logger.error(f"[SNOW] Request {request_id} not found — aborting.")
        return {"status": "not_found", "request_id": request_id}

    if req.status == RequestStatus.FULFILLED:
        logger.info(f"[SNOW] Request {request_id} already fulfilled — skipping.")
        return {"status": "already_fulfilled"}

    # Mark as processing
    req.status     = RequestStatus.PROCESSING
    req.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    try:
        # ── Step 1: Find available DID ────────────────────────────────────────
        pool = None
        if req.requested_pool_id:
            pool = DIDPool.query.get(req.requested_pool_id)

        did = _pick_available_did(
            pool_id=pool.id if pool else None,
            country=req.requested_country,
        )
        if not did:
            raise RuntimeError(
                f"No available DID found "
                f"(pool={pool.name if pool else 'any'}, "
                f"country={req.requested_country or 'any'})"
            )

        # ── Step 2: Assign in Webex ───────────────────────────────────────────
        from app.services.webex_service import assign_did_to_user
        webex_result = assign_did_to_user(
            user_email   = req.requester_email,
            did_number   = did.number,
            extension    = req.requested_extension or "",
        )

        # ── Step 3: Persist DID assignment ────────────────────────────────────
        did.status              = DIDStatus.ASSIGNED
        did.assigned_to_email   = req.requester_email
        did.assigned_to_name    = req.requester_name or ""
        did.assigned_at         = datetime.now(timezone.utc)
        did.snow_request_number = req.snow_number

        req.status              = RequestStatus.FULFILLED
        req.assigned_did        = did.number
        req.assigned_extension  = req.requested_extension or webex_result.get("extension","")
        req.fulfilled_at        = datetime.now(timezone.utc)
        req.failure_reason      = None
        db.session.commit()

        # ── Step 4: Update SNOW RITM ──────────────────────────────────────────
        update_snow_ticket.delay(request_id)

        # ── Step 5: Send emails ───────────────────────────────────────────────
        from app.tasks.notifications import send_fulfillment_email
        send_fulfillment_email.delay(request_id)

        # ── Step 6: Audit ─────────────────────────────────────────────────────
        AuditLog.write(
            action        = "SNOW_FULFILL",
            username      = "celery",
            user_role     = "scheduler",
            resource_type = "snow_request",
            resource_id   = req.id,
            resource_name = req.snow_number,
            payload_after = {
                "assigned_did":       did.number,
                "assigned_extension": req.assigned_extension,
            },
            status        = "success",
        )

        logger.info(
            f"[SNOW] ✓ Request {req.snow_number} fulfilled — "
            f"DID {did.number} → {req.requester_email}"
        )
        return {"status": "fulfilled", "did": did.number, "request": req.snow_number}

    except Exception as exc:
        db.session.rollback()
        req = SNOWRequest.query.get(request_id)
        if req:
            req.retry_count += 1
            req.updated_at   = datetime.now(timezone.utc)

            if self.request.retries >= MAX_AUTO_RETRIES - 1:
                req.status         = RequestStatus.FAILED
                req.failure_reason = str(exc)
                db.session.commit()
                AuditLog.write(
                    action        = "SNOW_FAIL",
                    username      = "celery",
                    resource_type = "snow_request",
                    resource_id   = req.id,
                    resource_name = req.snow_number,
                    payload_after = {"failure_reason": str(exc)},
                    status        = "failure",
                )
                logger.error(
                    f"[SNOW] ✗ Request {req.snow_number} permanently failed: {exc}"
                )
            else:
                req.status = RequestStatus.RETRYING
                db.session.commit()

        try:
            countdown = 90 * (2 ** self.request.retries)  # exponential back-off
            raise self.retry(exc=exc, countdown=min(countdown, 3600))
        except MaxRetriesExceededError:
            pass

        return {"status": "failed", "error": str(exc)}


def _pick_available_did(pool_id: int | None, country: str | None) -> DID | None:
    """Select and pessimistically lock the first available DID."""
    q = (
        DID.query
        .filter_by(status=DIDStatus.AVAILABLE)
        .with_for_update(skip_locked=True)
    )
    if pool_id:
        q = q.filter_by(pool_id=pool_id)
    if country:
        q = q.filter_by(country=country)

    return q.order_by(DID.id.asc()).first()


# ═══════════════════════════════════════════════════════════════════════════════
# SNOW TICKET UPDATE
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.snow.update_snow_ticket",
    max_retries=3,
    default_retry_delay=30,
    queue="snow",
)
def update_snow_ticket(self, request_id: int) -> dict:
    """
    Push fulfillment state back to ServiceNow via the REST API.
    Updates the RITM work notes and state code.
    """
    req = SNOWRequest.query.get(request_id)
    if not req:
        return {"status": "not_found"}

    try:
        from app.services.snow_service import update_ritm_state
        ok, msg = update_ritm_state(
            snow_number    = req.snow_number,
            state          = "fulfilled" if req.status == RequestStatus.FULFILLED else "failed",
            assigned_did   = req.assigned_did   or "",
            work_notes     = (
                f"DID {req.assigned_did} assigned to {req.requester_email}. "
                f"Extension: {req.assigned_extension or 'N/A'}."
                if req.status == RequestStatus.FULFILLED
                else f"Fulfillment failed: {req.failure_reason}"
            ),
        )
        if not ok:
            raise RuntimeError(msg)

        logger.info(f"[SNOW] RITM {req.snow_number} updated in ServiceNow.")
        return {"status": "ok", "snow_number": req.snow_number}

    except Exception as exc:
        logger.warning(f"[SNOW] RITM update failed for {req.snow_number}: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# BEAT: RETRY FAILED REQUESTS
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.snow.retry_failed_requests",
    queue="snow",
)
def retry_failed_requests() -> dict:
    """
    Beat task — runs every 5 minutes.
    Re-queues all SNOW requests in RETRYING status or FAILED with
    retry_count < MAX_AUTO_RETRIES.
    """
    candidates = SNOWRequest.query.filter(
        SNOWRequest.status.in_([RequestStatus.RETRYING, RequestStatus.FAILED]),
        SNOWRequest.retry_count < MAX_AUTO_RETRIES,
    ).all()

    queued = 0
    for req in candidates:
        process_snow_request.apply_async(
            args=[req.id],
            queue="snow",
            countdown=5,
        )
        queued += 1

    logger.info(f"[SNOW] Beat: re-queued {queued} retryable request(s).")
    return {"queued": queued}
