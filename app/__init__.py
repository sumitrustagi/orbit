"""
Orbit — Flask Application Factory
==================================
Creates and fully configures the Orbit application.

Registers:
  - All Flask extensions       (SQLAlchemy, Migrate, LoginManager, CSRF,
                                 Limiter, Cache, Mail)
  - All Blueprints             (auth, dashboard, did, did_pool, snow,
                                 call_forward, reports, users, settings, tasks)
  - CLI commands               (flask admin …)
  - Jinja2 filters & globals   (timeago, fmt_dt, status_badge, …)
  - Request hooks              (maintenance mode, forced password change,
                                 security headers)
  - Error handlers             (400, 401, 403, 404, 429, 500)
  - Celery context binding

Usage:
    from app import create_app
    app = create_app()
"""

import logging
import os
from logging.handlers import RotatingFileHandler

from flask import (
    Flask,
    render_template,
    redirect,
    url_for,
    request,
    jsonify,
)
from flask_login import current_user

from config import get_config


# ═════════════════════════════════════════════════════════════════════════════
# APPLICATION FACTORY
# ═════════════════════════════════════════════════════════════════════════════

def create_app(config_object=None) -> Flask:
    """
    Create, configure and return the Flask application.

    Args:
        config_object: Optional config class or instance to override the
                       environment-detected config (useful for testing).

    Returns:
        A fully initialised Flask application instance.
    """
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── 1. Configuration ──────────────────────────────────────────────────
    cfg = config_object or get_config()
    app.config.from_object(cfg)

    # Validate production secrets (raises ValueError if missing)
    if hasattr(cfg, "validate"):
        try:
            cfg.validate()
        except ValueError as exc:
            # Log but do not crash — allows partial startup for diagnostics
            app.logger.critical(f"[Config] Missing required variable: {exc}")

    # ── 2. Logging ────────────────────────────────────────────────────────
    _init_logging(app)

    # ── 3. Extensions ─────────────────────────────────────────────────────
    _init_extensions(app)

    # ── 4. Blueprints ─────────────────────────────────────────────────────
    _register_blueprints(app)

    # ── 5. CLI commands ───────────────────────────────────────────────────
    _register_cli(app)

    # ── 6. Template filters & context processors ──────────────────────────
    _register_template_utils(app)

    # ── 7. Request / response hooks ───────────────────────────────────────
    _register_hooks(app)

    # ── 8. Error handlers ─────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── 9. Celery ─────────────────────────────────────────────────────────
    _init_celery(app)

    # ── 10. Health-check route (unauthenticated) ──────────────────────────
    _register_health_route(app)

    app.logger.info(
        f"[Orbit] Application started — "
        f"env={os.environ.get('FLASK_ENV', 'production')} "
        f"debug={app.debug}"
    )
    return app


# ═════════════════════════════════════════════════════════════════════════════
# 1 — LOGGING
# ═════════════════════════════════════════════════════════════════════════════

def _init_logging(app: Flask) -> None:
    """
    Configure structured logging to stdout and (optionally) a rotating
    file. Quietens noisy third-party loggers.
    """
    level = getattr(
        logging,
        app.config.get("LOG_LEVEL", "INFO").upper(),
        logging.INFO,
    )

    fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-8s %(name)-35s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Always log to stdout (captured by Docker / systemd)
    if app.config.get("LOG_TO_STDOUT", True):
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(level)
        app.logger.addHandler(sh)

    # Optional rotating file handler
    log_file = os.environ.get("LOG_FILE")
    if log_file:
        try:
            fh = RotatingFileHandler(
                log_file,
                maxBytes=10 * 1024 * 1024,   # 10 MB per file
                backupCount=5,
            )
            fh.setFormatter(fmt)
            fh.setLevel(level)
            app.logger.addHandler(fh)
        except OSError as exc:
            app.logger.warning(f"[Logging] Cannot open log file {log_file}: {exc}")

    app.logger.setLevel(level)

    # Suppress noisy third-party loggers in production
    if not app.debug:
        for noisy in (
            "werkzeug",
            "urllib3",
            "urllib3.connectionpool",
            "sqlalchemy.engine",
            "sqlalchemy.pool",
            "celery.app.trace",
            "celery.worker.strategy",
        ):
            logging.getLogger(noisy).setLevel(logging.WARNING)


# ═════════════════════════════════════════════════════════════════════════════
# 2 — EXTENSIONS
# ═════════════════════════════════════════════════════════════════════════════

def _init_extensions(app: Flask) -> None:
    """
    Initialise all Flask extensions and attach them to the app.
    All extension instances live in app/extensions.py to avoid
    circular imports.
    """
    from app.extensions import (
        db,
        migrate,
        login_manager,
        csrf,
        limiter,
        cache,
        mail,
    )

    # ── Database & migrations ─────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)

    # ── CSRF protection ───────────────────────────────────────────────────
    csrf.init_app(app)

    # ── Rate limiting (Redis-backed) ──────────────────────────────────────
    limiter.init_app(app)

    # ── Cache (Redis in production, SimpleCache in dev/test) ─────────────
    cache.init_app(app)

    # ── Flask-Mail ────────────────────────────────────────────────────────
    mail.init_app(app)

    # ── Flask-Login ───────────────────────────────────────────────────────
    login_manager.init_app(app)
    login_manager.login_view             = "auth.login"
    login_manager.login_message          = "Please sign in to access this page."
    login_manager.login_message_category = "warning"
    login_manager.session_protection     = "strong"

    @login_manager.user_loader
    def load_user(user_id: str):
        """
        Reload the user object from the database on every request.
        Returns None if the user no longer exists or is inactive.
        """
        from app.models.user import User
        try:
            user = User.query.get(int(user_id))
            # Invalidate session if the account has been deactivated
            if user and not user.is_active:
                return None
            return user
        except Exception:
            return None

    @login_manager.unauthorized_handler
    def handle_unauthorized():
        """
        Return JSON 401 for AJAX requests, redirect to login for
        regular browser requests.
        """
        if request.is_json or request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("auth.login", next=request.full_path))


# ═════════════════════════════════════════════════════════════════════════════
# 3 — BLUEPRINTS
# ═════════════════════════════════════════════════════════════════════════════

def _register_blueprints(app: Flask) -> None:
    """
    Import and register every application blueprint.

    Blueprint URL prefixes are defined inside each blueprint module.
    Registration order matters for url_for() precedence — auth first.
    """

    # ── Authentication & dashboard ────────────────────────────────────────
    from app.routes.auth         import auth_bp        # /admin/login, /logout
    from app.routes.dashboard    import dashboard_bp   # /admin/

    # ── DID management ────────────────────────────────────────────────────
    from app.routes.did_pool     import did_pool_bp    # /admin/pools/…
    from app.routes.did          import did_bp         # /admin/dids/…

    # ── ServiceNow ────────────────────────────────────────────────────────
    from app.routes.snow         import snow_bp        # /admin/snow/… + /api/webhook/snow

    # ── Call Forward scheduling ───────────────────────────────────────────
    from app.routes.call_forward import call_forward_bp  # /admin/call-forward/…

    # ── Reports & analytics ───────────────────────────────────────────────
    from app.routes.reports      import reports_bp     # /admin/reports/…

    # ── User management ───────────────────────────────────────────────────
    from app.routes.users        import users_bp       # /admin/users/…

    # ── Settings ──────────────────────────────────────────────────────────
    from app.routes.settings     import settings_bp    # /admin/settings/…

    # ── Task Monitor ──────────────────────────────────────────────────────
    from app.routes.tasks        import tasks_bp       # /admin/tasks/…

    blueprints = [
        auth_bp,
        dashboard_bp,
        did_pool_bp,
        did_bp,
        snow_bp,
        call_forward_bp,
        reports_bp,
        users_bp,
        settings_bp,
        tasks_bp,
    ]

    for bp in blueprints:
        app.register_blueprint(bp)

    app.logger.debug(
        f"[Blueprints] Registered: "
        f"{', '.join(app.blueprints.keys())}"
    )


# ═════════════════════════════════════════════════════════════════════════════
# 4 — CLI
# ═════════════════════════════════════════════════════════════════════════════

def _register_cli(app: Flask) -> None:
    """
    Register the admin CLI blueprint which exposes:
      flask admin create-admin
      flask admin list-users
      flask admin reset-password
      flask admin seed-config
      flask admin show-config [--show-secrets]
      flask admin set-config <KEY> <VALUE> [--encrypted]
      flask admin sync-webex
      flask admin purge-audit [--days N]
      flask admin test-connections
    """
    from app.cli.admin_shell import admin_cli
    app.register_blueprint(admin_cli)


# ═════════════════════════════════════════════════════════════════════════════
# 5 — TEMPLATE UTILITIES
# ═════════════════════════════════════════════════════════════════════════════

def _register_template_utils(app: Flask) -> None:
    """
    Register Jinja2 custom filters and inject global template variables
    that are available in every rendered template.
    """

    # ── Custom filters ────────────────────────────────────────────────────
    from app.utils.template_filters import register_filters
    register_filters(app)

    # ── Context processor ─────────────────────────────────────────────────
    @app.context_processor
    def inject_globals() -> dict:
        """
        Inject variables into every template context:
          app_name        — display name from AppConfig
          app_version     — current version string
          primary_color   — brand primary hex colour
          accent_color    — brand accent hex colour
          maintenance_mode— bool, True when maintenance window is active
          now             — current UTC datetime (for age calculations)
          UserRole        — enum available in all templates for role checks
        """
        from datetime import datetime, timezone
        from app.models.app_config import AppConfig
        from app.models.user import UserRole

        return {
            "app_name":         AppConfig.get("APP_NAME",         "Orbit"),
            "app_version":      AppConfig.get("APP_VERSION",      "1.0.0"),
            "primary_color":    AppConfig.get("PRIMARY_COLOR",    "#1E40AF"),
            "accent_color":     AppConfig.get("ACCENT_COLOR",     "#3B82F6"),
            "maintenance_mode": AppConfig.get("MAINTENANCE_MODE", "false") == "true",
            "now":              datetime.now(timezone.utc),
            "UserRole":         UserRole,
        }


# ═════════════════════════════════════════════════════════════════════════════
# 6 — REQUEST / RESPONSE HOOKS
# ═════════════════════════════════════════════════════════════════════════════

def _register_hooks(app: Flask) -> None:
    """
    Register before_request and after_request hooks:

      before_request:
        1. check_maintenance   — block non-superadmin during maintenance
        2. enforce_password_change — redirect users who must reset their PW

      after_request:
        1. set_security_headers — attach OWASP-recommended HTTP headers
    """

    # ── Maintenance mode ──────────────────────────────────────────────────
    @app.before_request
    def check_maintenance():
        """
        When MAINTENANCE_MODE is 'true' in AppConfig, block all requests
        except:
          - Static file requests
          - The auth blueprint (so admins can still log in)
          - Superadmin users who are already authenticated
          - The /health endpoint
        Returns a 503 maintenance page for everyone else.
        """
        from app.models.app_config import AppConfig
        from app.models.user import UserRole

        if AppConfig.get("MAINTENANCE_MODE", "false") != "true":
            return None

        # Allow static assets
        if request.endpoint and request.endpoint.startswith("static"):
            return None

        # Allow the entire auth blueprint (login page stays accessible)
        if request.blueprint == "auth":
            return None

        # Allow the health-check endpoint
        if request.endpoint == "health":
            return None

        # Allow authenticated superadmins through
        if current_user.is_authenticated:
            if current_user.role == UserRole.SUPERADMIN:
                return None

        message = AppConfig.get(
            "MAINTENANCE_MESSAGE",
            "The system is currently undergoing scheduled maintenance. "
            "Please try again shortly.",
        )
        return render_template(
            "errors/maintenance.html",
            message=message,
        ), 503


    # ── Forced password change ────────────────────────────────────────────
    @app.before_request
    def enforce_password_change():
        """
        Redirect any authenticated user whose must_change_password flag
        is True to the self-service password change page.

        Exempted endpoints:
          - users.self_change_password  (the destination itself)
          - auth.logout                 (always allow logout)
          - static                      (assets still load)
          - health                      (health check)
        """
        if not current_user.is_authenticated:
            return None
        if not getattr(current_user, "must_change_password", False):
            return None

        exempt = {
            "users.self_change_password",
            "auth.logout",
            "static",
            "health",
        }
        if request.endpoint in exempt:
            return None

        return redirect(url_for("users.self_change_password"))


    # ── Security headers ──────────────────────────────────────────────────
    @app.after_request
    def set_security_headers(response):
        """
        Attach recommended security headers to every HTTP response.
        HSTS is only set in non-debug mode (production).
        """
        response.headers["X-Frame-Options"]        = "SAMEORIGIN"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-XSS-Protection"]       = "1; mode=block"
        response.headers["Referrer-Policy"]        = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"]     = (
            "geolocation=(), microphone=(), camera=()"
        )
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' cdn.jsdelivr.net; "
            "font-src 'self' cdn.jsdelivr.net; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )

        if not app.debug:
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains; preload"
            )

        return response


# ═════════════════════════════════════════════════════════════════════════════
# 7 — ERROR HANDLERS
# ═════════════════════════════════════════════════════════════════════════════

def _register_error_handlers(app: Flask) -> None:
    """
    Register HTTP error handlers.
    AJAX requests (JSON) receive JSON error responses.
    Browser requests receive rendered error pages.
    """

    def _is_api_request() -> bool:
        return (
            request.is_json
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.path.startswith("/api/")
        )

    @app.errorhandler(400)
    def bad_request(e):
        if _is_api_request():
            return jsonify({"error": "Bad request.", "detail": str(e)}), 400
        return render_template("errors/400.html", error=e), 400

    @app.errorhandler(401)
    def unauthorised(e):
        if _is_api_request():
            return jsonify({"error": "Authentication required."}), 401
        return redirect(url_for("auth.login", next=request.url))

    @app.errorhandler(403)
    def forbidden(e):
        if _is_api_request():
            return jsonify({"error": "You do not have permission to perform this action."}), 403
        return render_template("errors/403.html", error=e), 403

    @app.errorhandler(404)
    def not_found(e):
        if _is_api_request():
            return jsonify({"error": "The requested resource was not found."}), 404
        return render_template("errors/404.html", error=e), 404

    @app.errorhandler(405)
    def method_not_allowed(e):
        if _is_api_request():
            return jsonify({"error": "Method not allowed."}), 405
        return render_template("errors/404.html", error=e), 405

    @app.errorhandler(429)
    def rate_limited(e):
        if _is_api_request():
            return jsonify({
                "error": "Too many requests. Please slow down.",
                "retry_after": getattr(e, "retry_after", 60),
            }), 429
        return render_template("errors/429.html", error=e), 429

    @app.errorhandler(500)
    def internal_error(e):
        # Always rollback the DB session on unhandled exceptions
        from app.extensions import db
        try:
            db.session.rollback()
        except Exception:
            pass

        app.logger.exception(f"[500] Unhandled internal error: {e}")

        if _is_api_request():
            return jsonify({"error": "An internal server error occurred."}), 500
        return render_template("errors/500.html", error=e), 500

    @app.errorhandler(503)
    def service_unavailable(e):
        if _is_api_request():
            return jsonify({"error": "Service temporarily unavailable."}), 503
        return render_template("errors/maintenance.html", error=e), 503


# ═════════════════════════════════════════════════════════════════════════════
# 8 — CELERY
# ═════════════════════════════════════════════════════════════════════════════

def _init_celery(app: Flask) -> None:
    """
    Bind the Celery application instance to the Flask app context so that
    every task body runs inside an active Flask application context.

    The celery_app instance is created without context in app/tasks/__init__.py
    and fully configured here via init_celery().
    """
    from app.tasks import celery_app, init_celery
    init_celery(celery_app, app)
    app.logger.debug("[Celery] Bound to Flask application context.")


# ═════════════════════════════════════════════════════════════════════════════
# 9 — HEALTH CHECK ROUTE
# ═════════════════════════════════════════════════════════════════════════════

def _register_health_route(app: Flask) -> None:
    """
    Register a lightweight /health endpoint used by:
      - Docker HEALTHCHECK
      - Nginx upstream health probes
      - Load-balancer health checks

    Returns 200 JSON with DB and Redis connectivity status.
    No authentication required.
    """

    @app.route("/health", endpoint="health")
    def health():
        from app.extensions import db, cache

        status  = {"status": "ok", "db": "ok", "cache": "ok"}
        http_code = 200

        # Check DB
        try:
            db.session.execute(db.text("SELECT 1"))
        except Exception as exc:
            status["db"]     = f"error: {exc}"
            status["status"] = "degraded"
            http_code        = 503

        # Check Redis/Cache
        try:
            cache.set("__health__", "1", timeout=5)
            assert cache.get("__health__") == "1"
        except Exception as exc:
            status["cache"]  = f"error: {exc}"
            status["status"] = "degraded"
            http_code        = 503

        return jsonify(status), http_code
