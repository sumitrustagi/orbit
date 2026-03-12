"""
User Management Blueprint.

Routes:
  GET  /admin/users/                      → User list
  GET  /admin/users/new                   → Create user form
  POST /admin/users/new                   → Save new user
  GET  /admin/users/<id>                  → User detail + activity log
  GET  /admin/users/<id>/edit             → Edit user form
  POST /admin/users/<id>/edit             → Save edits
  POST /admin/users/<id>/delete           → Delete user
  POST /admin/users/<id>/toggle           → Enable / disable account
  GET  /admin/users/<id>/password         → Change password form
  POST /admin/users/<id>/password         → Save new password
  GET  /admin/users/me/password           → Self change-password form
  POST /admin/users/me/password           → Save own new password
  GET  /admin/users/api/list              → AJAX: user list (typeahead)
"""
from datetime import datetime, timezone

from flask import (
    Blueprint, render_template, redirect, url_for,
    request, flash, jsonify, abort
)
from flask_login import login_required, current_user

from app.utils.decorators import gui_admin_required, superadmin_required, _get_ip
from app.models.user import User, UserRole
from app.models.audit import AuditLog
from app.forms.user_forms import (
    CreateUserForm, EditUserForm,
    ChangePasswordForm, SelfChangePasswordForm
)
from app.extensions import db

users_bp = Blueprint(
    "users", __name__,
    template_folder="../templates/users",
    url_prefix="/admin/users"
)


# ── List ──────────────────────────────────────────────────────────────────────

@users_bp.route("/", methods=["GET"])
@login_required
@gui_admin_required
def list_users():
    role_filter   = request.args.get("role", "")
    status_filter = request.args.get("status", "")
    search        = request.args.get("search", "").strip()

    q = User.query.order_by(User.username.asc())

    if role_filter:
        try:
            q = q.filter_by(role=UserRole(role_filter))
        except ValueError:
            pass

    if status_filter == "active":
        q = q.filter_by(is_active=True)
    elif status_filter == "inactive":
        q = q.filter_by(is_active=False)
    elif status_filter == "locked":
        q = q.filter(User.locked_until.isnot(None))

    if search:
        term = f"%{search}%"
        q = q.filter(
            db.or_(
                User.username.ilike(term),
                User.email.ilike(term),
                User.full_name.ilike(term),
            )
        )

    users = q.all()

    stats = {
        "total":       User.query.count(),
        "active":      User.query.filter_by(is_active=True).count(),
        "superadmins": User.query.filter_by(role=UserRole.SUPERADMIN).count(),
        "admins":      User.query.filter_by(role=UserRole.ADMIN).count(),
        "readonly":    User.query.filter_by(role=UserRole.READONLY).count(),
    }

    return render_template(
        "list.html",
        users=users,
        stats=stats,
        role_filter=role_filter,
        status_filter=status_filter,
        search=search,
        UserRole=UserRole,
        now=datetime.now(timezone.utc),
    )


# ── Create ────────────────────────────────────────────────────────────────────

@users_bp.route("/new", methods=["GET", "POST"])
@login_required
@superadmin_required
def create_user():
    form = CreateUserForm()

    if form.validate_on_submit():
        user = User(
            username=form.username.data.strip().lower(),
            email=form.email.data.strip().lower(),
            full_name=(form.full_name.data or "").strip(),
            role=UserRole(form.role.data),
            is_active=form.is_active.data,
            notes=(form.notes.data or "").strip(),
        )
        user.set_password(form.password.data)
        db.session.add(user)
        db.session.commit()

        AuditLog.write(
            action="CREATE",
            user_id=current_user.id,
            username=current_user.username,
            user_role=current_user.role.value,
            ip_address=_get_ip(),
            resource_type="user",
            resource_id=user.id,
            resource_name=user.username,
            payload_after={
                "email": user.email,
                "role":  user.role.value,
            },
            status="success",
        )
        flash(f"User '{user.username}' created successfully.", "success")
        return redirect(url_for("users.user_detail", user_id=user.id))

    return render_template("form.html", form=form, user=None)


# ── Detail ────────────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>", methods=["GET"])
@login_required
@gui_admin_required
def user_detail(user_id: int):
    user = User.query.get_or_404(user_id)

    # Last 30 audit entries for this user
    recent_audit = (
        AuditLog.query
        .filter_by(user_id=user_id)
        .order_by(AuditLog.created_at.desc())
        .limit(30)
        .all()
    )
    pw_form = ChangePasswordForm()

    return render_template(
        "detail.html",
        user=user,
        recent_audit=recent_audit,
        pw_form=pw_form,
        UserRole=UserRole,
        now=datetime.now(timezone.utc),
    )


# ── Edit ──────────────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/edit", methods=["GET", "POST"])
@login_required
@superadmin_required
def edit_user(user_id: int):
    user = User.query.get_or_404(user_id)

    # Prevent editing a superadmin unless you ARE one (and not yourself)
    if user.role == UserRole.SUPERADMIN and user.id != current_user.id:
        if current_user.role != UserRole.SUPERADMIN:
            abort(403)

    form          = EditUserForm(obj=user)
    form._user_id = user.id

    if request.method == "GET":
        form.role.data = user.role.value

    if form.validate_on_submit():
        payload_before = {
            "username": user.username, "email": user.email,
            "role": user.role.value, "is_active": user.is_active,
        }

        user.username  = form.username.data.strip().lower()
        user.email     = form.email.data.strip().lower()
        user.full_name = (form.full_name.data or "").strip()
        user.role      = UserRole(form.role.data)
        user.is_active = form.is_active.data
        user.notes     = (form.notes.data or "").strip()
        user.updated_at = datetime.now(timezone.utc)
        db.session.commit()

        AuditLog.write(
            action="UPDATE",
            user_id=current_user.id,
            username=current_user.username,
            ip_address=_get_ip(),
            resource_type="user",
            resource_id=user.id,
            resource_name=user.username,
            payload_before=payload_before,
            payload_after={
                "username": user.username, "email": user.email,
                "role": user.role.value, "is_active": user.is_active,
            },
            status="success",
        )
        flash(f"User '{user.username}' updated.", "success")
        return redirect(url_for("users.user_detail", user_id=user.id))

    return render_template("form.html", form=form, user=user)


# ── Delete ────────────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/delete", methods=["POST"])
@login_required
@superadmin_required
def delete_user(user_id: int):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot delete your own account.", "danger")
        return redirect(url_for("users.list_users"))

    if user.role == UserRole.SUPERADMIN:
        count = User.query.filter_by(role=UserRole.SUPERADMIN, is_active=True).count()
        if count <= 1:
            flash("Cannot delete the last active Super Admin account.", "danger")
            return redirect(url_for("users.user_detail", user_id=user_id))

    AuditLog.write(
        action="DELETE",
        user_id=current_user.id,
        username=current_user.username,
        ip_address=_get_ip(),
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        payload_before={
            "email": user.email, "role": user.role.value
        },
        status="success",
    )
    username = user.username
    db.session.delete(user)
    db.session.commit()
    flash(f"User '{username}' deleted.", "success")
    return redirect(url_for("users.list_users"))


# ── Toggle active ─────────────────────────────────────────────────────────────

@users_bp.route("/<int:user_id>/toggle", methods=["POST"])
@login_required
@superadmin_required
def toggle_user(user_id: int):
    user = User.query.get_or_404(user_id)

    if user.id == current_user.id:
        flash("You cannot disable your own account.", "danger")
        return redirect(url_for("users.user_detail", user_id=user_id))

    user.is_active  = not user.is_active
    user.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    state = "activated" if user.is_active else "deactivated"
    AuditLog.write(
        action="UPDATE",
        user_id=current_user.id,
        username=current_user.username,
        ip_address=_get_ip(),
        resource_type="user",
        resource_id=user.id,
        resource_name=user.username,
        payload_after={"is_active": user.is_active},
        status="success",
    )
    flash(f"Account '{user.username}' {state}.", "success")
    return redirect(url_for("users.user_detail", user_id=user_id))


# ── Admin: Change another user's password ─────────────────────────────────────

@users_bp.route("/<int:user_id>/password", methods=["GET", "POST"])
@login_required
@superadmin_required
def change_password(user_id: int):
    user = User.query.get_or_404(user_id)
    form = ChangePasswordForm()

    if form.validate_on_submit():
        user.set_password(form.new_password.data)
        user.must_change_password = form.force_change.data
        user.updated_at           = datetime.now(timezone.utc)
        db.session.commit()

        AuditLog.write(
            action="PASSWORD_RESET",
            user_id=current_user.id,
            username=current_user.username,
            ip_address=_get_ip(),
            resource_type="user",
            resource_id=user.id,
            resource_name=user.username,
            payload_after={"force_change": form.force_change.data},
            status="success",
        )
        flash(f"Password for '{user.username}' updated.", "success")
        return redirect(url_for("users.user_detail", user_id=user.id))

    return render_template("change_password.html", form=form, user=user)


# ── Self: Change own password ─────────────────────────────────────────────────

@users_bp.route("/me/password", methods=["GET", "POST"])
@login_required
def self_change_password():
    form = SelfChangePasswordForm()

    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("Current password is incorrect.")
            return render_template("self_change_password.html", form=form)

        current_user.set_password(form.new_password.data)
        current_user.must_change_password = False
        current_user.updated_at           = datetime.now(timezone.utc)
        db.session.commit()

        AuditLog.write(
            action="PASSWORD_CHANGE_SELF",
            user_id=current_user.id,
            username=current_user.username,
            ip_address=_get_ip(),
            resource_type="user",
            resource_id=current_user.id,
            resource_name=current_user.username,
            status="success",
        )
        flash("Your password has been updated.", "success")
        return redirect(url_for("dashboard.index"))

    return render_template("self_change_password.html", form=form)


# ── AJAX: user search typeahead ───────────────────────────────────────────────

@users_bp.route("/api/list", methods=["GET"])
@login_required
@gui_admin_required
def api_list():
    q     = request.args.get("q", "").strip()
    users = User.query

    if len(q) >= 2:
        term  = f"%{q}%"
        users = users.filter(
            db.or_(User.username.ilike(term), User.email.ilike(term))
        )

    users = users.filter_by(is_active=True).limit(15).all()
    return jsonify([
        {"id": u.id, "username": u.username, "email": u.email,
         "full_name": u.full_name or "", "role": u.role.value}
        for u in users
    ])
