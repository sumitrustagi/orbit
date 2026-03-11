"""
DID Management Blueprint.

Routes:
  GET  /admin/did/                    → Pool list
  GET  /admin/did/pools/new           → Create pool form
  POST /admin/did/pools/new           → Create pool
  GET  /admin/did/pools/<id>          → Pool detail + numbers table
  GET  /admin/did/pools/<id>/edit     → Edit pool form
  POST /admin/did/pools/<id>/edit     → Update pool
  POST /admin/did/pools/<id>/delete   → Soft-delete pool
  POST /admin/did/pools/<id>/sync     → Trigger Celery sync task
  POST /admin/did/api/assign          → AJAX: assign a number
  POST /admin/did/api/release         → AJAX: release a number
  GET  /admin/did/api/search-entity   → AJAX: Webex entity typeahead
  GET  /admin/did/api/locations       → AJAX: Webex locations list
"""
from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, jsonify, current_app
)
from flask_login import login_required, current_user

from app.utils.decorators import gui_admin_required, audit_action, _get_ip
from app.models.did import DIDPool, DIDAssignment, DIDStatus
from app.models.audit import AuditLog
from app.forms.did_forms import DIDPoolForm, ManualAssignForm
from app.services import did_provision_service as provision_svc
from app.services.webex_service import get_webex_client
from app.extensions import db

did_bp = Blueprint(
    "did", __name__,
    template_folder="../templates/did",
    url_prefix="/admin/did"
)


# ── Pool List ─────────────────────────────────────────────────────────────────

@did_bp.route("/", methods=["GET"])
@login_required
@gui_admin_required
def pools_list():
    pools       = DIDPool.query.order_by(DIDPool.created_at.desc()).all()
    total_avail = sum(p.available_count for p in pools)
    total_asgn  = sum(p.assigned_count  for p in pools)
    total_nums  = sum(p.total_count     for p in pools)

    return render_template(
        "pools_list.html",
        pools=pools,
        total_avail=total_avail,
        total_asgn=total_asgn,
        total_nums=total_nums,
    )


# ── Create Pool ───────────────────────────────────────────────────────────────

@did_bp.route("/pools/new", methods=["GET", "POST"])
@login_required
@gui_admin_required
def pool_new():
    form = DIDPoolForm()
    _populate_location_choices(form)

    if form.validate_on_submit():
        pool = DIDPool(
            name          = form.name.data.strip(),
            description   = form.description.data.strip() if form.description.data else "",
            location_id   = form.location_id.data,
            location_name = _get_location_name(form.location_id.data),
            range_start   = form.range_start.data.strip(),
            range_end     = form.range_end.data.strip(),
            is_active     = form.is_active.data,
        )
        db.session.add(pool)
        db.session.flush()   # Get pool.id before populate

        created, _ = provision_svc.populate_pool(pool, admin_username=current_user.username)

        AuditLog.write(
            action="CREATE",
            user_id=current_user.id,
            username=current_user.username,
            user_role=current_user.role.value,
            ip_address=_get_ip(),
            resource_type="did_pool",
            resource_id=pool.id,
            resource_name=pool.name,
            payload_after={
                "name":        pool.name,
                "location_id": pool.location_id,
                "range_start": pool.range_start,
                "range_end":   pool.range_end,
                "numbers":     created,
            },
            status="success",
        )
        flash(
            f"Pool '{pool.name}' created with {created} numbers. "
            f"Run Sync to check current Webex assignment status.",
            "success"
        )
        return redirect(url_for("did.pool_detail", pool_id=pool.id))

    return render_template("pool_form.html", form=form, pool=None)


# ── Pool Detail ───────────────────────────────────────────────────────────────

@did_bp.route("/pools/<int:pool_id>", methods=["GET"])
@login_required
@gui_admin_required
def pool_detail(pool_id: int):
    pool        = DIDPool.query.get_or_404(pool_id)
    status_filter = request.args.get("status", "")
    search        = request.args.get("search", "").strip()
    page          = int(request.args.get("page", 1))
    per_page      = int(request.args.get("per_page", 50))

    q = DIDAssignment.query.filter_by(pool_id=pool_id)

    if status_filter:
        q = q.filter(DIDAssignment.status == DIDStatus(status_filter))

    if search:
        term = f"%{search}%"
        q = q.filter(
            db.or_(
                DIDAssignment.number.ilike(term),
                DIDAssignment.assigned_to_name.ilike(term),
                DIDAssignment.assigned_to_email.ilike(term),
            )
        )

    q = q.order_by(DIDAssignment.number.asc())
    pagination = q.paginate(page=page, per_page=per_page, error_out=False)

    assign_form = ManualAssignForm()

    return render_template(
        "pool_detail.html",
        pool=pool,
        pagination=pagination,
        numbers=pagination.items,
        assign_form=assign_form,
        status_filter=status_filter,
        search=search,
        page=page,
        per_page=per_page,
        DIDStatus=DIDStatus,
    )


# ── Edit Pool ─────────────────────────────────────────────────────────────────

@did_bp.route("/pools/<int:pool_id>/edit", methods=["GET", "POST"])
@login_required
@gui_admin_required
def pool_edit(pool_id: int):
    pool = DIDPool.query.get_or_404(pool_id)
    form = DIDPoolForm(obj=pool)
    _populate_location_choices(form)

    if form.validate_on_submit():
        payload_before = pool.to_dict()

        pool.name        = form.name.data.strip()
        pool.description = form.description.data.strip() if form.description.data else ""
        pool.location_id = form.location_id.data
        pool.location_name = _get_location_name(form.location_id.data)
        pool.is_active   = form.is_active.data
        # Range is immutable once created — don't allow editing
        db.session.commit()

        AuditLog.write(
            action="UPDATE",
            user_id=current_user.id,
            username=current_user.username,
            user_role=current_user.role.value,
            ip_address=_get_ip(),
            resource_type="did_pool",
            resource_id=pool.id,
            resource_name=pool.name,
            payload_before=payload_before,
            payload_after=pool.to_dict(),
            status="success",
        )
        flash(f"Pool '{pool.name}' updated.", "success")
        return redirect(url_for("did.pool_detail", pool_id=pool.id))

    return render_template("pool_form.html", form=form, pool=pool)


# ── Delete Pool ───────────────────────────────────────────────────────────────

@did_bp.route("/pools/<int:pool_id>/delete", methods=["POST"])
@login_required
@gui_admin_required
def pool_delete(pool_id: int):
    pool = DIDPool.query.get_or_404(pool_id)

    if pool.assigned_count > 0:
        flash(
            f"Cannot delete pool '{pool.name}' — "
            f"{pool.assigned_count} number(s) are currently assigned. "
            f"Release all numbers first.",
            "danger"
        )
        return redirect(url_for("did.pool_detail", pool_id=pool_id))

    AuditLog.write(
        action="DELETE",
        user_id=current_user.id,
        username=current_user.username,
        user_role=current_user.role.value,
        ip_address=_get_ip(),
        resource_type="did_pool",
        resource_id=pool.id,
        resource_name=pool.name,
        payload_before=pool.to_dict(),
        status="success",
    )
    name = pool.name
    db.session.delete(pool)   # Cascade deletes all DIDAssignment rows
    db.session.commit()

    flash(f"Pool '{name}' and all its numbers have been deleted.", "success")
    return redirect(url_for("did.pools_list"))


# ── Sync Pool (triggers Celery task) ──────────────────────────────────────────

@did_bp.route("/pools/<int:pool_id>/sync", methods=["POST"])
@login_required
@gui_admin_required
def pool_sync(pool_id: int):
    pool = DIDPool.query.get_or_404(pool_id)
    try:
        from app.tasks.did import sync_pool
        task = sync_pool.delay(pool_id)

        AuditLog.write(
            action="UPDATE",
            user_id=current_user.id,
            username=current_user.username,
            resource_type="did_pool",
            resource_id=pool.id,
            resource_name=f"Sync triggered for '{pool.name}'",
            status="success",
        )
        flash(
            f"Sync job queued for pool '{pool.name}'. "
            f"Task ID: {task.id[:12]}… Refresh in a few moments.",
            "info"
        )
    except Exception as exc:
        flash(f"Failed to queue sync: {exc}", "danger")

    return redirect(url_for("did.pool_detail", pool_id=pool_id))


# ── AJAX: Assign Number ───────────────────────────────────────────────────────

@did_bp.route("/api/assign", methods=["POST"])
@login_required
@gui_admin_required
def api_assign():
    data = request.get_json(silent=True) or {}

    number          = (data.get("number") or "").strip()
    assignment_type = (data.get("assignment_type") or "user").strip()
    entity_id       = (data.get("entity_id") or "").strip()
    notes           = (data.get("notes") or "").strip()

    if not number or not entity_id:
        return jsonify({"success": False, "message": "number and entity_id are required."}), 400

    ok, msg = provision_svc.manual_assign_did(
        number=number,
        assignment_type=assignment_type,
        entity_id=entity_id,
        notes=notes,
        admin_user_id=current_user.id,
        admin_username=current_user.username,
    )
    return jsonify({"success": ok, "message": msg})


# ── AJAX: Release Number ──────────────────────────────────────────────────────

@did_bp.route("/api/release", methods=["POST"])
@login_required
@gui_admin_required
def api_release():
    data   = request.get_json(silent=True) or {}
    number = (data.get("number") or "").strip()

    if not number:
        return jsonify({"success": False, "message": "number is required."}), 400

    ok, msg = provision_svc.release_did(
        number=number,
        admin_user_id=current_user.id,
        admin_username=current_user.username,
    )
    return jsonify({"success": ok, "message": msg})


# ── AJAX: Webex Entity Typeahead ──────────────────────────────────────────────

@did_bp.route("/api/search-entity", methods=["GET"])
@login_required
@gui_admin_required
def api_search_entity():
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
                    "label": f"{p.display_name} ({getattr(p,'emails',[''])[0]})",
                    "email": getattr(p, "emails", [""])[0],
                    "name":  p.display_name,
                }
                for p in people[:15]
            ]
        elif entity_type == "workspace":
            ws_list = webex.org.workspaces or []
            results = [
                {
                    "id":    ws.id,
                    "label": ws.display_name,
                    "name":  ws.display_name,
                    "email": "",
                }
                for ws in ws_list
                if q.lower() in (ws.display_name or "").lower()
            ][:15]

    except Exception as exc:
        current_app.logger.error(f"[DID entity search] {exc}")
        return jsonify([])

    return jsonify(results)


# ── AJAX: Webex Locations ─────────────────────────────────────────────────────

@did_bp.route("/api/locations", methods=["GET"])
@login_required
@gui_admin_required
def api_locations():
    try:
        webex     = get_webex_client()
        locations = webex.org.locations or []
        return jsonify([
            {"id": loc.id, "name": loc.name}
            for loc in locations
        ])
    except Exception as exc:
        current_app.logger.error(f"[DID locations] {exc}")
        return jsonify([])


# ── AJAX: Pool availability stats (for dashboard widget refresh) ───────────────

@did_bp.route("/api/pool-stats/<int:pool_id>", methods=["GET"])
@login_required
@gui_admin_required
def api_pool_stats(pool_id: int):
    pool = DIDPool.query.get_or_404(pool_id)
    return jsonify(pool.to_dict())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _populate_location_choices(form: DIDPoolForm) -> None:
    """Populate the location_id SelectField from the Webex API."""
    try:
        webex     = get_webex_client()
        locations = webex.org.locations or []
        form.location_id.choices = [
            (loc.id, loc.name) for loc in locations
        ]
    except Exception:
        form.location_id.choices = [("", "— Could not load Webex locations —")]


def _get_location_name(location_id: str) -> str:
    """Resolve a location name from ID for denormalisation."""
    try:
        webex     = get_webex_client()
        locations = webex.org.locations or []
        for loc in locations:
            if loc.id == location_id:
                return loc.name
    except Exception:
        pass
    return ""
