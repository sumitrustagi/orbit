"""
Role-based access decorators and the @audit_action decorator.
Import these in every route file.
"""
import functools
from flask import abort, request, g
from flask_login import current_user
from app.models.user import UserRole


# ── Role Guards ───────────────────────────────────────────────────────────────

def require_role(*roles: UserRole):
    """
    Decorator factory. Usage:
        @require_role(UserRole.GUI_ADMIN)
        @require_role(UserRole.GUI_ADMIN, UserRole.PLATFORM_ADMIN)
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                abort(401)
            if current_user.role not in roles:
                abort(403)
            return f(*args, **kwargs)
        return wrapped
    return decorator


def gui_admin_required(f):
    """Shortcut: GUI Admin only."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if current_user.role not in (UserRole.GUI_ADMIN, UserRole.PLATFORM_ADMIN):
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def platform_admin_required(f):
    """Shortcut: Platform Admin only."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        if current_user.role != UserRole.PLATFORM_ADMIN:
            abort(403)
        return f(*args, **kwargs)
    return wrapped


def end_user_required(f):
    """Shortcut: Any authenticated user (portal access)."""
    @functools.wraps(f)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            abort(401)
        return f(*args, **kwargs)
    return wrapped


# ── Audit Action Decorator ────────────────────────────────────────────────────

def audit_action(action: str, resource_type: str = "", capture_response: bool = False):
    """
    Decorator that writes an AuditLog entry for the wrapped route.

    Usage:
        @audit_action("UPDATE", "auto_attendant")
        def update_attendant(attendant_id):
            ...
    """
    def decorator(f):
        @functools.wraps(f)
        def wrapped(*args, **kwargs):
            from app.models.audit import AuditLog

            user_id   = current_user.id       if current_user.is_authenticated else None
            username  = current_user.username  if current_user.is_authenticated else "anonymous"
            role      = current_user.role.value if current_user.is_authenticated else ""
            ip        = _get_ip()
            ua        = request.headers.get("User-Agent", "")[:512]

            # Extract resource_id from route kwargs if present
            rid = (
                kwargs.get("id") or
                kwargs.get("user_id") or
                kwargs.get("pool_id") or
                kwargs.get("attendant_id") or
                ""
            )

            response = f(*args, **kwargs)

            status_code = None
            if hasattr(response, "status_code"):
                status_code = response.status_code

            AuditLog.write(
                action=action,
                user_id=user_id,
                username=username,
                user_role=role,
                ip_address=ip,
                user_agent=ua,
                resource_type=resource_type,
                resource_id=str(rid),
                http_method=request.method,
                http_path=request.path,
                http_status=status_code,
                status="success",
            )
            return response
        return wrapped
    return decorator


def _get_ip() -> str:
    """Extract real client IP respecting X-Forwarded-For from Nginx."""
    xff = request.headers.get("X-Forwarded-For")
    if xff:
        return xff.split(",")[0].strip()
    return request.remote_addr or ""
