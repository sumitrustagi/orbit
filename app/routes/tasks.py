"""
Task Monitor Blueprint.

Routes:
  GET  /admin/tasks/                  → Live task monitor dashboard
  GET  /admin/tasks/history           → Paginated task execution history
  POST /admin/tasks/trigger/<name>    → Manually trigger a named task
  GET  /admin/tasks/api/status        → AJAX: live worker + queue stats
  GET  /admin/tasks/api/beat-health   → AJAX: last beat heartbeat time
  GET  /admin/tasks/api/recent        → AJAX: recent task executions from Celery result backend
"""
import logging
from datetime import datetime, timezone, timedelta

from flask import (
    Blueprint, render_template, request,
    jsonify, redirect, url_for, flash
)
from flask_login import login_required

from app.utils.decorators import superadmin_required
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)

tasks_bp = Blueprint(
    "tasks", __name__,
    template_folder="../templates/tasks",
    url_prefix="/admin/tasks"
)

# ── Allowlist of tasks that can be manually triggered ─────────────────────────
TRIGGERABLE_TASKS = {
    "evaluate_cf_schedules": {
        "task":  "app.tasks.call_forward.evaluate_schedules",
        "label": "Evaluate Call Forward Schedules",
        "queue": "call_forward",
    },
    "retry_snow": {
        "task":  "app.tasks.snow.retry_failed_requests",
        "label": "Retry Failed SNOW Requests",
        "queue": "snow",
    },
    "sync_users": {
        "task":  "app.tasks.webex.sync_webex_users",
        "label": "Sync Webex Users",
        "queue": "webex_sync",
    },
    "sync_hunt_groups": {
        "task":  "app.tasks.webex.sync_hunt_groups",
        "label": "Sync Hunt Groups",
        "queue": "webex_sync",
    },
    "sync_call_queues": {
        "task":  "app.tasks.webex.sync_call_queues",
        "label": "Sync Call Queues",
        "queue": "webex_sync",
    },
    "full_webex_sync": {
        "task":  "app.tasks.webex.full_sync",
        "label": "Full Webex Sync",
        "queue": "webex_sync",
    },
    "release_quarantine": {
        "task":  "app.tasks.maintenance.release_quarantine_dids",
        "label": "Release Quarantine DIDs",
        "queue": "maintenance",
    },
    "purge_audit": {
        "task":  "app.tasks.maintenance.purge_old_audit_logs",
        "label": "Purge Old Audit Logs",
        "queue": "maintenance",
    },
    "health_ping": {
        "task":  "app.tasks.maintenance.health_ping",
        "label": "Health Ping",
        "queue": "maintenance",
    },
}


# ═════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@tasks_bp.route("/", methods=["GET"])
@login_required
@superadmin_required
def monitor():
    """Live task monitor dashboard."""
    last_ping = _get_last_beat_ping()
    return render_template(
        "monitor.html",
        triggerable=TRIGGERABLE_TASKS,
        last_ping=last_ping,
    )


@tasks_bp.route("/history", methods=["GET"])
@login_required
@superadmin_required
def history():
    """Paginated task execution history from AuditLog."""
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 50
    action   = request.args.get("action", "").strip()

    TASK_ACTIONS = [
        "SNOW_FULFILL", "SNOW_FAIL", "CF_APPLY", "CF_REVERT",
        "WEBEX_SYNC", "QUARANTINE_RELEASE", "HEALTH_PING",
    ]
    q = AuditLog.query.filter(
        AuditLog.username == "celery"
    )
    if action:
        q = q.filter(AuditLog.action == action)

    q      = q.order_by(AuditLog.created_at.desc())
    total  = q.count()
    pages  = max(1, (total + per_page - 1) // per_page)
    items  = q.offset((page - 1) * per_page).limit(per_page).all()

    return render_template(
        "history.html",
        items=items,
        total=total,
        page=page,
        pages=pages,
        per_page=per_page,
        action=action,
        task_actions=TASK_ACTIONS,
    )


# ═════════════════════════════════════════════════════════════════════════════
# MANUAL TRIGGER
# ═════════════════════════════════════════════════════════════════════════════

@tasks_bp.route("/trigger/<name>", methods=["POST"])
@login_required
@superadmin_required
def trigger_task(name: str):
    """
    Manually enqueue one of the allowlisted tasks.
    Returns JSON for AJAX or redirects for form POST.
    """
    task_def = TRIGGERABLE_TASKS.get(name)
    if not task_def:
        if request.is_json:
            return jsonify({"success": False, "message": "Unknown task."}), 400
        flash("Unknown task.", "danger")
        return redirect(url_for("tasks.monitor"))

    try:
        from app.tasks import celery_app
        result = celery_app.send_task(
            task_def["task"],
            queue=task_def["queue"],
        )
        AuditLog.write(
            action        = "TASK_TRIGGER",
            username      = getattr(__import__('flask_login', fromlist=['current_user']).current_user, 'username', 'unknown'),
            user_role     = "superadmin",
            resource_type = "celery_task",
            resource_name = task_def["task"],
            payload_after = {"task_id": result.id, "queue": task_def["queue"]},
            status        = "success",
        )
        msg = f"Task '{task_def['label']}' queued (ID: {result.id[:8]}…)."
        logger.info(f"[TaskMonitor] Manual trigger: {task_def['task']} → {result.id}")

        if request.is_json:
            return jsonify({"success": True, "message": msg, "task_id": result.id})

        flash(msg, "success")

    except Exception as exc:
        logger.error(f"[TaskMonitor] Trigger failed for '{name}': {exc}")
        if request.is_json:
            return jsonify({"success": False, "message": str(exc)}), 500
        flash(f"Failed to queue task: {exc}", "danger")

    return redirect(url_for("tasks.monitor"))


# ═════════════════════════════════════════════════════════════════════════════
# AJAX ENDPOINTS
# ═════════════════════════════════════════════════════════════════════════════

@tasks_bp.route("/api/status", methods=["GET"])
@login_required
@superadmin_required
def api_status():
    """
    Return live Celery worker and queue statistics.
    Uses the Celery inspect API with a 3-second timeout.
    """
    try:
        from app.tasks import celery_app
        inspector = celery_app.control.inspect(timeout=3)

        active_tasks = inspector.active()  or {}
        reserved     = inspector.reserved() or {}
        stats        = inspector.stats()    or {}

        workers = []
        for worker_name, worker_stats in stats.items():
            workers.append({
                "name":      worker_name,
                "active":    len(active_tasks.get(worker_name, [])),
                "reserved":  len(reserved.get(worker_name,    [])),
                "pool_size": (
                    worker_stats.get("pool", {}).get("max-concurrency", "—")
                ),
                "queues": list({
                    q["name"]
                    for q in worker_stats.get("consumer", {})
                                         .get("queues", [])
                }),
            })

        return jsonify({
            "ok":           True,
            "worker_count": len(workers),
            "workers":      workers,
            "ts":           datetime.now(timezone.utc).isoformat(),
        })

    except Exception as exc:
        return jsonify({
            "ok":           False,
            "worker_count": 0,
            "workers":      [],
            "error":        str(exc),
            "ts":           datetime.now(timezone.utc).isoformat(),
        })


@tasks_bp.route("/api/beat-health", methods=["GET"])
@login_required
@superadmin_required
def api_beat_health():
    """Return last health_ping timestamp and whether beat appears alive."""
    last_ping = _get_last_beat_ping()
    if last_ping:
        age_seconds = (datetime.now(timezone.utc) - last_ping).total_seconds()
        alive = age_seconds < 900  # 15 minutes — 1.5× the 10-minute beat interval
    else:
        age_seconds = None
        alive       = False

    return jsonify({
        "alive":       alive,
        "last_ping":   last_ping.isoformat() if last_ping else None,
        "age_seconds": age_seconds,
    })


@tasks_bp.route("/api/recent", methods=["GET"])
@login_required
@superadmin_required
def api_recent():
    """Return the 30 most recent celery task audit log entries."""
    logs = (
        AuditLog.query
        .filter(AuditLog.username == "celery")
        .order_by(AuditLog.created_at.desc())
        .limit(30)
        .all()
    )
    return jsonify([
        {
            "action":        log.action,
            "resource_type": log.resource_type or "",
            "resource_name": log.resource_name or "",
            "status":        log.status,
            "payload":       log.payload_after or {},
            "created_at":    log.created_at.strftime("%d %b %Y %H:%M:%S UTC")
                             if log.created_at else "",
        }
        for log in logs
    ])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_last_beat_ping() -> datetime | None:
    """Return the created_at of the most recent HEALTH_PING audit entry."""
    log = (
        AuditLog.query
        .filter_by(action="HEALTH_PING", username="celery")
        .order_by(AuditLog.created_at.desc())
        .first()
    )
    return log.created_at if log else None
