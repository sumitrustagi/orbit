"""
Celery tasks for Call Forward schedule evaluation and execution.

Tasks:
  evaluate_schedules()              — Beat: check all active schedules
  apply_call_forward(schedule_id)   — Apply forwarding for one schedule
  revert_call_forward(schedule_id)  — Revert forwarding for one schedule
"""
import logging
from datetime import datetime, timezone

from celery import shared_task

from app.extensions import db
from app.models.audit import AuditLog
from app.models.call_forward import (
    CallForwardSchedule, ForwardExecutionLog,
    ScheduleStatus, ExecutionResult, ScheduleType
)

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# BEAT: EVALUATE ALL SCHEDULES
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    name="app.tasks.call_forward.evaluate_schedules",
    queue="call_forward",
)
def evaluate_schedules() -> dict:
    """
    Beat task — runs every 2 minutes.

    For every ACTIVE call forward schedule:
      - Evaluate whether the current time falls inside the active window
      - If should_forward=True  and not yet forwarding → apply
      - If should_forward=False and currently forwarding → revert
    """
    now     = datetime.now(timezone.utc)
    active  = CallForwardSchedule.query.filter_by(status=ScheduleStatus.ACTIVE).all()

    applied  = 0
    reverted = 0
    skipped  = 0

    for schedule in active:
        try:
            should_forward = _evaluate_schedule(schedule, now)

            if should_forward and not schedule.is_currently_forwarded:
                apply_call_forward.apply_async(
                    args=[schedule.id], queue="call_forward"
                )
                applied += 1

            elif not should_forward and schedule.is_currently_forwarded:
                revert_call_forward.apply_async(
                    args=[schedule.id], queue="call_forward"
                )
                reverted += 1

            else:
                skipped += 1

        except Exception as exc:
            logger.error(
                f"[CF] Error evaluating schedule {schedule.id} "
                f"'{schedule.name}': {exc}"
            )

    logger.info(
        f"[CF] Evaluation complete — "
        f"applied={applied} reverted={reverted} skipped={skipped}"
    )
    return {"applied": applied, "reverted": reverted, "skipped": skipped}


def _evaluate_schedule(schedule: CallForwardSchedule, now: datetime) -> bool:
    """
    Return True if the schedule should be actively forwarding at `now`.

    always_on  → always True
    one_off    → True if now is within [start_datetime, end_datetime]
    recurring  → True if current day-of-week is active AND
                 current time is within [from_time, to_time]
                 (handles overnight windows where from_time > to_time)
    """
    import pytz

    if schedule.schedule_type == ScheduleType.ALWAYS_ON:
        return True

    tz  = pytz.timezone(schedule.timezone_name or "UTC")
    now_local = now.astimezone(tz)

    if schedule.schedule_type == ScheduleType.ONE_OFF:
        if schedule.start_datetime and schedule.end_datetime:
            start = schedule.start_datetime.astimezone(tz)
            end   = schedule.end_datetime.astimezone(tz)
            return start <= now_local <= end
        return False

    if schedule.schedule_type == ScheduleType.RECURRING:
        days = schedule.days_of_week or []
        if str(now_local.weekday()) not in [str(d) for d in days]:
            return False

        if not schedule.from_time or not schedule.to_time:
            return False

        current_t = now_local.time()
        from_t    = schedule.from_time
        to_t      = schedule.to_time

        if from_t <= to_t:
            # Normal window — e.g. 09:00 → 17:00
            return from_t <= current_t <= to_t
        else:
            # Overnight window — e.g. 22:00 → 06:00
            return current_t >= from_t or current_t <= to_t

    return False


# ═══════════════════════════════════════════════════════════════════════════════
# APPLY FORWARDING
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.call_forward.apply_call_forward",
    queue="call_forward",
    max_retries=3,
    default_retry_delay=30,
)
def apply_call_forward(self, schedule_id: int) -> dict:
    """
    Apply call forwarding for a single schedule via the Webex API.
    Writes a ForwardExecutionLog record regardless of outcome.
    """
    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        return {"status": "not_found"}

    log = ForwardExecutionLog(
        schedule_id  = schedule.id,
        action       = "apply",
        executed_at  = datetime.now(timezone.utc),
        entity_id    = schedule.webex_entity_id,
        entity_type  = schedule.entity_type,
        forward_to   = schedule.forward_to or "",
    )

    try:
        from app.services.webex_service import set_call_forward
        set_call_forward(
            entity_type  = schedule.entity_type,
            entity_id    = schedule.webex_entity_id,
            forward_to   = schedule.forward_to or "voicemail",
            forward_type = schedule.forward_target,
        )

        schedule.is_currently_forwarded = True
        schedule.last_applied_at        = datetime.now(timezone.utc)
        log.result                      = ExecutionResult.SUCCESS

        db.session.add(log)
        db.session.commit()

        AuditLog.write(
            action="CF_APPLY", username="celery", user_role="scheduler",
            resource_type="call_forward_schedule",
            resource_id=schedule.id, resource_name=schedule.name,
            payload_after={
                "forward_to": schedule.forward_to,
                "entity":     schedule.webex_entity_id,
            },
            status="success",
        )
        logger.info(f"[CF] ✓ Applied forwarding for '{schedule.name}'.")
        return {"status": "applied", "schedule": schedule.name}

    except Exception as exc:
        db.session.rollback()
        log.result       = ExecutionResult.FAILURE
        log.error_detail = str(exc)[:512]
        db.session.add(log)
        db.session.commit()

        AuditLog.write(
            action="CF_APPLY", username="celery", user_role="scheduler",
            resource_type="call_forward_schedule",
            resource_id=schedule.id, resource_name=schedule.name,
            payload_after={"error": str(exc)},
            status="failure",
        )
        logger.error(f"[CF] ✗ Apply failed for '{schedule.name}': {exc}")
        raise self.retry(exc=exc)


# ═══════════════════════════════════════════════════════════════════════════════
# REVERT FORWARDING
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(
    bind=True,
    name="app.tasks.call_forward.revert_call_forward",
    queue="call_forward",
    max_retries=3,
    default_retry_delay=30,
)
def revert_call_forward(self, schedule_id: int) -> dict:
    """
    Revert call forwarding for a single schedule via the Webex API.
    """
    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        return {"status": "not_found"}

    log = ForwardExecutionLog(
        schedule_id  = schedule.id,
        action       = "revert",
        executed_at  = datetime.now(timezone.utc),
        entity_id    = schedule.webex_entity_id,
        entity_type  = schedule.entity_type,
        forward_to   = "",
    )

    try:
        from app.services.webex_service import clear_call_forward
        clear_call_forward(
            entity_type = schedule.entity_type,
            entity_id   = schedule.webex_entity_id,
        )

        schedule.is_currently_forwarded = False
        schedule.last_reverted_at       = datetime.now(timezone.utc)
        log.result                      = ExecutionResult.SUCCESS

        db.session.add(log)
        db.session.commit()

        AuditLog.write(
            action="CF_REVERT", username="celery", user_role="scheduler",
            resource_type="call_forward_schedule",
            resource_id=schedule.id, resource_name=schedule.name,
            status="success",
        )
        logger.info(f"[CF] ✓ Reverted forwarding for '{schedule.name}'.")
        return {"status": "reverted", "schedule": schedule.name}

    except Exception as exc:
        db.session.rollback()
        log.result       = ExecutionResult.FAILURE
        log.error_detail = str(exc)[:512]
        db.session.add(log)
        db.session.commit()

        AuditLog.write(
            action="CF_REVERT", username="celery", user_role="scheduler",
            resource_type="call_forward_schedule",
            resource_id=schedule.id, resource_name=schedule.name,
            payload_after={"error": str(exc)},
            status="failure",
        )
        logger.error(f"[CF] ✗ Revert failed for '{schedule.name}': {exc}")
        raise self.retry(exc=exc)
