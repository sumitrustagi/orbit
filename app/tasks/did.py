"""
Celery task: sync DID pool assignment state from Webex API.
Runs hourly via Celery Beat and on-demand when admin clicks Refresh.
"""
import logging
from datetime import datetime, timezone

from app.extensions import celery, db
from app.models.did import DIDPool, DIDAssignment, DIDStatus, AssignmentType
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.did.sync_all_did_pools",
             bind=True, max_retries=2, default_retry_delay=120)
def sync_all_did_pools(self):
    """Sync all active DID pools against Webex API."""
    pools = DIDPool.query.filter_by(is_active=True).all()
    results = []
    for pool in pools:
        try:
            result = sync_pool(pool.id)
            results.append(result)
        except Exception as exc:
            logger.error(f"[DIDSync] Pool {pool.id} failed: {exc}")
    return results


@celery.task(name="app.tasks.did.sync_pool",
             bind=True, max_retries=3, default_retry_delay=60)
def sync_pool(self, pool_id: int) -> dict:
    """
    Sync a single DID pool:
    1. Generate all numbers in the range
    2. Check each against Webex assigned numbers
    3. Update DIDAssignment rows accordingly
    """
    from app.services.webex_service import get_webex_client
    from app.services.did_service import generate_e164_range

    pool = DIDPool.query.get(pool_id)
    if not pool:
        return {"error": f"Pool {pool_id} not found"}

    try:
        webex       = get_webex_client()
        org_numbers = {
            n.phone_number: n
            for n in webex.org.numbers
            if hasattr(n, "phone_number") and n.phone_number
        }

        all_numbers = generate_e164_range(pool.range_start, pool.range_end)
        updated = assigned = available = 0

        for number in all_numbers:
            assignment = DIDAssignment.query.filter_by(
                pool_id=pool_id, number=number
            ).first()

            if assignment is None:
                assignment = DIDAssignment(pool_id=pool_id, number=number)
                db.session.add(assignment)

            if number in org_numbers:
                wxc_num = org_numbers[number]
                owner   = getattr(wxc_num, "owner", None)
                a_type  = _map_owner_type(getattr(wxc_num, "owner_type", ""))

                if assignment.status != DIDStatus.ASSIGNED:
                    assignment.status          = DIDStatus.ASSIGNED
                    assignment.assignment_type = a_type
                    assignment.assigned_to_name  = (
                        getattr(owner, "display_name", "")
                        if owner else getattr(wxc_num, "owner_name", "")
                    )
                    assignment.assigned_to_email = (
                        getattr(owner, "email", "") if owner else ""
                    )
                    assignment.assigned_to_id    = (
                        getattr(owner, "id", "") if owner else ""
                    )
                assigned += 1
            else:
                if assignment.status != DIDStatus.AVAILABLE:
                    assignment.release()
                available += 1

            updated += 1

        pool.last_synced_at = datetime.now(timezone.utc)
        db.session.commit()

        logger.info(
            f"[DIDSync] Pool '{pool.name}': "
            f"{assigned} assigned, {available} available, {updated} total."
        )
        return {
            "pool_id":   pool_id,
            "name":      pool.name,
            "assigned":  assigned,
            "available": available,
            "total":     updated,
        }

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[DIDSync] Pool {pool_id} sync error: {exc}")
        raise self.retry(exc=exc)


def _map_owner_type(owner_type: str) -> AssignmentType:
    mapping = {
        "PEOPLE":           AssignmentType.USER,
        "PLACE":            AssignmentType.WORKSPACE,
        "AUTO_ATTENDANT":   AssignmentType.AUTO_ATTENDANT,
        "HUNT_GROUP":       AssignmentType.HUNT_GROUP,
        "CALL_QUEUE":       AssignmentType.CALL_QUEUE,
        "VIRTUAL_LINE":     AssignmentType.VIRTUAL_EXTENSION,
    }
    return mapping.get(owner_type.upper(), AssignmentType.UNASSIGNED)
