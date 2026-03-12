"""
Custom Flask route decorators used across all blueprints.

Decorators:
  @admin_required          — Requires role admin OR superadmin
  @superadmin_required     — Requires role superadmin only
  @readonly_allowed        — Explicitly marks a route as accessible to
                             read-only users (documents intent, no block)
  @api_key_required        — Validates X-API-Key header for webhook routes
  @log_action(action)      — Writes an AuditLog entry on every successful call
"""
import functools
import logging
from typing import Callable

from flask import abort, request, jsonify, current_app
from flask_login import current_user

logger = logging.getLogger(__name__)


# ═════════════════════════════════════════════════════════════════════════════
# ROLE-BASED ACCESS DECORATORS
# ═════════════════════════════════════════════════════════════════════════════

def admin_required(f: Callable) -> Callable:
    """
    Restrict a route to authenticated users with role ADMIN or SUPERADMIN.

    Read-only users are rejected with HTTP 403.
    Unauthenticated users are handled by Flask-Login's @login_required —
    apply that decorator as well when needed.

    Usage:
        @app.route("/admin/something")
        @login_required
        @admin_required
        def my_view():
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)

        from app.models.user import UserRole
        allowed = {UserRole.ADMIN, UserRole.SUPERADMIN}

        if current_user.role not in allowed:
            logger.warning(
                f"[Auth] 403 admin_required — "
                f"user={current_user.username} role={current_user.role} "
                f"path={request.path}"
            )
            if request.is_json:
                return jsonify({
                    "error": "Admin access required."
                }), 403
            abort(403)

        return f(*args, **kwargs)
    return decorated


def superadmin_required(f: Callable) -> Callable:
    """
    Restrict a route to authenticated users with role SUPERADMIN only.

    Admin and read-only users are rejected with HTTP 403.

    Usage:
        @app.route("/admin/users")
        @login_required
        @superadmin_required
        def user_list():
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)

        from app.models.user import UserRole

        if current_user.role != UserRole.SUPERADMIN:
            logger.warning(
                f"[Auth] 403 superadmin_required — "
                f"user={current_user.username} role={current_user.role} "
                f"path={request.path}"
            )
            if request.is_json:
                return jsonify({
                    "error": "Superadmin access required."
                }), 403
            abort(403)

        return f(*args, **kwargs)
    return decorated


def readonly_allowed(f: Callable) -> Callable:
    """
    Marker decorator — documents that a route is intentionally accessible
    to read-only users. No access restriction is applied; this exists
    purely for code readability and future auditing.

    Usage:
        @app.route("/admin/reports/dids")
        @login_required
        @readonly_allowed
        def did_report():
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        return f(*args, **kwargs)
    return decorated


# ═════════════════════════════════════════════════════════════════════════════
# API KEY DECORATOR  (webhook ingestion routes)
# ═════════════════════════════════════════════════════════════════════════════

def api_key_required(f: Callable) -> Callable:
    """
    Validate the X-API-Key HTTP header against the SNOW_WEBHOOK_SECRET
    value stored in AppConfig.

    Returns HTTP 401 JSON if the key is absent or incorrect.
    Returns HTTP 503 JSON if the secret has not been configured yet.

    Usage:
        @snow_bp.route("/api/webhook/snow", methods=["POST"])
        @csrf.exempt
        @api_key_required
        def snow_webhook():
            ...
    """
    @functools.wraps(f)
    def decorated(*args, **kwargs):
        from app.models.app_config import AppConfig

        expected = AppConfig.get("SNOW_WEBHOOK_SECRET", "")
        if not expected:
            logger.error(
                "[Auth] api_key_required — SNOW_WEBHOOK_SECRET not configured."
            )
            return jsonify({
                "error": "Webhook secret not configured on this server."
            }), 503

        provided = (
            request.headers.get("X-API-Key")
            or request.headers.get("Authorization", "").removeprefix("Bearer ")
        ).strip()

        if not provided or provided != expected:
            logger.warning(
                f"[Auth] 401 api_key_required — "
                f"invalid key from {request.remote_addr} "
                f"path={request.path}"
            )
            return jsonify({"error": "Invalid or missing API key."}), 401

        return f(*args, **kwargs)
    return decorated


# ═════════════════════════════════════════════════════════════════════════════
# AUDIT LOG DECORATOR
# ═════════════════════════════════════════════════════════════════════════════

def log_action(action: str, resource_type: str = ""):
    """
    Decorator factory that writes an AuditLog entry after a successful
    route call. Failures (exceptions / non-2xx) are not logged here —
    individual routes handle failure audit entries explicitly.

    Args:
        action:        AuditLog action string e.g. "DID_ASSIGN"
        resource_type: Optional resource type label e.g. "did"

    Usage:
        @did_bp.route("/dids/<int:did_id>/assign", methods=["POST"])
        @login_required
        @admin_required
        @log_action("DID_ASSIGN", resource_type="did")
        def assign_did(did_id):
            ...
    """
    def decorator(f: Callable) -> Callable:
        @functools.wraps(f)
        def decorated(*args, **kwargs):
            response = f(*args, **kwargs)

            # Only audit on success (2xx responses or non-Response returns)
            status_code = (
                response.status_code
                if hasattr(response, "status_code")
                else 200
            )
            if status_code < 400:
                try:
                    from app.models.audit import AuditLog
                    AuditLog.write(
                        action        = action,
                        username      = getattr(current_user, "username", "anonymous"),
                        user_role     = getattr(
                            getattr(current_user, "role", None),
                            "value", ""
                        ),
                        ip_address    = request.remote_addr,
                        resource_type = resource_type,
                        resource_name = request.path,
                        status        = "success",
                    )
                except Exception as exc:
                    logger.warning(f"[AuditLog] log_action write failed: {exc}")

            return response
        return decorated
    return decorator
