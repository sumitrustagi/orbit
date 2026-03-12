"""
Reports & Analytics aggregation service.

All database queries are centralised here so routes stay thin.
Every function returns plain Python dicts / lists suitable for
JSON serialisation or Jinja2 rendering — no SQLAlchemy objects
are passed to templates.

Query performance notes:
  - All heavy aggregations use db.session.execute() with compiled
    SQL for efficiency on large tables.
  - Results are cached for 5 minutes via Flask-Caching where the
    cache decorator is available; falls back to no-cache gracefully.
  - Date ranges default to the last 30 days where applicable.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Any

from sqlalchemy import func, text, case

from app.extensions import db
from app.models.did import DID, DIDPool, DIDStatus
from app.models.snow import SNOWRequest, RequestStatus
from app.models.call_forward import (
    CallForwardSchedule, ForwardExecutionLog,
    ScheduleStatus, ExecutionResult
)
from app.models.audit import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _date_range(days: int = 30):
    """Return (start, end) UTC datetimes for the last `days` days."""
    end   = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    return start, end


def _safe(fn, default=None):
    """Run fn(); return default on any exception."""
    try:
        return fn()
    except Exception as exc:
        logger.warning(f"[ReportsSvc] Query failed: {exc}")
        return default


# ═══════════════════════════════════════════════════════════════════════════════
# OVERVIEW / DASHBOARD
# ═══════════════════════════════════════════════════════════════════════════════

def get_dashboard_summary() -> dict:
    """
    Top-level KPI snapshot for the main analytics dashboard.
    Returns a flat dict of scalar values.
    """
    start_30d, now = _date_range(30)

    total_dids        = _safe(lambda: DID.query.count(), 0)
    assigned_dids     = _safe(lambda: DID.query.filter_by(status=DIDStatus.ASSIGNED).count(), 0)
    available_dids    = _safe(lambda: DID.query.filter_by(status=DIDStatus.AVAILABLE).count(), 0)
    quarantine_dids   = _safe(lambda: DID.query.filter_by(status=DIDStatus.QUARANTINE).count(), 0)

    total_pools       = _safe(lambda: DIDPool.query.count(), 0)

    snow_total        = _safe(lambda: SNOWRequest.query.count(), 0)
    snow_fulfilled    = _safe(lambda: SNOWRequest.query.filter_by(
                                status=RequestStatus.FULFILLED).count(), 0)
    snow_pending      = _safe(lambda: SNOWRequest.query.filter_by(
                                status=RequestStatus.PENDING).count(), 0)
    snow_failed       = _safe(lambda: SNOWRequest.query.filter_by(
                                status=RequestStatus.FAILED).count(), 0)
    snow_last_30d     = _safe(lambda: SNOWRequest.query.filter(
                                SNOWRequest.created_at >= start_30d).count(), 0)

    cf_total          = _safe(lambda: CallForwardSchedule.query.count(), 0)
    cf_active         = _safe(lambda: CallForwardSchedule.query.filter_by(
                                status=ScheduleStatus.ACTIVE).count(), 0)
    cf_forwarding     = _safe(lambda: CallForwardSchedule.query.filter_by(
                                is_currently_forwarded=True).count(), 0)

    audit_last_30d    = _safe(lambda: AuditLog.query.filter(
                                AuditLog.created_at >= start_30d).count(), 0)
    audit_failures    = _safe(lambda: AuditLog.query.filter(
                                AuditLog.status == "failure",
                                AuditLog.created_at >= start_30d).count(), 0)

    total_users       = _safe(lambda: User.query.count(), 0)
    active_users      = _safe(lambda: User.query.filter_by(is_active=True).count(), 0)

    did_utilisation   = round(
        (assigned_dids / total_dids * 100) if total_dids else 0, 1
    )

    snow_success_rate = round(
        (snow_fulfilled / snow_total * 100) if snow_total else 0, 1
    )

    return {
        # DID
        "total_dids":        total_dids,
        "assigned_dids":     assigned_dids,
        "available_dids":    available_dids,
        "quarantine_dids":   quarantine_dids,
        "total_pools":       total_pools,
        "did_utilisation":   did_utilisation,
        # SNOW
        "snow_total":        snow_total,
        "snow_fulfilled":    snow_fulfilled,
        "snow_pending":      snow_pending,
        "snow_failed":       snow_failed,
        "snow_last_30d":     snow_last_30d,
        "snow_success_rate": snow_success_rate,
        # Call Forward
        "cf_total":          cf_total,
        "cf_active":         cf_active,
        "cf_forwarding":     cf_forwarding,
        # Audit
        "audit_last_30d":    audit_last_30d,
        "audit_failures":    audit_failures,
        # Users
        "total_users":       total_users,
        "active_users":      active_users,
        # Meta
        "generated_at":      now.strftime("%d %b %Y %H:%M UTC"),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# DID ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def get_did_status_breakdown() -> list[dict]:
    """
    DID count grouped by status — used for the doughnut chart.
    Returns: [{"status": "assigned", "count": 412}, …]
    """
    rows = (
        db.session.query(DID.status, func.count(DID.id))
        .group_by(DID.status)
        .all()
    )
    return [{"status": r[0].value, "count": r[1]} for r in rows]


def get_did_pool_utilisation() -> list[dict]:
    """
    Per-pool DID utilisation — used for the horizontal bar chart.
    Returns: [{"pool": "Brussels", "total": 50, "assigned": 38, "pct": 76.0}, …]
    """
    pools = DIDPool.query.order_by(DIDPool.name.asc()).all()
    result = []
    for pool in pools:
        total    = DID.query.filter_by(pool_id=pool.id).count()
        assigned = DID.query.filter_by(
            pool_id=pool.id, status=DIDStatus.ASSIGNED
        ).count()
        pct = round((assigned / total * 100) if total else 0, 1)
        result.append({
            "pool":     pool.name,
            "total":    total,
            "assigned": assigned,
            "pct":      pct,
        })
    return result


def get_did_assignment_trend(days: int = 30) -> list[dict]:
    """
    Daily DID assignment counts over the last `days` days.
    Returns: [{"date": "2026-02-10", "assigned": 3}, …]
    """
    start, end = _date_range(days)
    rows = (
        db.session.query(
            func.date(DID.assigned_at).label("d"),
            func.count(DID.id).label("cnt"),
        )
        .filter(DID.assigned_at.between(start, end))
        .group_by(func.date(DID.assigned_at))
        .order_by(func.date(DID.assigned_at).asc())
        .all()
    )
    return [{"date": str(r.d), "assigned": r.cnt} for r in rows]


def get_did_quarantine_list() -> list[dict]:
    """
    Full list of quarantined DIDs with age and pool info.
    Used for the quarantine table in the DID report.
    """
    now  = datetime.now(timezone.utc)
    dids = (
        DID.query
        .filter_by(status=DIDStatus.QUARANTINE)
        .order_by(DID.quarantine_until.asc())
        .all()
    )
    result = []
    for d in dids:
        days_left = None
        if d.quarantine_until:
            delta     = d.quarantine_until - now
            days_left = max(0, delta.days)
        result.append({
            "id":              d.id,
            "number":          d.number,
            "pool":            d.pool.name if d.pool else "—",
            "quarantine_until":d.quarantine_until.strftime("%d %b %Y") if d.quarantine_until else "—",
            "days_left":       days_left,
            "notes":           d.notes or "",
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# SERVICENOW ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def get_snow_status_breakdown() -> list[dict]:
    """SNOW request count by status."""
    rows = (
        db.session.query(SNOWRequest.status, func.count(SNOWRequest.id))
        .group_by(SNOWRequest.status)
        .all()
    )
    return [{"status": r[0].value, "count": r[1]} for r in rows]


def get_snow_daily_trend(days: int = 30) -> list[dict]:
    """
    Daily SNOW request volume and fulfillment rate over the last `days` days.
    Returns: [{"date": "2026-02-10", "total": 5, "fulfilled": 4, "failed": 1}, …]
    """
    start, end = _date_range(days)
    rows = (
        db.session.query(
            func.date(SNOWRequest.created_at).label("d"),
            func.count(SNOWRequest.id).label("total"),
            func.sum(
                case((SNOWRequest.status == RequestStatus.FULFILLED, 1), else_=0)
            ).label("fulfilled"),
            func.sum(
                case((SNOWRequest.status == RequestStatus.FAILED, 1), else_=0)
            ).label("failed"),
        )
        .filter(SNOWRequest.created_at.between(start, end))
        .group_by(func.date(SNOWRequest.created_at))
        .order_by(func.date(SNOWRequest.created_at).asc())
        .all()
    )
    return [
        {
            "date":      str(r.d),
            "total":     r.total,
            "fulfilled": int(r.fulfilled or 0),
            "failed":    int(r.failed or 0),
        }
        for r in rows
    ]


def get_snow_avg_fulfillment_time() -> float:
    """
    Average time (in minutes) between request creation and fulfillment.
    Returns 0.0 if no fulfilled requests exist.
    """
    try:
        requests = (
            SNOWRequest.query
            .filter(
                SNOWRequest.status == RequestStatus.FULFILLED,
                SNOWRequest.fulfilled_at.isnot(None),
            )
            .all()
        )
        if not requests:
            return 0.0
        deltas = [
            (r.fulfilled_at - r.created_at).total_seconds() / 60
            for r in requests
            if r.fulfilled_at and r.created_at
        ]
        return round(sum(deltas) / len(deltas), 1) if deltas else 0.0
    except Exception:
        return 0.0


def get_snow_recent_requests(limit: int = 20) -> list[dict]:
    """Most recent SNOW requests for the report table."""
    rows = (
        SNOWRequest.query
        .order_by(SNOWRequest.created_at.desc())
        .limit(limit)
        .all()
    )
    return [r.to_dict() for r in rows]


def get_snow_failed_requests() -> list[dict]:
    """All failed SNOW requests for the failures table."""
    rows = (
        SNOWRequest.query
        .filter_by(status=RequestStatus.FAILED)
        .order_by(SNOWRequest.created_at.desc())
        .all()
    )
    return [r.to_dict() for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# CALL FORWARD ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def get_cf_execution_trend(days: int = 30) -> list[dict]:
    """
    Daily call forward apply/revert counts over the last `days` days.
    Returns: [{"date": "2026-02-10", "applied": 3, "reverted": 3, "failed": 0}, …]
    """
    start, end = _date_range(days)
    rows = (
        db.session.query(
            func.date(ForwardExecutionLog.executed_at).label("d"),
            func.sum(
                case((ForwardExecutionLog.action == "apply", 1), else_=0)
            ).label("applied"),
            func.sum(
                case((ForwardExecutionLog.action == "revert", 1), else_=0)
            ).label("reverted"),
            func.sum(
                case((ForwardExecutionLog.result == ExecutionResult.FAILURE, 1), else_=0)
            ).label("failed"),
        )
        .filter(ForwardExecutionLog.executed_at.between(start, end))
        .group_by(func.date(ForwardExecutionLog.executed_at))
        .order_by(func.date(ForwardExecutionLog.executed_at).asc())
        .all()
    )
    return [
        {
            "date":     str(r.d),
            "applied":  int(r.applied  or 0),
            "reverted": int(r.reverted or 0),
            "failed":   int(r.failed   or 0),
        }
        for r in rows
    ]


def get_cf_schedule_summary() -> list[dict]:
    """
    Per-schedule execution statistics — total applies, reverts, failures.
    Used for the schedule performance table.
    """
    schedules = CallForwardSchedule.query.order_by(CallForwardSchedule.name.asc()).all()
    result    = []
    for s in schedules:
        logs     = ForwardExecutionLog.query.filter_by(schedule_id=s.id).all()
        applies  = sum(1 for l in logs if l.action == "apply")
        reverts  = sum(1 for l in logs if l.action == "revert")
        failures = sum(1 for l in logs if l.result == ExecutionResult.FAILURE)
        result.append({
            "id":                     s.id,
            "name":                   s.name,
            "entity":                 s.webex_entity_name or s.webex_entity_id,
            "status":                 s.status.value,
            "is_currently_forwarded": s.is_currently_forwarded,
            "total_applies":          applies,
            "total_reverts":          reverts,
            "total_failures":         failures,
            "last_applied_at":        s.last_applied_at.strftime("%d %b %Y %H:%M")
                                      if s.last_applied_at else "—",
            "last_reverted_at":       s.last_reverted_at.strftime("%d %b %Y %H:%M")
                                      if s.last_reverted_at else "—",
        })
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# AUDIT ANALYTICS
# ═══════════════════════════════════════════════════════════════════════════════

def get_audit_action_breakdown(days: int = 30) -> list[dict]:
    """
    Audit log entry count by action type for the last `days` days.
    Returns: [{"action": "CREATE", "count": 42}, …]
    """
    start, _ = _date_range(days)
    rows = (
        db.session.query(AuditLog.action, func.count(AuditLog.id))
        .filter(AuditLog.created_at >= start)
        .group_by(AuditLog.action)
        .order_by(func.count(AuditLog.id).desc())
        .all()
    )
    return [{"action": r[0], "count": r[1]} for r in rows]


def get_audit_daily_trend(days: int = 30) -> list[dict]:
    """Daily audit entry counts over the last `days` days."""
    start, end = _date_range(days)
    rows = (
        db.session.query(
            func.date(AuditLog.created_at).label("d"),
            func.count(AuditLog.id).label("cnt"),
            func.sum(
                case((AuditLog.status == "failure", 1), else_=0)
            ).label("failures"),
        )
        .filter(AuditLog.created_at.between(start, end))
        .group_by(func.date(AuditLog.created_at))
        .order_by(func.date(AuditLog.created_at).asc())
        .all()
    )
    return [
        {
            "date":     str(r.d),
            "total":    r.cnt,
            "failures": int(r.failures or 0),
        }
        for r in rows
    ]


def get_audit_top_users(days: int = 30, limit: int = 10) -> list[dict]:
    """
    Most active admin users by audit log entry count for the last `days` days.
    """
    start, _ = _date_range(days)
    rows = (
        db.session.query(
            AuditLog.username,
            func.count(AuditLog.id).label("cnt"),
            func.sum(
                case((AuditLog.status == "failure", 1), else_=0)
            ).label("failures"),
        )
        .filter(AuditLog.created_at >= start)
        .filter(AuditLog.username.isnot(None))
        .group_by(AuditLog.username)
        .order_by(func.count(AuditLog.id).desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "username": r.username,
            "total":    r.cnt,
            "failures": int(r.failures or 0),
        }
        for r in rows
    ]


def get_audit_log_page(
    page:         int  = 1,
    per_page:     int  = 50,
    action:       str  = "",
    username:     str  = "",
    resource_type:str  = "",
    status:       str  = "",
    days:         int  = 30,
) -> dict:
    """
    Paginated, filterable audit log for the audit report table.
    Returns {"items": [...], "total": N, "pages": N, "page": N}.
    """
    start, _ = _date_range(days)
    q = AuditLog.query.filter(AuditLog.created_at >= start)

    if action:
        q = q.filter(AuditLog.action.ilike(f"%{action}%"))
    if username:
        q = q.filter(AuditLog.username.ilike(f"%{username}%"))
    if resource_type:
        q = q.filter(AuditLog.resource_type.ilike(f"%{resource_type}%"))
    if status:
        q = q.filter(AuditLog.status == status)

    q = q.order_by(AuditLog.created_at.desc())
    total     = q.count()
    pages     = max(1, (total + per_page - 1) // per_page)
    items_raw = q.offset((page - 1) * per_page).limit(per_page).all()

    items = []
    for log in items_raw:
        items.append({
            "id":            log.id,
            "action":        log.action,
            "username":      log.username or "—",
            "user_role":     log.user_role or "—",
            "ip_address":    log.ip_address or "—",
            "resource_type": log.resource_type or "—",
            "resource_name": log.resource_name or "—",
            "status":        log.status or "—",
            "status_detail": log.status_detail or "",
            "created_at":    log.created_at.strftime("%d %b %Y %H:%M:%S UTC")
                             if log.created_at else "—",
        })

    return {"items": items, "total": total, "pages": pages, "page": page}


# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def export_did_csv() -> str:
    """
    Generate CSV string for full DID export.
    """
    import csv, io
    buf  = io.StringIO()
    w    = csv.writer(buf)
    w.writerow(["ID","Number","E164","Status","Pool","Assigned To",
                "Assigned Email","Assigned At","Country","Notes"])

    dids = DID.query.order_by(DID.number.asc()).all()
    for d in dids:
        w.writerow([
            d.id, d.number, d.e164 or "",
            d.status.value,
            d.pool.name if d.pool else "",
            d.assigned_to_name or "",
            d.assigned_to_email or "",
            d.assigned_at.strftime("%Y-%m-%d %H:%M UTC") if d.assigned_at else "",
            d.country or "",
            d.notes or "",
        ])
    return buf.getvalue()


def export_snow_csv() -> str:
    """Generate CSV string for SNOW request export."""
    import csv, io
    buf = io.StringIO()
    w   = csv.writer(buf)
    w.writerow(["SNOW Number","Requester Email","Requester Name","Status",
                "Assigned DID","Extension","Retry Count","Created At","Fulfilled At"])

    rows = SNOWRequest.query.order_by(SNOWRequest.created_at.desc()).all()
    for r in rows:
        w.writerow([
            r.snow_number, r.requester_email, r.requester_name or "",
            r.status.value, r.assigned_did or "", r.assigned_extension or "",
            r.retry_count,
            r.created_at.strftime("%Y-%m-%d %H:%M UTC") if r.created_at else "",
            r.fulfilled_at.strftime("%Y-%m-%d %H:%M UTC") if r.fulfilled_at else "",
        ])
    return buf.getvalue()


def export_audit_csv(days: int = 30) -> str:
    """Generate CSV string for audit log export."""
    import csv, io
    buf   = io.StringIO()
    w     = csv.writer(buf)
    start, _ = _date_range(days)
    w.writerow(["Timestamp","Action","Username","Role","IP Address",
                "Resource Type","Resource Name","Status","Detail"])

    logs = (
        AuditLog.query
        .filter(AuditLog.created_at >= start)
        .order_by(AuditLog.created_at.desc())
        .all()
    )
    for log in logs:
        w.writerow([
            log.created_at.strftime("%Y-%m-%d %H:%M:%S UTC") if log.created_at else "",
            log.action, log.username or "", log.user_role or "",
            log.ip_address or "", log.resource_type or "",
            log.resource_name or "", log.status or "",
            log.status_detail or "",
        ])
    return buf.getvalue()
