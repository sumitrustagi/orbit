"""
Celery tasks for Webex entity synchronisation.

Tasks:
  sync_webex_users()       — Sync all Webex Calling users to local cache
  sync_hunt_groups()       — Sync hunt groups
  sync_call_queues()       — Sync call queues
  sync_auto_attendants()   — Sync auto attendants
  full_sync()              — Trigger all four syncs sequentially (manual / CLI)
"""
import logging
from datetime import datetime, timezone

from celery import shared_task, chord

from app.extensions import db
from app.models.audit import AuditLog
from app.models.webex_cache import (
    WebexUserCache, WebexHuntGroupCache,
    WebexCallQueueCache, WebexAutoAttendantCache,
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# USERS
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.webex.sync_webex_users",
    queue="webex_sync",
    max_retries=3,
    default_retry_delay=60,
    bind=True,
)
def sync_webex_users(self) -> dict:
    """
    Fetch all Webex Calling users via the Webex API and upsert them
    into the local WebexUserCache table.

    Returns the count of records created / updated.
    """
    try:
        from app.services.webex_service import list_webex_users
        users = list_webex_users()

        created = updated = 0
        now     = datetime.now(timezone.utc)

        for u in users:
            row = WebexUserCache.query.filter_by(webex_id=u["id"]).first()
            if row is None:
                row = WebexUserCache(webex_id=u["id"])
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.display_name  = u.get("displayName", "")
            row.email         = u.get("emails", [""])[0]
            row.first_name    = u.get("firstName", "")
            row.last_name     = u.get("lastName", "")
            row.phone_numbers = u.get("phoneNumbers", [])
            row.extension     = u.get("extension", "")
            row.location_id   = u.get("locationId", "")
            row.location_name = u.get("locationName", "")
            row.synced_at     = now

        db.session.commit()

        AuditLog.write(
            action="WEBEX_SYNC", username="celery", user_role="scheduler",
            resource_type="webex_user_cache",
            payload_after={"created": created, "updated": updated},
            status="success",
        )
        logger.info(f"[Webex] User sync: +{created} created, ~{updated} updated.")
        return {"created": created, "updated": updated}

    except Exception as exc:
        logger.error(f"[Webex] User sync failed: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# HUNT GROUPS
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.webex.sync_hunt_groups",
    queue="webex_sync",
    max_retries=3,
    default_retry_delay=60,
    bind=True,
)
def sync_hunt_groups(self) -> dict:
    """Sync Webex Calling hunt groups to local cache."""
    try:
        from app.services.webex_service import list_hunt_groups
        groups  = list_hunt_groups()
        created = updated = 0
        now     = datetime.now(timezone.utc)

        for g in groups:
            row = WebexHuntGroupCache.query.filter_by(webex_id=g["id"]).first()
            if row is None:
                row = WebexHuntGroupCache(webex_id=g["id"])
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.name          = g.get("name", "")
            row.phone_number  = g.get("phoneNumber", "")
            row.extension     = g.get("extension", "")
            row.location_id   = g.get("locationId", "")
            row.location_name = g.get("locationName", "")
            row.synced_at     = now

        db.session.commit()
        logger.info(f"[Webex] Hunt group sync: +{created} / ~{updated}.")
        return {"created": created, "updated": updated}

    except Exception as exc:
        logger.error(f"[Webex] Hunt group sync failed: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# CALL QUEUES
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.webex.sync_call_queues",
    queue="webex_sync",
    max_retries=3,
    default_retry_delay=60,
    bind=True,
)
def sync_call_queues(self) -> dict:
    """Sync Webex Calling call queues to local cache."""
    try:
        from app.services.webex_service import list_call_queues
        queues  = list_call_queues()
        created = updated = 0
        now     = datetime.now(timezone.utc)

        for q in queues:
            row = WebexCallQueueCache.query.filter_by(webex_id=q["id"]).first()
            if row is None:
                row = WebexCallQueueCache(webex_id=q["id"])
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.name          = q.get("name", "")
            row.phone_number  = q.get("phoneNumber", "")
            row.extension     = q.get("extension", "")
            row.location_id   = q.get("locationId", "")
            row.location_name = q.get("locationName", "")
            row.synced_at     = now

        db.session.commit()
        logger.info(f"[Webex] Call queue sync: +{created} / ~{updated}.")
        return {"created": created, "updated": updated}

    except Exception as exc:
        logger.error(f"[Webex] Call queue sync failed: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# AUTO ATTENDANTS
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.webex.sync_auto_attendants",
    queue="webex_sync",
    max_retries=3,
    default_retry_delay=60,
    bind=True,
)
def sync_auto_attendants(self) -> dict:
    """Sync Webex Calling auto attendants to local cache."""
    try:
        from app.services.webex_service import list_auto_attendants
        attendants = list_auto_attendants()
        created = updated = 0
        now     = datetime.now(timezone.utc)

        for a in attendants:
            row = WebexAutoAttendantCache.query.filter_by(webex_id=a["id"]).first()
            if row is None:
                row = WebexAutoAttendantCache(webex_id=a["id"])
                db.session.add(row)
                created += 1
            else:
                updated += 1

            row.name          = a.get("name", "")
            row.phone_number  = a.get("phoneNumber", "")
            row.extension     = a.get("extension", "")
            row.location_id   = a.get("locationId", "")
            row.location_name = a.get("locationName", "")
            row.synced_at     = now

        db.session.commit()
        logger.info(f"[Webex] Auto attendant sync: +{created} / ~{updated}.")
        return {"created": created, "updated": updated}

    except Exception as exc:
        logger.error(f"[Webex] Auto attendant sync failed: {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# FULL SYNC  (manual trigger / CLI)
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.webex.full_sync",
    queue="webex_sync",
)
def full_sync() -> dict:
    """
    Trigger all four Webex sync tasks sequentially.
    Used by flask admin sync-webex and the Task Monitor UI.
    """
    results = {}
    for task_fn, key in [
        (sync_webex_users,    "users"),
        (sync_hunt_groups,    "hunt_groups"),
        (sync_call_queues,    "call_queues"),
        (sync_auto_attendants,"auto_attendants"),
    ]:
        try:
            result      = task_fn.apply()
            results[key] = result.result
        except Exception as exc:
            results[key] = {"error": str(exc)}

    logger.info(f"[Webex] Full sync completed: {results}")
    return results
