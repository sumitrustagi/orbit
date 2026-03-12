"""
Orbit — Flask Extension Instances
===================================
All extension objects are created here without an app instance.
They are bound to the app inside create_app() via their init_app() methods.

Import from this module everywhere else to avoid circular imports:

    from app.extensions import db, login_manager, cache, ...
"""
from flask_sqlalchemy   import SQLAlchemy
from flask_migrate      import Migrate
from flask_login        import LoginManager
from flask_wtf.csrf     import CSRFProtect
from flask_limiter      import Limiter
from flask_limiter.util import get_remote_address
from flask_caching      import Cache
from flask_mail         import Mail


# ── Database ORM ──────────────────────────────────────────────────────────────
db = SQLAlchemy()

# ── Alembic migrations ────────────────────────────────────────────────────────
migrate = Migrate()

# ── Authentication ────────────────────────────────────────────────────────────
login_manager = LoginManager()

# ── CSRF protection (applied globally via init_app) ───────────────────────────
csrf = CSRFProtect()

# ── Rate limiting (Redis-backed in production) ────────────────────────────────
#    key_func uses the real client IP, respecting X-Forwarded-For behind Nginx
limiter = Limiter(key_func=get_remote_address)

# ── Response caching (Redis in production, SimpleCache in dev/test) ───────────
cache = Cache()

# ── Outbound email ────────────────────────────────────────────────────────────
mail = Mail()
