"""
Audit query service — handles filtering, pagination, export
and SHA-256 chain integrity verification of the audit log.
"""
import csv
import io
import json
import hashlib
import logging
from datetime import datetime, timezone, timedelta
from typing import Tuple

from sqlalchemy import or_, and_

from app.extensions import db
from app.models.audit import AuditLog

logger = logging.getLogger(__name__)


# ── Filtering & Pagination ────────────────────────────────────────────────────

def get_audit_page(
    search:     str  = "",
    action:     str  = "",
    resource:   str  = "",
    status:     str  = "",
    username:   str  = "",
    ip_address: str  = "",
    date_from=None,
    date_to=None,
    page:       int  = 1,
    per_page:   int  = 50,
):
    """
    Build a filtered, paginated AuditLog query.
    Returns a Flask-SQLAlchemy Pagination object.
    """
    q = AuditLog.query.order_by(AuditLog.timestamp.desc())

    if search:
        term = f"%{search}%"
        q = q.filter(or_(
            AuditLog.username.ilike(term),
            AuditLog.resource_name.ilike(term),
            AuditLog.resource_id.ilike(term),
            AuditLog.ip_address.ilike(term),
            AuditLog.status_detail.ilike(term),
            AuditLog.http_path.ilike(term),
        ))

    if action:
        q = q.filter(AuditLog.action == action)

    if resource:
        q = q.filter(AuditLog.resource_type == resource)

    if status:
        q = q.filter(AuditLog.status == status)

    if username:
        q = q.filter(AuditLog.username.ilike(f"%{username}%"))

    if ip_address:
        q = q.filter(AuditLog.ip_address.ilike(f"%{ip_address}%"))

    if date_from:
        q = q.filter(
            AuditLog.timestamp >= datetime(
                date_from.year, date_from.month, date_from.day,
                tzinfo=timezone.utc
            )
        )

    if date_to:
        q = q.filter(
            AuditLog.timestamp < datetime(
                date_to.year, date_to.month, date_to.day,
                tzinfo=timezone.utc
            ) + timedelta(days=1)
        )

    return q.paginate(page=page, per_page=per_page, error_out=False)


# ── Statistics for dashboard widgets ─────────────────────────────────────────

def get_audit_stats(days: int = 30) -> dict:
    """
    Return summary statistics for the audit dashboard header.
    Covers the last `days` calendar days.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    base   = AuditLog.query.filter(AuditLog.timestamp >= cutoff)

    total          = base.count()
    failures       = base.filter_by(status="failure").count()
    login_failures = base.filter_by(action="LOGIN_FAILED").count()
    logins         = base.filter(
        AuditLog.action.in_(["LOGIN", "LOGIN_SSO"])
    ).count()
    config_changes = base.filter(AuditLog.action == "CONFIG_UPDATED").count()

    # Unique active users
    unique_users = (
        db.session.query(AuditLog.user_id)
        .filter(AuditLog.timestamp >= cutoff)
        .filter(AuditLog.user_id.isnot(None))
        .distinct()
        .count()
    )

    return {
        "total":          total,
        "failures":       failures,
        "login_failures": login_failures,
        "logins":         logins,
        "config_changes": config_changes,
        "unique_users":   unique_users,
        "days":           days,
    }


def get_daily_counts(days: int = 14) -> list[dict]:
    """
    Return per-day counts (success vs failure) for the sparkline chart.
    Returns list of {date, success, failure} dicts, oldest first.
    """
    from sqlalchemy import func, case

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.session.query(
            func.date(AuditLog.timestamp).label("day"),
            func.count(
                case((AuditLog.status == "success", 1))
            ).label("success"),
            func.count(
                case((AuditLog.status == "failure", 1))
            ).label("failure"),
        )
        .filter(AuditLog.timestamp >= cutoff)
        .group_by(func.date(AuditLog.timestamp))
        .order_by(func.date(AuditLog.timestamp))
        .all()
    )

    return [
        {"date": str(r.day), "success": r.success, "failure": r.failure}
        for r in rows
    ]


def get_top_actors(days: int = 30, limit: int = 10) -> list[dict]:
    """Return the most active users in the audit log."""
    from sqlalchemy import func

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    rows = (
        db.session.query(
            AuditLog.username,
            AuditLog.user_role,
            func.count(AuditLog.id).label("count"),
        )
        .filter(AuditLog.timestamp >= cutoff)
        .filter(AuditLog.username != "system")
        .group_by(AuditLog.username, AuditLog.user_role)
        .order_by(func.count(AuditLog.id).desc())
        .limit(limit)
        .all()
    )

    return [
        {"username": r.username, "role": r.user_role, "count": r.count}
        for r in rows
    ]


# ── Export ────────────────────────────────────────────────────────────────────

EXPORT_COLUMNS = [
    "id", "timestamp", "username", "user_role", "ip_address",
    "action", "resource_type", "resource_id", "resource_name",
    "status", "status_detail", "http_method", "http_path",
    "http_status", "row_hash",
]


def export_csv(filters: dict) -> io.StringIO:
    """
    Stream audit log rows matching filters as CSV.
    Returns a StringIO buffer for Flask's send_file.
    """
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=EXPORT_COLUMNS, extrasaction="ignore")
    writer.writeheader()

    q = _build_export_query(filters)
    for entry in q.yield_per(500):
        row = entry.to_dict()
        row["timestamp"] = entry.timestamp.isoformat()
        row["row_hash"]  = entry.row_hash or ""
        writer.writerow({col: row.get(col, "") for col in EXPORT_COLUMNS})

    buf.seek(0)
    return buf


def export_json(filters: dict) -> io.StringIO:
    """Export audit log as newline-delimited JSON."""
    buf = io.StringIO()
    q   = _build_export_query(filters)

    buf.write("[\n")
    first = True
    for entry in q.yield_per(500):
        if not first:
            buf.write(",\n")
        d = entry.to_dict()
        d["timestamp"]      = entry.timestamp.isoformat()
        d["row_hash"]       = entry.row_hash or ""
        d["payload_before"] = entry.payload_before
        d["payload_after"]  = entry.payload_after
        buf.write(json.dumps(d))
        first = False
    buf.write("\n]")
    buf.seek(0)
    return buf


def _build_export_query(filters: dict):
    """Build a query from a flat filter dict for export."""
    q = AuditLog.query.order_by(AuditLog.timestamp.asc())

    if filters.get("date_from"):
        q = q.filter(AuditLog.timestamp >= filters["date_from"])
    if filters.get("date_to"):
        q = q.filter(AuditLog.timestamp <= filters["date_to"])
    if filters.get("action"):
        q = q.filter(AuditLog.action == filters["action"])
    if filters.get("resource"):
        q = q.filter(AuditLog.resource_type == filters["resource"])
    if filters.get("status"):
        q = q.filter(AuditLog.status == filters["status"])
    if filters.get("username"):
        q = q.filter(AuditLog.username.ilike(f"%{filters['username']}%"))

    return q


# ── Chain Integrity Verification ──────────────────────────────────────────────

def verify_chain_integrity(
    limit: int = 5000,
    offset: int = 0
) -> Tuple[bool, str, dict]:
    """
    Verify the SHA-256 chain hash of audit log entries.
    Reads `limit` rows starting at `offset` (ordered by id ASC).
    Returns (ok, message, stats_dict).

    The chain is intact if every entry's stored row_hash matches a fresh
    re-computation using the previous entry's hash. A mismatch indicates
    that a row was modified or deleted outside the application.
    """
    entries = (
        AuditLog.query
        .order_by(AuditLog.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    if not entries:
        return True, "No audit log entries to verify.", {
            "checked": 0, "corrupted": [], "ok": True
        }

    corrupted    = []
    prev_hash    = ""
    checked      = 0

    for entry in entries:
        expected = entry.compute_hash(prev_hash)
        if entry.row_hash and entry.row_hash != expected:
            corrupted.append({
                "id":        entry.id,
                "timestamp": entry.timestamp.isoformat(),
                "action":    entry.action,
                "username":  entry.username,
                "stored":    entry.row_hash,
                "expected":  expected,
            })
            logger.warning(
                f"[AuditIntegrity] Hash mismatch at id={entry.id} "
                f"(action={entry.action}, user={entry.username})"
            )
        prev_hash = entry.row_hash or expected
        checked  += 1

    ok  = len(corrupted) == 0
    msg = (
        f"Chain verified — {checked} entries checked, no tampering detected."
        if ok else
        f"⚠ Chain BROKEN — {len(corrupted)} corrupted "
        f"entr{'y' if len(corrupted)==1 else 'ies'} detected out of {checked} checked."
    )

    return ok, msg, {
        "checked":   checked,
        "corrupted": corrupted,
        "ok":        ok,
        "offset":    offset,
        "limit":     limit,
    }
