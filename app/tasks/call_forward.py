"""
Celery tasks for the call forward scheduling engine.

Tasks:
  - tick_call_forward_schedules  — runs every minute via Celery Beat
  - apply_schedule_task          — apply a single schedule (called directly)
  - revert_schedule_task         — revert a single schedule (called directly)
"""
import logging
from app.extensions import celery, db
from app.models.call_forward import CallForwardSchedule, ScheduleStatus

logger = logging.getLogger(__name__)


@celery.task(
    name="app.tasks.call_forward.tick_call_forward_schedules",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def tick_call_forward_schedules(self):
    """
    Main scheduler tick — evaluates all active schedules against
    the current local time and applies or reverts forwarding as needed.

    Runs every minute via Celery Beat.
    """
    from app.services.call_forward_service import evaluate_schedules

    try:
        result = evaluate_schedules()
        logger.info(
            f"[CFwdTick] applied={len(result['applied'])} "
            f"reverted={len(result['reverted'])} "
            f"errors={len(result['errors'])} "
            f"skipped={len(result['skipped'])}"
        )
        return result
    except Exception as exc:
        logger.error(f"[CFwdTick] Unhandled error: {exc}")
        db.session.rollback()
        raise self.retry(exc=exc)


@celery.task(
    name="app.tasks.call_forward.apply_schedule_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def apply_schedule_task(self, schedule_id: int, triggered_by: str = "system"):
    """
    Apply forwarding for a single schedule.
    Called directly from the admin UI for on-demand activation.
    """
    from app.services.call_forward_service import apply_forward

    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        return {"success": False, "message": f"Schedule {schedule_id} not found."}

    try:
        ok, msg = apply_forward(schedule, triggered_by=triggered_by)
        return {"success": ok, "message": msg, "schedule_id": schedule_id}
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[CFwdTask] apply_schedule_task failed id={schedule_id}: {exc}")
        raise self.retry(exc=exc)


@celery.task(
    name="app.tasks.call_forward.revert_schedule_task",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def revert_schedule_task(self, schedule_id: int, triggered_by: str = "system"):
    """
    Revert forwarding for a single schedule.
    Called directly from the admin UI for on-demand deactivation.
    """
    from app.services.call_forward_service import revert_forward

    schedule = CallForwardSchedule.query.get(schedule_id)
    if not schedule:
        return {"success": False, "message": f"Schedule {schedule_id} not found."}

    try:
        ok, msg = revert_forward(schedule, triggered_by=triggered_by)
        return {"success": ok, "message": msg, "schedule_id": schedule_id}
    except Exception as exc:
        db.session.rollback()
        logger.error(f"[CFwdTask] revert_schedule_task failed id={schedule_id}: {exc}")
        raise self.retry(exc=exc)


@celery.task(
    name="app.tasks.call_forward.revert_all_active_on_startup",
    bind=True,
    max_retries=1,
)
def revert_all_active_on_startup(self):
    """
    Called once on application startup via Celery Beat warmup.
    Reverts any schedules that were left ACTIVE from a previous run
    where the revert tick may have been missed (e.g. worker crash).
    """
    from app.services.call_forward_service import evaluate_schedules

    try:
        result = evaluate_schedules()
        logger.info(
            f"[CFwdStartup] Startup evaluation complete. "
            f"applied={result['applied']} reverted={result['reverted']}"
        )
        return result
    except Exception as exc:
        logger.error(f"[CFwdStartup] Error: {exc}")
        db.session.rollback()
