"""
ServiceNow Blueprint.

Public routes (no auth — webhook receiver):
  POST /api/snow/webhook               ← SNOW sends here on new request

Admin routes (gui_admin_required):
  GET  /admin/snow/                    ← Request list
  GET  /admin/snow/<id>                ← Request detail
  POST /admin/snow/<id>/retry          ← Manual retry
  POST /admin/snow/<id>/mark-fulfilled ← Manual close as fulfilled
  POST /admin/snow/<id>/mark-failed    ← Manual close as failed
  GET  /admin/snow/config              ← SNOW integration settings
  POST /admin/snow/config              ← Save settings
  POST /admin/snow/api/test-connection ← AJAX: test SNOW credentials
  GET  /admin/snow/api/stats           ← AJAX: request stats for dashboard
"""
import hashlib
import hmac
import json
import logging
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, request, redirect,
    url_for, flash, jsonify, current_app
)
from flask_login import login_required, current_user

from app.utils.decorators import gui_admin_required, audit_action, _get_ip
from app.models.snow import SNOWRequest, RequestStatus
from app.models.did import DIDPool
from app.models.audit import AuditLog
from app.models.app_config import AppConfig
from app.forms.snow_forms import SNOWConfigForm, ManualFulfillForm
from app.services import snow_fulfillment_service as fulfillment_svc
from app.services import snow_service as snow_api
from app.extensions import db
from app.utils.crypto import encrypt

logger = logging.getLogger(__name__)

snow_bp = Blueprint(
    "snow", __name__,
    template_folder="../templates/snow",
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# PUBLIC — Webhook receiver
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@snow_bp.route("/api/snow/webhook", methods=["POST"])
def webhook():
    """
    Receive a ServiceNow outbound REST webhook.

    Expected JSON body (minimum):
        {
          "number":           "RITM0012345",
          "sys_id":           "<sysid>",
          "requested_for":    {"email": "user@company.com", "display_value": "Jane Doe"},
          "short_description": "Webex Calling provisioning"
        }

    Optional extras:
        "did_pool_id": <int>   ← override default pool
    """
    raw_body  = request.get_data()
    signature = request.headers.get("X-Orbit-Signature", "")

    # ── Signature verification ────────────────────────────────────────────────
    if not fulfillment_svc.validate_webhook_signature(raw_body, signature):
        logger.warning(
            f"[SNOW Webhook] Signature mismatch — IP {request.remote_addr}"
        )
        AuditLog.write(
            action="SNOW_REQUEST_RECEIVED",
            username="system",
            ip_address=request.remote_addr,
            resource_type="snow_request",
            resource_name="webhook",
            status="failure",
            status_detail="Signature verification failed.",
        )
        return jsonify({"error": "Forbidden — invalid signature."}), 403

    # ── Parse payload ─────────────────────────────────────────────────────────
    try:
        data = request.get_json(force=True, silent=True) or {}
    except Exception:
        return jsonify({"error": "Invalid JSON body."}), 400

    if not data:
        return jsonify({"error": "Empty payload."}), 400

    parsed = fulfillment_svc.parse_snow_payload(data)

    if not parsed["snow_number"]:
        return jsonify({"error": "Missing request number (number / ritm_number)."}), 400

    if not parsed["requester_email"]:
        return jsonify({"error": "Missing requester email."}), 400

    # ── Idempotency check ─────────────────────────────────────────────────────
    existing = SNOWRequest.query.filter_by(
        snow_number=parsed["snow_number"]
    ).first()

    if existing:
        logger.info(
            f"[SNOW Webhook] Duplicate: {parsed['snow_number']} "
            f"already in DB (status={existing.status.value})"
        )
        return jsonify({
            "status":   "duplicate",
            "message":  f"Request {parsed['snow_number']} already received.",
            "db_id":    existing.id,
        }), 200

    # ── Persist SNOWRequest record ────────────────────────────────────────────
    req = SNOWRequest(
        snow_number=parsed["snow_number"],
        snow_sys_id=parsed["snow_sys_id"],
        requester_email=parsed["requester_email"],
        requester_name=parsed["requester_name"],
        short_description=parsed["short_description"],
        requested_did_pool_id=parsed["requested_did_pool_id"],
        status=RequestStatus.PENDING,
        raw_payload=parsed["raw_payload"],
        received_at=datetime.now(timezone.utc),
    )
    req.add_log("Webhook received and validated.")
    db.session.add(req)
    db.session.commit()

    AuditLog.write(
        action="SNOW_REQUEST_RECEIVED",
        username="system",
        ip_address=request.remote_addr,
        resource_type="snow_request",
        resource_id=req.id,
        resource_name=req.snow_number,
        payload_after=parsed,
        status="success",
    )

    # ── Dispatch Celery fulfillment task ──────────────────────────────────────
    auto_fulfill = AppConfig.get("SNOW_AUTO_FULFILL", "true").lower() == "true"
    task_id      = None

    if auto_fulfill:
        from app.tasks.snow import fulfill_snow_request
        task    = fulfill_snow_request.delay(req.id)
        task_id = task.id
        req.celery_task_id = task_id
        db.session.commit()
        req.add_log(f"Fulfillment task queued (Celery task: {task_id[:12]}…).")

    logger.info(
        f"[SNOW Webhook] Ingested {req.snow_number} for {req.requester_email} "
        f"— auto_fulfill={auto_fulfill}"
    )

    return jsonify({
        "status":         "accepted",
        "db_id":          req.id,
        "snow_number":    req.snow_number,
        "auto_fulfill":   auto_fulfill,
        "celery_task_id": task_id,
    }), 202


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADMIN — Request list
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@snow_bp.route("/admin/snow/", methods=["GET"])
@login_required
@gui_admin_required
def requests_list():
    status_filter = request.args.get("status", "")
    search        = request.args.get("search", "").strip()
    page          = int(request.args.get("page", 1))
    per_page      = int(request.args.get("per_page", 50))

    q = SNOWRequest.query.order_by(SNOWRequest.received_at.desc())

    if status_filter:
        try:
            q = q.filter_by(status=RequestStatus(status_filter))
        except ValueError:
            pass

    if search:
        term = f"%{search}%"
        q = q.filter(
            db.or_(
                SNOWRequest.snow_number.ilike(term),
                SNOWRequest.requester_email.ilike(term),
                SNOWRequest.requester_name.ilike(term),
                SNOWRequest.assigned_did.ilike(term),
            )
        )

    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    # Summary stats
    stats = _get_request_stats()

    return render_template(
        "requests_list.html",
        pagination=pagination,
        requests=pagination.items,
        status_filter=status_filter,
        search=search,
        page=page,
        per_page=per_page,
        stats=stats,
        RequestStatus=RequestStatus,
    )


# ── Request detail ────────────────────────────────────────────────────────────

@snow_bp.route("/admin/snow/<int:req_id>", methods=["GET"])
@login_required
@gui_admin_required
def request_detail(req_id: int):
    req  = SNOWRequest.query.get_or_404(req_id)
    form = ManualFulfillForm(
        snow_request_id=req.snow_number,
        user_email=req.requester_email,
    )
    _populate_pool_choices(form)

    return render_template(
        "request_detail.html",
        req=req,
        form=form,
        RequestStatus=RequestStatus,
    )


# ── Manual retry ──────────────────────────────────────────────────────────────

@snow_bp.route("/admin/snow/<int:req_id>/retry", methods=["POST"])
@login_required
@gui_admin_required
def request_retry(req_id: int):
    req = SNOWRequest.query.get_or_404(req_id)

    if req.status == RequestStatus.FULFILLED:
        flash(f"Request {req.snow_number} is already fulfilled.", "warning")
        return redirect(url_for("snow.request_detail", req_id=req_id))

    req.transition(RequestStatus.PENDING)
    req.failure_reason = None
    req.retry_count    = (req.retry_count or 0) + 1
    req.add_log(
        f"Manual retry triggered by {current_user.username} "
        f"(attempt #{req.retry_count})."
    )
    db.session.commit()

    from app.tasks.snow import fulfill_snow_request
    task = fulfill_snow_request.delay(req.id)
    req.celery_task_id = task.id
    db.session.commit()

    AuditLog.write(
        action="SNOW_REQUEST_RECEIVED",
        user_id=current_user.id,
        username=current_user.username,
        ip_address=_get_ip(),
        resource_type="snow_request",
        resource_id=req.id,
        resource_name=req.snow_number,
        status="success",
        status_detail="Manual retry queued.",
    )

    flash(
        f"Fulfillment retry queued for {req.snow_number}. "
        f"Celery task: {task.id[:12]}…",
        "info"
    )
    return redirect(url_for("snow.request_detail", req_id=req_id))


# ── Manual fulfill (override) ─────────────────────────────────────────────────

@snow_bp.route("/admin/snow/<int:req_id>/mark-fulfilled", methods=["POST"])
@login_required
@gui_admin_required
def mark_fulfilled(req_id: int):
    req  = SNOWRequest.query.get_or_404(req_id)
    form = ManualFulfillForm()
    _populate_pool_choices(form)

    if form.validate_on_submit():
        pool_id    = form.did_pool_id.data
        user_email = form.user_email.data.strip()
        notes      = form.notes.data.strip() if form.notes.data else ""

        ok, msg, did = did_svc_ref().auto_provision_did(
            pool_id=pool_id,
            user_email=user_email,
            username=current_user.username,
            snow_request_id=req.snow_number,
        )

        if ok and did:
            req.assigned_did     = did
            req.requester_email  = user_email
            req.fulfilled_at     = datetime.now(timezone.utc)
            req.transition(RequestStatus.FULFILLED)
            req.add_log(
                f"Manually fulfilled by {current_user.username}. "
                f"DID: {did}. Notes: {notes}"
            )
            db.session.commit()
            flash(f"Request {req.snow_number} manually fulfilled — DID {did}.", "success")
        else:
            flash(f"Manual fulfillment failed: {msg}", "danger")

    else:
        flash("Form validation failed. Check all fields.", "danger")

    return redirect(url_for("snow.request_detail", req_id=req_id))


# ── Mark failed ───────────────────────────────────────────────────────────────

@snow_bp.route("/admin/snow/<int:req_id>/mark-failed", methods=["POST"])
@login_required
@gui_admin_required
def mark_failed(req_id: int):
    req    = SNOWRequest.query.get_or_404(req_id)
    reason = request.form.get("reason", "Manually marked as failed by admin.").strip()

    req.transition(RequestStatus.FAILED)
    req.failure_reason = reason
    req.add_log(f"Marked failed by {current_user.username}: {reason}")
    db.session.commit()

    AuditLog.write(
        action="SNOW_REQUEST_FAILED",
        user_id=current_user.id,
        username=current_user.username,
        resource_type="snow_request",
        resource_id=req.id,
        resource_name=req.snow_number,
        status="failure",
        status_detail=f"Manual close by {current_user.username}: {reason}",
    )

    flash(f"Request {req.snow_number} marked as failed.", "warning")
    return redirect(url_for("snow.request_detail", req_id=req_id))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ADMIN — Configuration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@snow_bp.route("/admin/snow/config", methods=["GET", "POST"])
@login_required
@gui_admin_required
def snow_config():
    form = SNOWConfigForm()
    _populate_pool_choices(form)

    if request.method == "GET":
        # Pre-populate from AppConfig
        form.snow_instance.data         = AppConfig.get("SNOW_INSTANCE", "")
        form.snow_username.data         = AppConfig.get("SNOW_USERNAME", "")
        form.snow_catalog_item_id.data  = AppConfig.get("SNOW_CATALOG_ITEM_ID", "")
        form.snow_assignment_group.data = AppConfig.get("SNOW_ASSIGNMENT_GROUP", "")
        form.snow_fulfilled_state.data  = int(AppConfig.get("SNOW_FULFILLED_STATE", "3"))
        form.snow_failed_state.data     = int(AppConfig.get("SNOW_FAILED_STATE", "4"))
        form.auto_fulfill.data          = AppConfig.get("SNOW_AUTO_FULFILL", "true").lower() == "true"
        form.send_welcome_email.data    = AppConfig.get("SNOW_SEND_WELCOME_EMAIL", "true").lower() == "true"
        form.send_did_email.data        = AppConfig.get("SNOW_SEND_DID_EMAIL", "true").lower() == "true"

        raw_pool = AppConfig.get("DEFAULT_DID_POOL_ID", "0")
        try:
            form.default_did_pool_id.data = int(raw_pool) if raw_pool else 0
        except ValueError:
            form.default_did_pool_id.data = 0

    if form.validate_on_submit():
        payload_before = {
            "instance": AppConfig.get("SNOW_INSTANCE", ""),
            "username": AppConfig.get("SNOW_USERNAME", ""),
        }

        AppConfig.set("SNOW_INSTANCE",          form.snow_instance.data.strip())
        AppConfig.set("SNOW_USERNAME",          form.snow_username.data.strip())
        AppConfig.set("SNOW_CATALOG_ITEM_ID",   form.snow_catalog_item_id.data or "")
        AppConfig.set("SNOW_ASSIGNMENT_GROUP",  form.snow_assignment_group.data or "")
        AppConfig.set("SNOW_FULFILLED_STATE",   str(form.snow_fulfilled_state.data or 3))
        AppConfig.set("SNOW_FAILED_STATE",      str(form.snow_failed_state.data or 4))
        AppConfig.set("SNOW_AUTO_FULFILL",      "true" if form.auto_fulfill.data else "false")
        AppConfig.set("SNOW_SEND_WELCOME_EMAIL","true" if form.send_welcome_email.data else "false")
        AppConfig.set("SNOW_SEND_DID_EMAIL",    "true" if form.send_did_email.data else "false")
        AppConfig.set("DEFAULT_DID_POOL_ID",    str(form.default_did_pool_id.data or 0))

        # Only update secrets if new values were provided
        if form.snow_password.data:
            AppConfig.set("SNOW_PASSWORD", encrypt(form.snow_password.data), encrypted=True)
        if form.snow_webhook_secret.data:
            AppConfig.set("SNOW_WEBHOOK_SECRET", encrypt(form.snow_webhook_secret.data), encrypted=True)

        AuditLog.write(
            action="CONFIG_UPDATED",
            user_id=current_user.id,
            username=current_user.username,
            ip_address=_get_ip(),
            resource_type="app_config",
            resource_name="SNOW Integration Settings",
            payload_before=payload_before,
            payload_after={
                "instance": form.snow_instance.data,
                "username": form.snow_username.data,
                "auto_fulfill": form.auto_fulfill.data,
            },
            status="success",
        )
        flash("ServiceNow integration settings saved.", "success")
        return redirect(url_for("snow.snow_config"))

    return render_template("config.html", form=form)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# AJAX APIs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

@snow_bp.route("/admin/snow/api/test-connection", methods=["POST"])
@login_required
@gui_admin_required
def api_test_connection():
    ok, msg, info = snow_api.test_connection()
    return jsonify({"ok": ok, "message": msg, "info": info})


@snow_bp.route("/admin/snow/api/stats", methods=["GET"])
@login_required
@gui_admin_required
def api_stats():
    return jsonify(_get_request_stats())


@snow_bp.route("/admin/snow/api/request-status/<int:req_id>", methods=["GET"])
@login_required
@gui_admin_required
def api_request_status(req_id: int):
    """Poll endpoint for live status updates on the detail page."""
    req = SNOWRequest.query.get_or_404(req_id)
    return jsonify({
        "id":              req.id,
        "status":          req.status.value,
        "assigned_did":    req.assigned_did,
        "failure_reason":  req.failure_reason,
        "fulfilled_at":    req.fulfilled_at.isoformat() if req.fulfilled_at else None,
        "log_entries":     req.fulfillment_log or [],
    })


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _populate_pool_choices(form) -> None:
    pools = DIDPool.query.filter_by(is_active=True).order_by(DIDPool.name).all()
    if hasattr(form, "default_did_pool_id"):
        form.default_did_pool_id.choices = [(0, "— No default pool —")] + [
            (p.id, f"{p.name} ({p.available_count} available)") for p in pools
        ]
    if hasattr(form, "did_pool_id"):
        form.did_pool_id.choices = [
            (p.id, f"{p.name} ({p.available_count} available)") for p in pools
        ]


def _get_request_stats() -> dict:
    from sqlalchemy import func
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    base   = SNOWRequest.query.filter(SNOWRequest.received_at >= cutoff)

    return {
        "total":      base.count(),
        "pending":    base.filter_by(status=RequestStatus.PENDING).count(),
        "processing": base.filter_by(status=RequestStatus.PROCESSING).count(),
        "fulfilled":  base.filter_by(status=RequestStatus.FULFILLED).count(),
        "failed":     base.filter_by(status=RequestStatus.FAILED).count(),
    }


def did_svc_ref():
    """Lazy import to avoid circular deps."""
    from app.services import did_provision_service
    return did_provision_service
