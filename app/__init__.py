import os
import logging
from logging.handlers import RotatingFileHandler

from flask import Flask, redirect, url_for, request, session
from flask_migrate import Migrate

from .config import config_map
from .extensions import (
    db, migrate, login_manager, bcrypt,
    mail, csrf, limiter, cache, cors, celery
)


def create_app(env_name: str = "production") -> Flask:
    """
    Application factory — creates and configures the Flask app.
    All blueprints, extensions and error handlers are registered here.
    """
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
    )

    # ── Load config ───────────────────────────────────────────────────────────
    cfg = config_map.get(env_name, config_map["default"])
    app.config.from_object(cfg)

    # ── Initialise extensions ─────────────────────────────────────────────────
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    bcrypt.init_app(app)
    mail.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)
    cache.init_app(app)
    cors.init_app(app, resources={r"/api/*": {"origins": "*"}})

    # ── Configure Celery ──────────────────────────────────────────────────────
    celery.conf.update(
        broker_url=app.config["CELERY_BROKER_URL"],
        result_backend=app.config["CELERY_RESULT_BACKEND"],
        task_serializer=app.config["CELERY_TASK_SERIALIZER"],
        accept_content=app.config["CELERY_ACCEPT_CONTENT"],
        timezone=app.config["CELERY_TIMEZONE"],
        enable_utc=app.config["CELERY_ENABLE_UTC"],
        beat_schedule=app.config["CELERYBEAT_SCHEDULE"],
        task_track_started=app.config["CELERY_TASK_TRACK_STARTED"],
        task_time_limit=app.config["CELERY_TASK_TIME_LIMIT"],
        task_soft_time_limit=app.config["CELERY_TASK_SOFT_TIME_LIMIT"],
    )
    celery.conf.update(app.config)

    class ContextTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = ContextTask

    # ── Register blueprints ───────────────────────────────────────────────────
    _register_blueprints(app)

    # ── Setup guard middleware ─────────────────────────────────────────────────
    _register_setup_guard(app)

    # ── Session idle timeout ──────────────────────────────────────────────────
    _register_session_timeout(app)

    # ── Error handlers ────────────────────────────────────────────────────────
    _register_error_handlers(app)

    # ── Logging ───────────────────────────────────────────────────────────────
    _configure_logging(app)

    return app


def create_celery(app: Flask) -> celery.__class__:
    """Return the Celery instance bound to the app context."""
    celery.conf.update(app.config)
    return celery


def _register_blueprints(app: Flask) -> None:
    """Import and register all blueprints."""

    # Setup wizard (Section 4)
    from app.routes.setup import setup_bp
    app.register_blueprint(setup_bp, url_prefix="/setup")

    # Auth (Section 4)
    from app.routes.auth import auth_bp
    app.register_blueprint(auth_bp, url_prefix="/auth")

    # Admin GUI (Sections 6–11)
    from app.routes.admin import admin_bp
    app.register_blueprint(admin_bp, url_prefix="/admin")

    # DID Management (Section 6)
    from app.routes.did import did_bp
    app.register_blueprint(did_bp, url_prefix="/admin/did")

    # Features (Sections 7–8)
    from app.routes.features import features_bp
    app.register_blueprint(features_bp, url_prefix="/admin/features")

    # Users / Workspaces / Devices (Section 9)
    from app.routes.users import users_bp
    app.register_blueprint(users_bp, url_prefix="/admin/users")

    # ServiceNow integration (Section 10)
    from app.routes.snow import snow_bp
    app.register_blueprint(snow_bp, url_prefix="/api/snow")

    # End-user portal (Section 11)
    from app.routes.portal import portal_bp
    app.register_blueprint(portal_bp, url_prefix="/portal")

    # System admin (Section 12)
    from app.routes.system import system_bp
    app.register_blueprint(system_bp, url_prefix="/system")

    # API (internal AJAX)
    from app.routes.api import api_bp
    app.register_blueprint(api_bp, url_prefix="/api/v1")


def _register_setup_guard(app: Flask) -> None:
    """
    Block all routes except /setup and /static when APP_STATE=setup_pending.
    Once setup is complete the guard is lifted.
    """
    @app.before_request
    def setup_guard():
        if app.config.get("APP_STATE") == "setup_pending":
            allowed_prefixes = ("/setup", "/static", "/favicon.ico")
            if not any(request.path.startswith(p) for p in allowed_prefixes):
                return redirect(url_for("setup.index"))


def _register_session_timeout(app: Flask) -> None:
    """Enforce server-side idle session timeout for all authenticated users."""
    from flask_login import current_user
    from datetime import datetime, timezone
    from flask import g

    @app.before_request
    def enforce_session_timeout():
        if current_user.is_authenticated:
            timeout = app.config["SESSION_TIMEOUT_MINUTES"] * 60
            last_activity = session.get("_last_activity")
            now = datetime.now(timezone.utc).timestamp()

            if last_activity and (now - last_activity) > timeout:
                from flask_login import logout_user
                logout_user()
                session.clear()
                from flask import flash
                flash("Your session has expired due to inactivity. Please log in again.", "warning")
                return redirect(url_for("auth.login"))

            session["_last_activity"] = now
            session.permanent = True


def _register_error_handlers(app: Flask) -> None:
    from flask import render_template

    @app.errorhandler(400)
    def bad_request(e):
        return render_template("errors/400.html"), 400

    @app.errorhandler(403)
    def forbidden(e):
        return render_template("errors/403.html"), 403

    @app.errorhandler(404)
    def not_found(e):
        return render_template("errors/404.html"), 404

    @app.errorhandler(429)
    def rate_limited(e):
        return render_template("errors/429.html"), 429

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        return render_template("errors/500.html"), 500


def _configure_logging(app: Flask) -> None:
    log_dir = os.path.join(app.config.get("ORBIT_HOME", "/opt/orbit"), "logs")
    os.makedirs(log_dir, exist_ok=True)

    formatter = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    file_handler = RotatingFileHandler(
        os.path.join(log_dir, "orbit.log"),
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=10,
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    app.logger.addHandler(file_handler)
    app.logger.setLevel(logging.INFO)

    if not app.debug:
        logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
        logging.getLogger("werkzeug").setLevel(logging.WARNING)
