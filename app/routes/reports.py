"""
Reports & Analytics Blueprint.

Routes:
  GET  /admin/reports/                      → Main analytics dashboard
  GET  /admin/reports/did                   → DID utilisation report
  GET  /admin/reports/snow                  → SNOW fulfillment report
  GET  /admin/reports/call-forward          → Call forward activity report
  GET  /admin/reports/audit                 → Audit log viewer + filters
  GET  /admin/reports/export/did.csv        → DID export CSV
  GET  /admin/reports/export/snow.csv       → SNOW export CSV
  GET  /admin/reports/export/audit.csv      → Audit export CSV
  GET  /admin/reports/api/dashboard         → AJAX: dashboard KPIs
  GET  /admin/reports/api/did-status        → AJAX: DID status breakdown
  GET  /admin/reports/api/did-pool          → AJAX: pool utilisation
  GET  /admin/reports/api/did-trend         → AJAX: DID assignment trend
  GET  /admin/reports/api/snow-trend        → AJAX: SNOW daily trend
  GET  /admin/reports/api/snow-status       → AJAX: SNOW status breakdown
  GET  /admin/reports/api/audit-trend       → AJAX: audit daily trend
  GET  /admin/reports/api/audit-actions     → AJAX: audit action breakdown
  GET  /admin/reports/api/cf-trend          → AJAX: call forward execution trend
"""
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request,
    jsonify, Response, current_app
)
from flask_login import login_required

from app.utils.decorators import gui_admin_required
from app.services import reports_service as rpt

reports_bp = Blueprint(
    "reports", __name__,
    template_folder="../templates/reports",
    url_prefix="/admin/reports"
)


# ── Utility ───────────────────────────────────────────────────────────────────

def _days() -> int:
    """Read ?days= query param; default 30, clamp to [7, 365]."""
    try:
        d = int(request.args.get("days", 30))
        return max(7, min(365, d))
    except (ValueError, TypeError):
        return 30


# ═════════════════════════════════════════════════════════════════════════════
# PAGE ROUTES
# ═════════════════════════════════════════════════════════════════════════════

@reports_bp.route("/", methods=["GET"])
@login_required
@gui_admin_required
def dashboard():
    summary = rpt.get_dashboard_summary()
    return render_template(
        "dashboard.html",
        summary=summary,
        days=_days(),
    )


@reports_bp.route("/did", methods=["GET"])
@login_required
@gui_admin_required
def did_report():
    days        = _days()
    pool_data   = rpt.get_did_pool_utilisation()
    status_data = rpt.get_did_status_breakdown()
    quarantine  = rpt.get_did_quarantine_list()
    trend       = rpt.get_did_assignment_trend(days)
    return render_template(
        "did_report.html",
        pool_data=pool_data,
        status_data=status_data,
        quarantine=quarantine,
        trend=trend,
        days=days,
    )


@reports_bp.route("/snow", methods=["GET"])
@login_required
@gui_admin_required
def snow_report():
    days         = _days()
    status_data  = rpt.get_snow_status_breakdown()
    trend        = rpt.get_snow_daily_trend(days)
    recent       = rpt.get_snow_recent_requests(20)
    failed       = rpt.get_snow_failed_requests()
    avg_time     = rpt.get_snow_avg_fulfillment_time()
    return render_template(
        "snow_report.html",
        status_data=status_data,
        trend=trend,
        recent=recent,
        failed=failed,
        avg_fulfillment_time=avg_time,
        days=days,
    )


@reports_bp.route("/call-forward", methods=["GET"])
@login_required
@gui_admin_required
def call_forward_report():
    days     = _days()
    trend    = rpt.get_cf_execution_trend(days)
    summary  = rpt.get_cf_schedule_summary()
    return render_template(
        "call_forward_report.html",
        trend=trend,
        summary=summary,
        days=days,
    )


@reports_bp.route("/audit", methods=["GET"])
@login_required
@gui_admin_required
def audit_report():
    days     = _days()
    page     = max(1, int(request.args.get("page", 1)))
    per_page = 50

    filters = {
        "action":        request.args.get("action", "").strip(),
        "username":      request.args.get("username", "").strip(),
        "resource_type": request.args.get("resource_type", "").strip(),
        "status":        request.args.get("status", "").strip(),
    }

    log_data   = rpt.get_audit_log_page(
        page=page, per_page=per_page, days=days, **filters
    )
    trend      = rpt.get_audit_daily_trend(days)
    actions    = rpt.get_audit_action_breakdown(days)
    top_users  = rpt.get_audit_top_users(days)

    return render_template(
        "audit_report.html",
        log_data=log_data,
        trend=trend,
        actions=actions,
        top_users=top_users,
        days=days,
        page=page,
        filters=filters,
    )


# ═════════════════════════════════════════════════════════════════════════════
# CSV EXPORTS
# ═════════════════════════════════════════════════════════════════════════════

@reports_bp.route("/export/did.csv")
@login_required
@gui_admin_required
def export_did():
    csv_data  = rpt.export_did_csv()
    filename  = f"orbit_dids_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reports_bp.route("/export/snow.csv")
@login_required
@gui_admin_required
def export_snow():
    csv_data = rpt.export_snow_csv()
    filename = f"orbit_snow_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@reports_bp.route("/export/audit.csv")
@login_required
@gui_admin_required
def export_audit():
    days     = _days()
    csv_data = rpt.export_audit_csv(days)
    filename = f"orbit_audit_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.csv"
    return Response(
        csv_data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ═════════════════════════════════════════════════════════════════════════════
# AJAX DATA ENDPOINTS (consumed by Chart.js on all report pages)
# ═════════════════════════════════════════════════════════════════════════════

@reports_bp.route("/api/dashboard")
@login_required
@gui_admin_required
def api_dashboard():
    return jsonify(rpt.get_dashboard_summary())


@reports_bp.route("/api/did-status")
@login_required
@gui_admin_required
def api_did_status():
    return jsonify(rpt.get_did_status_breakdown())


@reports_bp.route("/api/did-pool")
@login_required
@gui_admin_required
def api_did_pool():
    return jsonify(rpt.get_did_pool_utilisation())


@reports_bp.route("/api/did-trend")
@login_required
@gui_admin_required
def api_did_trend():
    return jsonify(rpt.get_did_assignment_trend(_days()))


@reports_bp.route("/api/snow-trend")
@login_required
@gui_admin_required
def api_snow_trend():
    return jsonify(rpt.get_snow_daily_trend(_days()))


@reports_bp.route("/api/snow-status")
@login_required
@gui_admin_required
def api_snow_status():
    return jsonify(rpt.get_snow_status_breakdown())


@reports_bp.route("/api/audit-trend")
@login_required
@gui_admin_required
def api_audit_trend():
    return jsonify(rpt.get_audit_daily_trend(_days()))


@reports_bp.route("/api/audit-actions")
@login_required
@gui_admin_required
def api_audit_actions():
    return jsonify(rpt.get_audit_action_breakdown(_days()))


@reports_bp.route("/api/cf-trend")
@login_required
@gui_admin_required
def api_cf_trend():
    return jsonify(rpt.get_cf_execution_trend(_days()))
