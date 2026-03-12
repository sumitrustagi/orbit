"""
Celery maintenance tasks.

Tasks:
  release_quarantine_dids()  — Move DIDs past quarantine_until → AVAILABLE
  purge_old_audit_logs()     — Delete audit entries older than retention window
  health_ping()              — Write a heartbeat entry to confirm workers are live
"""
import logging
from datetime import datetime, timezone, timedelta

from celery import shared_task

from app.extensions import db
from app.models.audit import AuditLog
from app.models.did import DID, DIDStatus
from app.models.app_config import AppConfig

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# QUARANTINE RELEASE
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.maintenance.release_quarantine_dids",
    queue="maintenance",
)
def release_quarantine_dids() -> dict:
    """
    Beat task — runs every hour at :05.

    Finds all DIDs in QUARANTINE status whose quarantine_until timestamp
    is in the past and moves them back to AVAILABLE.
    """
    now = datetime.now(timezone.utc)
    expired = (
        DID.query
        .filter(
            DID.status == DIDStatus.QUARANTINE,
            DID.quarantine_until <= now,
        )
        .all()
    )

    released = 0
    for did in expired:
        did.status           = DIDStatus.AVAILABLE
        did.quarantine_until = None
        released += 1

    if released:
        db.session.commit()
        AuditLog.write(
            action        = "QUARANTINE_RELEASE",
            username      = "celery",
            user_role     = "scheduler",
            resource_type = "did",
            payload_after = {"released_count": released},
            status        = "success",
        )
        logger.info(f"[Maintenance] Released {released} DID(s) from quarantine.")
    else:
        logger.debug("[Maintenance] Quarantine release: no expired DIDs found.")

    return {"released": released}


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT LOG PURGE
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.maintenance.purge_old_audit_logs",
    queue="maintenance",
)
def purge_old_audit_logs() -> dict:
    """
    Beat task — runs nightly at 02:30.

    Deletes AuditLog entries older than AUDIT_RETENTION_DAYS (default 365).
    The retention window is read from AppConfig at runtime so it can be
    changed without redeploying.
    """
    retention_days = int(AppConfig.get("AUDIT_RETENTION_DAYS", "365"))
    cutoff         = datetime.now(timezone.utc) - timedelta(days=retention_days)

    deleted = AuditLog.query.filter(AuditLog.created_at < cutoff).delete()
    db.session.commit()

    if deleted:
        logger.info(
            f"[Maintenance] Purged {deleted} audit log entries "
            f"older than {retention_days} days."
        )
    return {"deleted": deleted, "retention_days": retention_days}


# ═══════════════════════════════════════════════════════════════════════════════
# HEALTH PING
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.maintenance.health_ping",
    queue="maintenance",
)
def health_ping() -> dict:
    """
    Beat task — runs every 10 minutes.

    Writes a lightweight HEALTH_PING entry to AuditLog so the Task Monitor
    can confirm workers and beat are alive. Also records Celery worker
    stats if available.
    """
    now = datetime.now(timezone.utc)

    # Attempt to get live worker stats via the Celery inspect API
    worker_count = 0
    try:
        from app.tasks import celery_app
        inspector    = celery_app.control.inspect(timeout=2)
        stats        = inspector.stats() or {}
        worker_count = len(stats)
    except Exception:
        pass

    AuditLog.write(
        action        = "HEALTH_PING",
        username      = "celery",
        user_role     = "scheduler",
        resource_type = "system",
        resource_name = "celery_beat",
        payload_after = {
            "worker_count": worker_count,
            "timestamp":    now.isoformat(),
        },
        status="success",
    )

    logger.debug(f"[Maintenance] Health ping — {worker_count} worker(s) active.")
    return {"workers": worker_count, "ts": now.isoformat()}
