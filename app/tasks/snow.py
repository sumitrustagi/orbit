"""
Celery tasks for ServiceNow fulfillment.
"""
import logging
from app.extensions import celery, db
from app.models.snow import SNOWRequest, RequestStatus
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


@celery.task(
    name="app.tasks.snow.fulfill_snow_request",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def fulfill_snow_request(self, snow_request_id: int):
    """
    Asynchronously fulfill a ServiceNow request.

    Called immediately after webhook ingestion.
    Retries up to 3 times with a 60-second back-off on transient errors.
    """
    from app.services.snow_fulfillment_service import process_snow_request

    logger.info(f"[SNOWTask] Starting fulfillment for SNOWRequest.id={snow_request_id}")

    try:
        ok, msg = process_snow_request(snow_request_id)

        if ok:
            logger.info(
                f"[SNOWTask] Fulfillment SUCCESS for id={snow_request_id}: {msg}"
            )
        else:
            logger.error(
                f"[SNOWTask] Fulfillment FAILED for id={snow_request_id}: {msg}"
            )

        return {"success": ok, "message": msg, "snow_request_id": snow_request_id}

    except Exception as exc:
        logger.error(
            f"[SNOWTask] Unhandled exception for id={snow_request_id}: {exc}"
        )
        db.session.rollback()

        try:
            req = SNOWRequest.query.get(snow_request_id)
            if req and req.status not in (
                RequestStatus.FULFILLED, RequestStatus.FAILED
            ):
                req.add_log(
                    f"Celery task error (attempt {self.request.retries + 1}): {exc}"
                )
                db.session.commit()
        except Exception:
            pass

        raise self.retry(exc=exc)


@celery.task(
    name="app.tasks.snow.retry_failed_requests",
    bind=True,
    max_retries=1,
)
def retry_failed_requests(self):
    """
    Nightly task — find FAILED requests less than 7 days old and retry them.
    Prevents permanent failure from transient SNOW / Webex API issues.
    """
    from datetime import datetime, timezone, timedelta

    cutoff   = datetime.now(timezone.utc) - timedelta(days=7)
    failed   = (
        SNOWRequest.query
        .filter_by(status=RequestStatus.FAILED)
        .filter(SNOWRequest.created_at >= cutoff)
        .filter(SNOWRequest.retry_count < 3)
        .all()
    )

    queued = 0
    for req in failed:
        req.retry_count = (req.retry_count or 0) + 1
        req.transition(RequestStatus.PENDING)
        req.add_log(f"Nightly auto-retry #{req.retry_count}.")
        db.session.commit()

        fulfill_snow_request.delay(req.id)
        queued += 1

    logger.info(f"[SNOWRetry] Queued {queued} failed requests for retry.")
    return {"queued": queued}


@celery.task(
    name="app.tasks.snow.sync_pending_requests",
    bind=True,
    max_retries=2,
)
def sync_pending_requests(self):
    """
    Hourly task — find PENDING requests older than 5 minutes and re-queue them.
    Catches requests that were ingested but whose Celery task was lost
    (e.g. worker restart during processing).
    """
    from datetime import datetime, timezone, timedelta

    cutoff  = datetime.now(timezone.utc) - timedelta(minutes=5)
    pending = (
        SNOWRequest.query
        .filter_by(status=RequestStatus.PENDING)
        .filter(SNOWRequest.created_at <= cutoff)
        .all()
    )

    queued = 0
    for req in pending:
        req.add_log("Re-queued by sync_pending_requests task.")
        db.session.commit()
        fulfill_snow_request.delay(req.id)
        queued += 1

    logger.info(f"[SNOWSync] Re-queued {queued} stale pending requests.")
    return {"queued": queued}
