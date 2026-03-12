"""
Call Forward service.

Responsible for:
  - Applying call forward settings to a Webex entity via wxcadm
  - Reverting call forward settings to the previously saved snapshot
  - Evaluating which schedules should be active RIGHT NOW
  - On-demand apply/revert (bypasses schedule window check)
"""
import logging
from datetime import datetime, timezone
from typing import Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.extensions import db
from app.models.call_forward import (
    CallForwardSchedule, ScheduleStatus, ForwardType, EntityType
)
from app.models.audit import AuditLog
from app.services.webex_service import get_webex_client

logger = logging.getLogger(__name__)


# ── Apply forward ─────────────────────────────────────────────────────────────

def apply_forward(
    schedule: CallForwardSchedule,
    triggered_by: str = "scheduler",
) -> Tuple[bool, str]:
    """
    Apply the call forward defined in `schedule` to the Webex entity.

    1. Fetches and snapshots the current Webex forward settings.
    2. Applies the new destination.
    3. Updates the schedule status in DB.
    4. Writes an audit log entry.

    Returns (success, message).
    """
    if schedule.status == ScheduleStatus.ACTIVE and not schedule.is_ondemand:
        return True, "Already active — skipping duplicate apply."

    try:
        webex  = get_webex_client()
        entity, snapshot = _resolve_entity(webex, schedule)

        if entity is None:
            return _mark_error(
                schedule,
                f"Entity not found in Webex: type={schedule.entity_type.value} "
                f"id={schedule.entity_id}",
                triggered_by,
            )

        # Save snapshot of current settings for later revert
        schedule.original_settings = snapshot
        logger.debug(
            f"[CFwdSvc] Snapshot for {schedule.entity_name}: {snapshot}"
        )

        # Apply forward
        _set_forward(entity, schedule)

        schedule.status          = ScheduleStatus.ACTIVE
        schedule.last_applied_at = datetime.now(timezone.utc)
        schedule.last_error      = None
        db.session.commit()

        action = (
            "CALL_FORWARD_ONDEMAND_ON"
            if schedule.is_ondemand else
            "CALL_FORWARD_APPLIED"
        )
        AuditLog.write(
            action=action,
            username=triggered_by,
            resource_type="call_forward_schedule",
            resource_id=schedule.id,
            resource_name=schedule.name,
            payload_after={
                "entity":      schedule.entity_name,
                "destination": schedule.destination,
                "type":        schedule.forward_type.value,
                "ondemand":    schedule.is_ondemand,
            },
            status="success",
        )

        logger.info(
            f"[CFwdSvc] Applied forward for '{schedule.entity_name}' "
            f"→ {schedule.destination} "
            f"(type={schedule.forward_type.value}, by={triggered_by})"
        )
        return True, (
            f"Call forward applied: {schedule.entity_name} → {schedule.destination}"
        )

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[CFwdSvc] apply_forward failed for id={schedule.id}: {exc}")
        return _mark_error(schedule, str(exc), triggered_by)


# ── Revert forward ────────────────────────────────────────────────────────────

def revert_forward(
    schedule: CallForwardSchedule,
    triggered_by: str = "scheduler",
) -> Tuple[bool, str]:
    """
    Revert the Webex entity's call forward settings to the snapshot
    captured when the schedule was applied.

    If no snapshot exists, disables all forward types as a safe default.

    Returns (success, message).
    """
    if schedule.status == ScheduleStatus.INACTIVE:
        return True, "Already inactive — skipping duplicate revert."

    try:
        webex          = get_webex_client()
        entity, _      = _resolve_entity(webex, schedule)

        if entity is None:
            return _mark_error(
                schedule,
                f"Entity not found during revert: {schedule.entity_id}",
                triggered_by,
            )

        snapshot = schedule.original_settings or {}
        _restore_forward(entity, schedule, snapshot)

        schedule.status           = ScheduleStatus.INACTIVE
        schedule.is_ondemand      = False
        schedule.last_reverted_at = datetime.now(timezone.utc)
        schedule.last_error       = None
        db.session.commit()

        action = (
            "CALL_FORWARD_ONDEMAND_OFF"
            if schedule.is_ondemand else
            "CALL_FORWARD_REVERTED"
        )
        AuditLog.write(
            action=action,
            username=triggered_by,
            resource_type="call_forward_schedule",
            resource_id=schedule.id,
            resource_name=schedule.name,
            payload_before={
                "destination": schedule.destination,
                "type":        schedule.forward_type.value,
            },
            payload_after=snapshot,
            status="success",
        )

        logger.info(
            f"[CFwdSvc] Reverted forward for '{schedule.entity_name}' "
            f"(by={triggered_by})"
        )
        return True, f"Call forward reverted for {schedule.entity_name}."

    except Exception as exc:
        db.session.rollback()
        logger.error(f"[CFwdSvc] revert_forward failed for id={schedule.id}: {exc}")
        return _mark_error(schedule, str(exc), triggered_by)


# ── On-demand ─────────────────────────────────────────────────────────────────

def ondemand_on(
    schedule: CallForwardSchedule,
    admin_username: str,
) -> Tuple[bool, str]:
    """
    Immediately apply forwarding outside the schedule window.
    Sets is_ondemand=True so the scheduler tick won't auto-revert it.
    """
    schedule.is_ondemand = True
    db.session.commit()
    return apply_forward(schedule, triggered_by=admin_username)


def ondemand_off(
    schedule: CallForwardSchedule,
    admin_username: str,
) -> Tuple[bool, str]:
    """
    Immediately revert an on-demand forward.
    """
    return revert_forward(schedule, triggered_by=admin_username)


# ── Schedule evaluation ───────────────────────────────────────────────────────

def evaluate_schedules() -> dict:
    """
    Compare every active schedule against the current local time
    in the schedule's configured timezone.

    Returns a summary dict:
        {
          "applied":  [schedule_ids],
          "reverted": [schedule_ids],
          "errors":   [schedule_ids],
          "skipped":  [schedule_ids],
        }
    """
    schedules = (
        CallForwardSchedule.query
        .filter_by(is_active=True)
        .filter(CallForwardSchedule.is_ondemand == False)   # noqa: E712
        .all()
    )

    result = {"applied": [], "reverted": [], "errors": [], "skipped": []}

    for sched in schedules:
        now_local = _local_now(sched.timezone_name)
        should_be_active = sched.is_in_window(now_local)

        if should_be_active and sched.status != ScheduleStatus.ACTIVE:
            ok, _ = apply_forward(sched, triggered_by="scheduler")
            (result["applied"] if ok else result["errors"]).append(sched.id)

        elif not should_be_active and sched.status == ScheduleStatus.ACTIVE:
            ok, _ = revert_forward(sched, triggered_by="scheduler")
            (result["reverted"] if ok else result["errors"]).append(sched.id)

        else:
            result["skipped"].append(sched.id)

    logger.info(
        f"[CFwdSvc] Tick: applied={result['applied']} "
        f"reverted={result['reverted']} errors={result['errors']}"
    )
    return result


# ── Webex entity resolver ─────────────────────────────────────────────────────

def _resolve_entity(webex, schedule: CallForwardSchedule):
    """
    Resolve the Webex entity and return (entity, snapshot_dict).
    snapshot_dict captures the current forward settings before we overwrite them.
    """
    t = schedule.entity_type

    if t == EntityType.USER:
        entity = (
            webex.org.get_person_by_id(schedule.entity_id) or
            webex.org.get_person_by_email(schedule.entity_id)
        )
        if not entity:
            return None, {}
        snapshot = {
            "call_forwarding_always_enabled":    getattr(entity, "call_forwarding_always_enabled",    False),
            "call_forwarding_always_destination": getattr(entity, "call_forwarding_always_destination",""),
            "call_forwarding_busy_enabled":       getattr(entity, "call_forwarding_busy_enabled",       False),
            "call_forwarding_busy_destination":   getattr(entity, "call_forwarding_busy_destination",   ""),
            "call_forwarding_no_answer_enabled":   getattr(entity, "call_forwarding_no_answer_enabled",  False),
            "call_forwarding_no_answer_destination":getattr(entity,"call_forwarding_no_answer_destination",""),
        }
        return entity, snapshot

    if t == EntityType.HUNT_GROUP:
        entity = _find_by_id(webex.org.hunt_groups, schedule.entity_id)
        if not entity:
            return None, {}
        snapshot = {
            "call_forwarding_always_enabled":     getattr(entity, "call_forwarding_always_enabled",    False),
            "call_forwarding_always_destination":  getattr(entity, "call_forwarding_always_destination",""),
        }
        return entity, snapshot

    if t == EntityType.AUTO_ATTENDANT:
        entity = _find_by_id(webex.org.auto_attendants, schedule.entity_id)
        return (entity, {}) if entity else (None, {})

    if t == EntityType.CALL_QUEUE:
        entity = _find_by_id(webex.org.call_queues, schedule.entity_id)
        return (entity, {}) if entity else (None, {})

    if t == EntityType.WORKSPACE:
        entity = webex.org.get_workspace_by_id(schedule.entity_id)
        return (entity, {}) if entity else (None, {})

    return None, {}


def _find_by_id(collection, entity_id: str):
    """Linear search through a wxcadm collection by id."""
    if not collection:
        return None
    for item in collection:
        if getattr(item, "id", "") == entity_id:
            return item
    return None


# ── Webex forward setters ─────────────────────────────────────────────────────

def _set_forward(entity, schedule: CallForwardSchedule) -> None:
    """
    Write the forward configuration onto the Webex entity object
    and push the change.
    """
    ft   = schedule.forward_type
    dest = schedule.destination

    if ft == ForwardType.ALWAYS:
        entity.call_forwarding_always_enabled     = True
        entity.call_forwarding_always_destination = dest

    elif ft == ForwardType.BUSY:
        entity.call_forwarding_busy_enabled       = True
        entity.call_forwarding_busy_destination   = dest

    elif ft == ForwardType.NO_ANSWER:
        entity.call_forwarding_no_answer_enabled      = True
        entity.call_forwarding_no_answer_destination  = dest
        # Typically also set ring-count before forwarding
        entity.call_forwarding_no_answer_number_of_rings = getattr(
            entity, "call_forwarding_no_answer_number_of_rings", 3
        )

    elif ft == ForwardType.SELECTIVE:
        # Selective forwarding is managed by Webex rules — just enable it
        entity.call_forwarding_selective_enabled = True

    entity.push()


def _restore_forward(entity, schedule: CallForwardSchedule, snapshot: dict) -> None:
    """
    Restore the Webex entity to its snapshotted forward settings.
    Falls back to disabling all forward types if snapshot is empty.
    """
    if not snapshot:
        # Safe default: disable all forward types
        for attr in [
            "call_forwarding_always_enabled",
            "call_forwarding_busy_enabled",
            "call_forwarding_no_answer_enabled",
            "call_forwarding_selective_enabled",
        ]:
            if hasattr(entity, attr):
                setattr(entity, attr, False)
        entity.push()
        return

    for key, value in snapshot.items():
        if hasattr(entity, key):
            setattr(entity, key, value)
    entity.push()


# ── Timezone helper ───────────────────────────────────────────────────────────

def _local_now(tz_name: str) -> datetime:
    """Return current datetime in the given timezone."""
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        logger.warning(
            f"[CFwdSvc] Unknown timezone '{tz_name}' — falling back to UTC."
        )
        tz = ZoneInfo("UTC")
    return datetime.now(tz)


# ── Error marker ──────────────────────────────────────────────────────────────

def _mark_error(
    schedule: CallForwardSchedule,
    error_msg: str,
    triggered_by: str,
) -> Tuple[bool, str]:
    schedule.status     = ScheduleStatus.ERROR
    schedule.last_error = error_msg
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()

    AuditLog.write(
        action="CALL_FORWARD_APPLIED",
        username=triggered_by,
        resource_type="call_forward_schedule",
        resource_id=schedule.id,
        resource_name=schedule.name,
        status="failure",
        status_detail=error_msg,
    )

    logger.error(
        f"[CFwdSvc] Error on schedule id={schedule.id} "
        f"'{schedule.name}': {error_msg}"
    )
    return False, error_msg
