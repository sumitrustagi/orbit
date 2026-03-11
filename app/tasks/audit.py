"""
Celery task: purge AuditLog entries older than AUDIT_LOG_RETENTION_DAYS.
Runs nightly at 02:00 via Celery Beat (configured in config.py CELERYBEAT_SCHEDULE).
"""
from datetime import datetime, timezone, timedelta
import logging

from app.extensions import celery, db
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.audit.purge_old_audit_logs", bind=True,
             max_retries=3, default_retry_delay=300)
def purge_old_audit_logs(self):
    """
    Delete AuditLog rows older than AUDIT_LOG_RETENTION_DAYS (default 120).
    Runs in batches of 500 to avoid long-running deletes.
    """
    from flask import current_app

    retention_days = current_app.config.get("AUDIT_LOG_RETENTION_DAYS", 120)
    cutoff         = datetime.now(timezone.utc) - timedelta(days=retention_days)
    batch_size     = 500
    total_deleted  = 0

    try:
        while True:
            # Fetch IDs in batches to keep delete statements small
            old_ids = (
                db.session.query(AuditLog.id)
                .filter(AuditLog.timestamp < cutoff)
                .limit(batch_size)
                .all()
            )
            if not old_ids:
                break

            ids = [row.id for row in old_ids]
            deleted = (
                db.session.query(AuditLog)
                .filter(AuditLog.id.in_(ids))
                .delete(synchronize_session=False)
            )
            db.session.commit()
            total_deleted += deleted
            logger.info(f"[AuditPurge] Deleted {deleted} records in this batch.")

        logger.info(
            f"[AuditPurge] Complete. Total deleted: {total_deleted} "
            f"(retention: {retention_days} days, cutoff: {cutoff.date()})"
        )

        # Write a system audit log entry for the purge itself
        AuditLog.write(
            action="AUDIT_PURGE",
            username="system",
            resource_type="audit_log",
            resource_name=f"Purged {total_deleted} records older than {cutoff.date()}",
            status="success",
        )
        return {"deleted": total_deleted, "cutoff": cutoff.isoformat()}

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[AuditPurge] Failed: {exc}")
        raise self.retry(exc=exc)
