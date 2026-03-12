"""
Call Forward Scheduling Blueprint.

Routes:
  GET  /admin/call-forward/                   → Schedule list
  GET  /admin/call-forward/new                → Create form
  POST /admin/call-forward/new                → Save new schedule
  GET  /admin/call-forward/<id>               → Detail view
  GET  /admin/call-forward/<id>/edit          → Edit form
  POST /admin/call-forward/<id>/edit          → Update schedule
  POST /admin/call-forward/<id>/delete        → Delete schedule
  POST /admin/call-forward/<id>/toggle        → Enable / disable schedule
  POST /admin/call-forward/<id>/ondemand      → On-demand ON / OFF
  POST /admin/call-forward/<id>/apply-now     → Force apply immediately
  POST /admin/call-forward/<id>/revert-now    → Force revert immediately
  GET  /admin/call-forward/api/status         → AJAX: all schedule statuses
  GET  /admin/call-forward/api/entity-search  → AJAX: Webex entity typeahead
"""
from datetime import datetime, timezone, time as dt_time
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, jsonify, current_app
)
from flask_login import login_required, current_user

from app.utils.decorators import gui_admin_required, _get_ip
from app.models.call_forward import (
    CallForwardSchedule, ScheduleStatus,
    EntityType, ForwardType, WEEKDAY_BITS
)
from app.models.audit import AuditLog
from app.forms.call_forward_forms import (
    CallForwardScheduleForm, OnDemandToggleForm
)
from app.services import call_forward_service as cf_svc
from app.services.webex_service import get_webex_client
from app.extensions import db

cf_bp = Blueprint(
    "call_forward", __name__,
    template_folder="../templates/call_forward",
    url_prefix="/admin/call-forward"
)


# ── List ──────────────────────────────────────────────────────────────────────

@cf_bp.route("/", methods=["GET"])
@login_required
@gui_admin_required
def schedules_list():
    status_filter  = request.args.get("status", "")
    entity_filter  = request.args.get("entity_type", "")
    search         = request.args.get("search", "").strip()

    q = CallForwardSchedule.query.order_by(CallForwardSchedule.name.asc())

    if status_filter:
        try:
            q = q.filter_by(status=ScheduleStatus(status_filter))
        except ValueError:
            pass

    if entity_filter:
        try:
            q = q.filter_by(entity_type=EntityType(entity_filter))
        except ValueError:
            pass

    if search:
        term = f"%{search}%"
        q = q.filter(
            db.or_(
                CallForwardSchedule.name.ilike(term),
                CallForwardSchedule.entity_name.ilike(term),
                CallForwardSchedule.destination.ilike(term),
            )
        )

    schedules = q.all()

    # Summary counts
    stats = {
        "total":    len(schedules),
        "active":   sum(1 for s in schedules if s.status == ScheduleStatus.ACTIVE),
        "ondemand": sum(1 for s in schedules if s.status == ScheduleStatus.ONDEMAND),
        "error":    sum(1 for s in schedules if s.status == ScheduleStatus.ERROR),
        "inactive": sum(1 for s in schedules if s.status == ScheduleStatus.INACTIVE),
    }

    return render_template(
        "schedules_list.html",
        schedules=schedules,
        stats=stats,
        status_filter=status_filter,
        entity_filter=entity_filter,
        search=search,
        ScheduleStatus=ScheduleStatus,
        EntityType=EntityType,
    )


# ── Create ────────────────────────────────────────────────────────────────────

@cf_bp.route("/new", methods=["GET", "POST"])
@login_required
@gui_admin_required
def schedule_new():
    form = CallForwardScheduleForm()

    if form.validate_on_submit():
        sched = CallForwardSchedule(
            name         = form.name.data.strip(),
            description  = (form.description.data or "").strip(),
            entity_type  = EntityType(form.entity_type.data),
            entity_id    = form.entity_id.data.strip(),
            entity_name  = form.entity_name.data.strip(),
            entity_email = (form.entity_email.data or "").strip() or None,
            forward_type = ForwardType(form.forward_type.data),
            destination  = form.destination.data.strip(),
            time_start   = form.time_start.data,
            time_end     = form.time_end.data,
            timezone_name= form.timezone_name.data,
            is_active    = form.is_active.data,
            status       = ScheduleStatus.INACTIVE,
            created_by_id= current_user.id,
        )
        sched.set_days(form.active_days.data)
        db.session.add(sched)
        db.session.commit()

        AuditLog.write(
            action="CREATE",
            user_id=current_user.id,
            username=current_user.username,
            user_role=current_user.role.value,
            ip_address=_get_ip(),
            resource_type="call_forward_schedule",
            resource_id=sched.id,
            resource_name=sched.name,
            payload_after=sched.to_dict(),
            status="success",
        )
        flash(
            f"Schedule '{sched.name}' created. "
            f"The first tick will apply it automatically within 60 seconds.",
            "success"
        )
        return redirect(url_for("call_forward.schedule_detail", sched_id=sched.id))

    # Defaults for new form
    if request.method == "GET":
        form.active_days.data = ["monday","tuesday","wednesday","thursday","friday"]
        form.time_start.data  = dt_time(18, 0)
        form.time_end.data    = dt_time(8, 0)
        form.timezone_name.data = "Europe/Brussels"

    return render_template("schedule_form.html", form=form, schedule=None)


# ── Detail ────────────────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>", methods=["GET"])
@login_required
@gui_admin_required
def schedule_detail(sched_id: int):
    sched       = CallForwardSchedule.query.get_or_404(sched_id)
    toggle_form = OnDemandToggleForm()

    # Compute next apply/revert times for display
    from app.services.call_forward_service import _local_now
    now_local  = _local_now(sched.timezone_name)
    in_window  = sched.is_in_window(now_local)

    return render_template(
        "schedule_detail.html",
        sched=sched,
        toggle_form=toggle_form,
        now_local=now_local,
        in_window=in_window,
        ScheduleStatus=ScheduleStatus,
    )


# ── Edit ──────────────────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/edit", methods=["GET", "POST"])
@login_required
@gui_admin_required
def schedule_edit(sched_id: int):
    sched = CallForwardSchedule.query.get_or_404(sched_id)
    form  = CallForwardScheduleForm(obj=sched)

    if request.method == "GET":
        form.entity_type.data   = sched.entity_type.value
        form.forward_type.data  = sched.forward_type.value
        form.timezone_name.data = sched.timezone_name
        form.active_days.data   = [
            name for name, bit in WEEKDAY_BITS.items()
            if sched.active_days & bit
        ]

    if form.validate_on_submit():
        payload_before = sched.to_dict()

        sched.name          = form.name.data.strip()
        sched.description   = (form.description.data or "").strip()
        sched.entity_type   = EntityType(form.entity_type.data)
        sched.entity_id     = form.entity_id.data.strip()
        sched.entity_name   = form.entity_name.data.strip()
        sched.entity_email  = (form.entity_email.data or "").strip() or None
        sched.forward_type  = ForwardType(form.forward_type.data)
        sched.destination   = form.destination.data.strip()
        sched.time_start    = form.time_start.data
        sched.time_end      = form.time_end.data
        sched.timezone_name = form.timezone_name.data
        sched.is_active     = form.is_active.data
        sched.set_days(form.active_days.data)
        db.session.commit()

        AuditLog.write(
            action="UPDATE",
            user_id=current_user.id,
            username=current_user.username,
            ip_address=_get_ip(),
            resource_type="call_forward_schedule",
            resource_id=sched.id,
            resource_name=sched.name,
            payload_before=payload_before,
            payload_after=sched.to_dict(),
            status="success",
        )
        flash(f"Schedule '{sched.name}' updated.", "success")
        return redirect(url_for("call_forward.schedule_detail", sched_id=sched.id))

    return render_template("schedule_form.html", form=form, schedule=sched)


# ── Delete ────────────────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/delete", methods=["POST"])
@login_required
@gui_admin_required
def schedule_delete(sched_id: int):
    sched = CallForwardSchedule.query.get_or_404(sched_id)

    # Revert first if currently active
    if sched.status == ScheduleStatus.ACTIVE:
        cf_svc.revert_forward(sched, triggered_by=current_user.username)

    AuditLog.write(
        action="DELETE",
        user_id=current_user.id,
        username=current_user.username,
        ip_address=_get_ip(),
        resource_type="call_forward_schedule",
        resource_id=sched.id,
        resource_name=sched.name,
        payload_before=sched.to_dict(),
        status="success",
    )
    name = sched.name
    db.session.delete(sched)
    db.session.commit()

    flash(f"Schedule '{name}' deleted.", "success")
    return redirect(url_for("call_forward.schedules_list"))


# ── Enable / Disable toggle ───────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/toggle", methods=["POST"])
@login_required
@gui_admin_required
def schedule_toggle(sched_id: int):
    sched          = CallForwardSchedule.query.get_or_404(sched_id)
    sched.is_active = not sched.is_active
    db.session.commit()

    if not sched.is_active and sched.status == ScheduleStatus.ACTIVE:
        cf_svc.revert_forward(sched, triggered_by=current_user.username)

    state = "enabled" if sched.is_active else "disabled"
    flash(f"Schedule '{sched.name}' {state}.", "success")
    return redirect(url_for("call_forward.schedule_detail", sched_id=sched_id))


# ── On-demand toggle ──────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/ondemand", methods=["POST"])
@login_required
@gui_admin_required
def ondemand_toggle(sched_id: int):
    sched  = CallForwardSchedule.query.get_or_404(sched_id)
    action = request.form.get("action", "on")

    if action == "on":
        ok, msg = cf_svc.ondemand_on(sched, admin_username=current_user.username)
    else:
        ok, msg = cf_svc.ondemand_off(sched, admin_username=current_user.username)

    flash(msg, "success" if ok else "danger")
    return redirect(url_for("call_forward.schedule_detail", sched_id=sched_id))


# ── Force apply now ───────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/apply-now", methods=["POST"])
@login_required
@gui_admin_required
def apply_now(sched_id: int):
    sched      = CallForwardSchedule.query.get_or_404(sched_id)
    ok, msg    = cf_svc.apply_forward(sched, triggered_by=current_user.username)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("call_forward.schedule_detail", sched_id=sched_id))


# ── Force revert now ──────────────────────────────────────────────────────────

@cf_bp.route("/<int:sched_id>/revert-now", methods=["POST"])
@login_required
@gui_admin_required
def revert_now(sched_id: int):
    sched   = CallForwardSchedule.query.get_or_404(sched_id)
    ok, msg = cf_svc.revert_forward(sched, triggered_by=current_user.username)
    flash(msg, "success" if ok else "danger")
    return redirect(url_for("call_forward.schedule_detail", sched_id=sched_id))


# ── AJAX: all statuses (dashboard widget refresh) ─────────────────────────────

@cf_bp.route("/api/status", methods=["GET"])
@login_required
@gui_admin_required
def api_status():
    schedules = CallForwardSchedule.query.all()
    return jsonify([
        {
            "id":          s.id,
            "name":        s.name,
            "status":      s.status.value,
            "entity_name": s.entity_name,
            "destination": s.destination,
            "is_ondemand": s.is_ondemand,
            "last_error":  s.last_error,
        }
        for s in schedules
    ])


# ── AJAX: Webex entity search (typeahead in form) ─────────────────────────────

@cf_bp.route("/api/entity-search", methods=["GET"])
@login_required
@gui_admin_required
def api_entity_search():
    q           = request.args.get("q", "").strip()
    entity_type = request.args.get("type", "user")

    if len(q) < 2:
        return jsonify([])

    try:
        webex   = get_webex_client()
        results = []

        if entity_type == "user":
            people = webex.org.get_people(display_name=q) or []
            results = [
                {
                    "id":    p.id,
                    "name":  p.display_name,
                    "email": getattr(p, "emails", [""])[0],
                    "label": f"{p.display_name} ({getattr(p,'emails',[''])[0]})",
                }
                for p in people[:15]
            ]

        elif entity_type == "hunt_group":
            items = webex.org.hunt_groups or []
            results = [
                {"id": g.id, "name": getattr(g,"name",""), "email": "", "label": getattr(g,"name","")}
                for g in items if q.lower() in getattr(g,"name","").lower()
            ][:15]

        elif entity_type == "auto_attendant":
            items = webex.org.auto_attendants or []
            results = [
                {"id": a.id, "name": getattr(a,"name",""), "email": "", "label": getattr(a,"name","")}
                for a in items if q.lower() in getattr(a,"name","").lower()
            ][:15]

        elif entity_type == "call_queue":
            items = webex.org.call_queues or []
            results = [
                {"id": c.id, "name": getattr(c,"name",""), "email": "", "label": getattr(c,"name","")}
                for c in items if q.lower() in getattr(c,"name","").lower()
            ][:15]

        elif entity_type == "workspace":
            items = webex.org.workspaces or []
            results = [
                {"id": w.id, "name": getattr(w,"display_name",""), "email": "",
                 "label": getattr(w,"display_name","")}
                for w in items if q.lower() in getattr(w,"display_name","").lower()
            ][:15]

    except Exception as exc:
        current_app.logger.error(f"[CFwd entity search] {exc}")
        return jsonify([])

    return jsonify(results)
