"""
All Flask extensions are instantiated here (without app binding)
and imported by the factory in __init__.py.
This prevents circular imports across blueprints.
"""
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_bcrypt import Bcrypt
from flask_mail import Mail
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_caching import Cache
from flask_cors import CORS
from celery import Celery

# Core ORM
db       = SQLAlchemy()
migrate  = Migrate()

# Auth
login_manager = LoginManager()
login_manager.login_view       = "auth.login"
login_manager.login_message    = "Please log in to access Orbit."
login_manager.login_message_category = "warning"
login_manager.session_protection = "strong"

# Password hashing
bcrypt   = Bcrypt()

# Email
mail     = Mail()

# CSRF protection
csrf     = CSRFProtect()

# Rate limiting
limiter  = Limiter(
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"],
)

# Caching
cache    = Cache()

# CORS (API routes only)
cors     = CORS()

# Celery instance (bound to app in factory)
celery   = Celery()
