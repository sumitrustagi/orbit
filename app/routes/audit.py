"""
Audit log Blueprint — list, detail, export, chain integrity verify.
All routes require GUI_ADMIN or PLATFORM_ADMIN role.
"""
import io
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, send_file, current_app
)
from flask_login import login_required, current_user

from app.utils.decorators import gui_admin_required, audit_action, _get_ip
from app.models.audit import AuditLog
from app.models.user import UserRole
from app.forms.audit_forms import AuditFilterForm
from app.services import audit_query_service as svc

audit_bp = Blueprint(
    "audit", __name__,
    template_folder="../templates/audit",
    url_prefix="/admin/audit"
)


# ── Helper: extract filter kwargs from request.args ───────────────────────────

def _collect_filters() -> dict:
    return {
        "search":     request.args.get("search",     "").strip(),
        "action":     request.args.get("action",     "").strip(),
        "resource":   request.args.get("resource",   "").strip(),
        "status":     request.args.get("status",     "").strip(),
        "username":   request.args.get("username",   "").strip(),
        "ip_address": request.args.get("ip_address", "").strip(),
        "date_from":  _parse_date(request.args.get("date_from")),
        "date_to":    _parse_date(request.args.get("date_to")),
        "per_page":   int(request.args.get("per_page", 50)),
    }


def _parse_date(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── List ──────────────────────────────────────────────────────────────────────

@audit_bp.route("/", methods=["GET"])
@login_required
@gui_admin_required
def list_logs():
    filters  = _collect_filters()
    page     = int(request.args.get("page", 1))

    pagination = svc.get_audit_page(
        search=filters["search"],
        action=filters["action"],
        resource=filters["resource"],
        status=filters["status"],
        username=filters["username"],
        ip_address=filters["ip_address"],
        date_from=filters["date_from"],
        date_to=filters["date_to"],
        page=page,
        per_page=filters["per_page"],
    )

    form         = AuditFilterForm(data={**filters, "per_page": filters["per_page"]})
    stats        = svc.get_audit_stats(days=30)
    daily_counts = svc.get_daily_counts(days=14)

    return render_template(
        "list.html",
        pagination=pagination,
        entries=pagination.items,
        form=form,
        filters=filters,
        stats=stats,
        daily_counts=daily_counts,
        page=page,
    )


# ── Detail ────────────────────────────────────────────────────────────────────

@audit_bp.route("/<int:entry_id>", methods=["GET"])
@login_required
@gui_admin_required
def detail(entry_id: int):
    entry = AuditLog.query.get_or_404(entry_id)

    # Get adjacent entries for prev/next navigation
    prev_entry = (
        AuditLog.query
        .filter(AuditLog.id < entry_id)
        .order_by(AuditLog.id.desc())
        .first()
    )
    next_entry = (
        AuditLog.query
        .filter(AuditLog.id > entry_id)
        .order_by(AuditLog.id.asc())
        .first()
    )

    return render_template(
        "detail.html",
        entry=entry,
        prev_entry=prev_entry,
        next_entry=next_entry,
    )


# ── Export page ───────────────────────────────────────────────────────────────

@audit_bp.route("/export", methods=["GET"])
@login_required
@gui_admin_required
def export_page():
    stats     = svc.get_audit_stats(days=30)
    top_actors = svc.get_top_actors(days=30)
    return render_template(
        "export.html",
        stats=stats,
        top_actors=top_actors,
        now=datetime.now(timezone.utc),
    )


# ── Download CSV ──────────────────────────────────────────────────────────────

@audit_bp.route("/export/csv", methods=["GET"])
@login_required
@gui_admin_required
@audit_action("EXPORT", "audit_log")
def export_csv():
    filters = _collect_filters()
    buf     = svc.export_csv(filters)

    filename = (
        f"orbit_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    )
    return send_file(
        io.BytesIO(buf.read().encode("utf-8-sig")),
        mimetype="text/csv",
        as_attachment=True,
        download_name=filename,
    )


# ── Download JSON ─────────────────────────────────────────────────────────────

@audit_bp.route("/export/json", methods=["GET"])
@login_required
@gui_admin_required
@audit_action("EXPORT", "audit_log")
def export_json():
    filters = _collect_filters()
    buf     = svc.export_json(filters)

    filename = (
        f"orbit_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.json"
    )
    return send_file(
        io.BytesIO(buf.read().encode("utf-8")),
        mimetype="application/json",
        as_attachment=True,
        download_name=filename,
    )


# ── Chain Integrity Verification (AJAX) ───────────────────────────────────────

@audit_bp.route("/api/verify-integrity", methods=["POST"])
@login_required
@gui_admin_required
def api_verify_integrity():
    """
    Run chain integrity check and return JSON result.
    Accepts optional JSON body: { "limit": 5000, "offset": 0 }
    """
    body   = request.get_json(silent=True) or {}
    limit  = min(int(body.get("limit",  5000)), 50_000)
    offset = max(int(body.get("offset", 0)),    0)

    ok, msg, stats = svc.verify_chain_integrity(limit=limit, offset=offset)

    AuditLog.write(
        action="AUDIT_INTEGRITY_CHECK",
        user_id=current_user.id,
        username=current_user.username,
        user_role=current_user.role.value,
        ip_address=_get_ip(),
        resource_type="audit_log",
        resource_name=f"Checked {stats['checked']} entries",
        status="success" if ok else "failure",
        status_detail=msg,
    )

    return jsonify({
        "ok":        ok,
        "message":   msg,
        "checked":   stats["checked"],
        "corrupted": stats["corrupted"],
        "offset":    offset,
        "limit":     limit,
    })


# ── Stats API (for dashboard widget refresh) ──────────────────────────────────

@audit_bp.route("/api/stats", methods=["GET"])
@login_required
@gui_admin_required
def api_stats():
    days  = int(request.args.get("days", 30))
    return jsonify({
        "stats":       svc.get_audit_stats(days=days),
        "daily":       svc.get_daily_counts(days=14),
        "top_actors":  svc.get_top_actors(days=days),
    })


# ── Quick search (AJAX typeahead for username) ────────────────────────────────

@audit_bp.route("/api/usernames", methods=["GET"])
@login_required
@gui_admin_required
def api_usernames():
    q    = request.args.get("q", "").strip()
    rows = (
        AuditLog.query
        .with_entities(AuditLog.username)
        .filter(AuditLog.username.ilike(f"%{q}%"))
        .distinct()
        .limit(20)
        .all()
    )
    return jsonify([r.username for r in rows if r.username])
