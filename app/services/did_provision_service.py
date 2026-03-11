"""
DID provisioning service.

Orchestrates:
  1. Reserving a DID from the pool (atomic SELECT FOR UPDATE)
  2. Assigning the number to the Webex entity via wxcadm
  3. Updating the DIDAssignment record on success
  4. Writing the audit log entry
  5. Triggering the welcome / DID assignment email

Also handles manual assign and release flows from the admin UI.
"""
import logging
from datetime import datetime, timezone
from typing import Tuple

from app.extensions import db
from app.models.did import (
    DIDPool, DIDAssignment, DIDStatus, AssignmentType
)
from app.models.audit import AuditLog
from app.services.webex_service import get_webex_client
from app.services.did_service import next_available_number, validate_e164

logger = logging.getLogger(__name__)


# ── Auto-provision (called from SNOW fulfillment task) ─────────────────────────

def auto_provision_did(
    pool_id:       int,
    user_email:    str,
    user_id:       int | None = None,
    username:      str = "system",
    snow_request_id: str = "",
) -> Tuple[bool, str, str | None]:
    """
    Automatically pick and assign the next available DID from a pool.

    Returns (success, message, assigned_number_or_None).
    """
    pool = DIDPool.query.get(pool_id)
    if not pool or not pool.is_active:
        return False, f"Pool {pool_id} not found or inactive.", None

    # Atomically reserve a number
    number = next_available_number(pool_id)
    if not number:
        return False, f"No available DIDs in pool '{pool.name}'.", None

    try:
        webex  = get_webex_client()
        person = webex.org.get_person_by_email(user_email)

        if not person:
            # Roll back reservation
            _release_reservation(number)
            return False, f"Webex user '{user_email}' not found.", None

        # Assign via wxcadm
        person.direct_number = number
        person.push()

        # Update DB assignment record
        assignment = DIDAssignment.query.filter_by(
            pool_id=pool_id, number=number
        ).first()

        if assignment:
            assignment.assign_to(
                entity_id=person.id,
                entity_name=person.display_name or user_email,
                entity_email=user_email,
                assignment_type=AssignmentType.USER,
            )
            assignment.notes = f"Auto-provisioned via SNOW request {snow_request_id}" \
                               if snow_request_id else "Auto-provisioned"
            db.session.commit()

        AuditLog.write(
            action="DID_ASSIGNED",
            user_id=user_id,
            username=username,
            resource_type="did_assignment",
            resource_id=assignment.id if assignment else "",
            resource_name=number,
            payload_after={
                "number":   number,
                "assigned_to": user_email,
                "pool":     pool.name,
                "snow_id":  snow_request_id,
            },
            status="success",
        )
        logger.info(
            f"[DIDProvision] Assigned {number} to {user_email} "
            f"from pool '{pool.name}' (SNOW: {snow_request_id})"
        )
        return True, f"DID {number} assigned to {user_email}.", number

    except Exception as exc:
        db.session.rollback()
        _release_reservation(number)
        logger.error(f"[DIDProvision] Auto-provision failed: {exc}")

        AuditLog.write(
            action="DID_ASSIGNED",
            username=username,
            resource_type="did_assignment",
            resource_name=number,
            status="failure",
            status_detail=str(exc),
        )
        return False, f"Provisioning failed: {exc}", None


# ── Manual assign (admin UI) ───────────────────────────────────────────────────

def manual_assign_did(
    number:          str,
    assignment_type: str,
    entity_id:       str,
    notes:           str = "",
    admin_user_id:   int | None = None,
    admin_username:  str = "admin",
) -> Tuple[bool, str]:
    """
    Manually assign a DID to any Webex entity type from the admin UI.
    """
    assignment = DIDAssignment.query.filter_by(number=number).first()
    if not assignment:
        return False, f"DID {number} not found in any pool."

    if assignment.status == DIDStatus.ASSIGNED:
        return False, f"DID {number} is already assigned to {assignment.assigned_to_name}."

    a_type = AssignmentType(assignment_type) \
             if assignment_type in [e.value for e in AssignmentType] \
             else AssignmentType.USER

    try:
        webex        = get_webex_client()
        entity_name  = entity_id
        entity_email = ""

        # Resolve the entity from Webex by ID or email
        if a_type == AssignmentType.USER:
            entity = webex.org.get_person_by_id(entity_id) \
                     or webex.org.get_person_by_email(entity_id)
            if not entity:
                return False, f"Webex user '{entity_id}' not found."
            entity_name  = entity.display_name or entity_id
            entity_email = getattr(entity, "email", entity_id) or entity_id
            entity.direct_number = number
            entity.push()

        elif a_type == AssignmentType.WORKSPACE:
            entity = webex.org.get_workspace_by_id(entity_id)
            if not entity:
                return False, f"Webex workspace '{entity_id}' not found."
            entity_name = getattr(entity, "display_name", entity_id)

        # Update DB
        payload_before = assignment.to_dict()
        assignment.assign_to(
            entity_id=entity_id,
            entity_name=entity_name,
            entity_email=entity_email,
            assignment_type=a_type,
        )
        assignment.notes = notes
        db.session.commit()

        AuditLog.write(
            action="DID_ASSIGNED",
            user_id=admin_user_id,
            username=admin_username,
            resource_type="did_assignment",
            resource_id=assignment.id,
            resource_name=number,
            payload_before=payload_before,
            payload_after=assignment.to_dict(),
            status="success",
        )
        return True, f"DID {number} assigned to {entity_name}."

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[DIDProvision] Manual assign failed: {exc}")
        return False, f"Assignment failed: {exc}"


# ── Release (admin UI) ─────────────────────────────────────────────────────────

def release_did(
    number:         str,
    admin_user_id:  int | None = None,
    admin_username: str = "admin",
) -> Tuple[bool, str]:
    """
    Release an assigned DID back to available status.
    Removes the number from the Webex entity if the entity is a USER.
    """
    assignment = DIDAssignment.query.filter_by(number=number).first()
    if not assignment:
        return False, f"DID {number} not found."

    if assignment.status == DIDStatus.AVAILABLE:
        return False, f"DID {number} is already available."

    payload_before = assignment.to_dict()

    try:
        # Attempt to remove from Webex (best-effort — don't fail if entity gone)
        if assignment.assignment_type == AssignmentType.USER and assignment.assigned_to_id:
            try:
                webex  = get_webex_client()
                person = webex.org.get_person_by_id(assignment.assigned_to_id)
                if person:
                    person.direct_number = ""
                    person.push()
            except Exception as exc:
                logger.warning(
                    f"[DIDProvision] Could not remove number from Webex: {exc}"
                )

        assignment.release()
        db.session.commit()

        AuditLog.write(
            action="DID_RELEASED",
            user_id=admin_user_id,
            username=admin_username,
            resource_type="did_assignment",
            resource_id=assignment.id,
            resource_name=number,
            payload_before=payload_before,
            payload_after=assignment.to_dict(),
            status="success",
        )
        return True, f"DID {number} released successfully."

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[DIDProvision] Release failed: {exc}")
        return False, f"Release failed: {exc}"


# ── Pool population (generates DIDAssignment rows for a new pool) ──────────────

def populate_pool(pool: DIDPool, admin_username: str = "system") -> Tuple[int, int]:
    """
    Generate DIDAssignment rows for every number in pool.range_start→range_end.
    Skips numbers that already have a row.
    Returns (created_count, skipped_count).
    """
    from app.services.did_service import generate_e164_range

    numbers  = generate_e164_range(pool.range_start, pool.range_end)
    created  = 0
    skipped  = 0

    existing = {
        row.number for row in
        DIDAssignment.query.filter_by(pool_id=pool.id)
        .with_entities(DIDAssignment.number).all()
    }

    for number in numbers:
        if number in existing:
            skipped += 1
            continue
        db.session.add(DIDAssignment(
            pool_id=pool.id,
            number=number,
            status=DIDStatus.AVAILABLE,
        ))
        created += 1

    db.session.commit()
    logger.info(
        f"[DIDProvision] Pool '{pool.name}': "
        f"created {created} numbers, skipped {skipped}."
    )
    return created, skipped


# ── Helpers ────────────────────────────────────────────────────────────────────

def _release_reservation(number: str) -> None:
    """Roll back a RESERVED status to AVAILABLE on provisioning failure."""
    try:
        a = DIDAssignment.query.filter_by(
            number=number, status=DIDStatus.RESERVED
        ).first()
        if a:
            a.status = DIDStatus.AVAILABLE
            db.session.commit()
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[DIDProvision] Could not release reservation for {number}: {exc}")
