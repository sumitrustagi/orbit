"""
Celery tasks for scheduled call forwarding.
Runs every 60 seconds via Celery Beat, checks all active schedules
and applies/reverts call forwarding via the Webex API (wxcadm).
"""
import logging
from datetime import datetime, timezone

import pytz

from app.extensions import celery, db
from app.models.call_forward import CallForwardSchedule, ForwardType
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


@celery.task(name="app.tasks.call_forwarding.process_scheduled_forwards",
             bind=True, max_retries=2, default_retry_delay=60)
def process_scheduled_forwards(self):
    """
    For every enabled scheduled call-forward rule, check if the current
    time falls within the window. If yes — apply forwarding.
    If no — revert forwarding (if it was previously applied by this schedule).
    """
    now_utc = datetime.now(timezone.utc)
    processed = 0

    schedules = (
        CallForwardSchedule.query
        .filter_by(forward_type=ForwardType.SCHEDULED, schedule_enabled=True)
        .all()
    )

    for schedule in schedules:
        try:
            _evaluate_schedule(schedule, now_utc)
            processed += 1
        except Exception as exc:
            logger.error(
                f"[CallForward] Error processing schedule {schedule.id}: {exc}"
            )

    logger.debug(f"[CallForward] Processed {processed} schedules.")
    return {"processed": processed}


def _evaluate_schedule(schedule: CallForwardSchedule, now_utc: datetime) -> None:
    """Evaluate a single schedule and apply/revert forwarding as needed."""
    user = schedule.user
    if not user or not user.is_active or not user.webex_person_id:
        return

    # Convert now to the user's timezone for day/time comparison
    try:
        tz      = pytz.timezone(schedule.timezone or "UTC")
        now_local = now_utc.astimezone(tz)
    except pytz.UnknownTimeZoneError:
        now_local = now_utc

    current_day  = now_local.weekday()      # 0=Mon … 6=Sun
    current_time = now_local.time()

    in_window = (
        schedule.start_time is not None and
        schedule.end_time   is not None and
        current_day in schedule.days_list  and
        schedule.start_time <= current_time <= schedule.end_time
    )

    if in_window and not schedule.webex_applied:
        _apply_forwarding(schedule, user)
    elif not in_window and schedule.webex_applied:
        _revert_forwarding(schedule, user)


def _apply_forwarding(schedule: CallForwardSchedule, user) -> None:
    """Enable call forwarding on Webex for the given user."""
    try:
        from app.services.webex_service import get_webex_client
        webex = get_webex_client()
        person = webex.org.get_person_by_id(user.webex_person_id)

        if person:
            # wxcadm call forwarding API
            person.call_forwarding.always.enabled     = True
            person.call_forwarding.always.destination = schedule.forward_to
            person.call_forwarding.push()

            schedule.webex_applied   = True
            schedule.last_applied_at = datetime.now(timezone.utc)
            db.session.commit()

            AuditLog.write(
                action="CALL_FORWARD_APPLIED",
                user_id=user.id, username=user.username,
                resource_type="call_forward_schedule",
                resource_id=schedule.id,
                resource_name=f"→ {schedule.forward_to}",
                status="success",
            )
            logger.info(
                f"[CallForward] Applied: user={user.username} "
                f"→ {schedule.forward_to}"
            )
    except Exception as exc:
        logger.error(
            f"[CallForward] Apply failed for user {user.username}: {exc}"
        )


def _revert_forwarding(schedule: CallForwardSchedule, user) -> None:
    """Disable call forwarding on Webex for the given user."""
    try:
        from app.services.webex_service import get_webex_client
        webex = get_webex_client()
        person = webex.org.get_person_by_id(user.webex_person_id)

        if person:
            person.call_forwarding.always.enabled = False
            person.call_forwarding.push()

            schedule.webex_applied = False
            db.session.commit()

            AuditLog.write(
                action="CALL_FORWARD_REVERTED",
                user_id=user.id, username=user.username,
                resource_type="call_forward_schedule",
                resource_id=schedule.id,
                resource_name=f"reverted from {schedule.forward_to}",
                status="success",
            )
            logger.info(
                f"[CallForward] Reverted: user={user.username}"
            )
    except Exception as exc:
        logger.error(
            f"[CallForward] Revert failed for user {user.username}: {exc}"
        )


@celery.task(name="app.tasks.call_forwarding.apply_ondemand_forward",
             bind=True, max_retries=3, default_retry_delay=30)
def apply_ondemand_forward(self, schedule_id: int, enable: bool):
    """
    Apply or revert on-demand call forwarding for a single schedule.
    Called immediately from the portal when user toggles forwarding.
    """
    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        logger.error(f"[OnDemandForward] Schedule {schedule_id} not found.")
        return

    user = schedule.user
    if not user or not user.webex_person_id:
        return

    try:
        from app.services.webex_service import get_webex_client
        webex  = get_webex_client()
        person = webex.org.get_person_by_id(user.webex_person_id)

        if not person:
            raise ValueError(f"Webex person {user.webex_person_id} not found.")

        person.call_forwarding.always.enabled     = enable
        person.call_forwarding.always.destination = schedule.forward_to if enable else ""
        person.call_forwarding.push()

        schedule.is_active       = enable
        schedule.webex_applied   = enable
        schedule.last_applied_at = datetime.now(timezone.utc)
        db.session.commit()

        action = "CALL_FORWARD_ONDEMAND_ON" if enable else "CALL_FORWARD_ONDEMAND_OFF"
        AuditLog.write(
            action=action,
            user_id=user.id, username=user.username,
            resource_type="call_forward_schedule",
            resource_id=schedule_id,
            resource_name=f"→ {schedule.forward_to}" if enable else "disabled",
            status="success",
        )
        logger.info(
            f"[OnDemandForward] {'Enabled' if enable else 'Disabled'} "
            f"for user={user.username} → {schedule.forward_to}"
        )
        return {"success": True, "enabled": enable}

    except Exception as exc:
        logger.error(f"[OnDemandForward] Failed for schedule {schedule_id}: {exc}")
        raise self.retry(exc=exc)
