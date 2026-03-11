import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()

class BaseConfig:
    # ── Core ──────────────────────────────────────────────────────────────────
    SECRET_KEY                  = os.environ.get("SECRET_KEY", "change-me-in-production")
    FERNET_KEY                  = os.environ.get("FERNET_KEY", "").encode()
    APP_NAME                    = "Orbit"
    APP_VERSION                 = "1.0.0"
    APP_STATE                   = os.environ.get("APP_STATE", "setup_pending")

    # ── Database ──────────────────────────────────────────────────────────────
    SQLALCHEMY_DATABASE_URI     = os.environ.get("DATABASE_URL", "sqlite:///orbit.db")
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS   = {
        "pool_pre_ping": True,
        "pool_recycle": 300,
        "pool_size": 10,
        "max_overflow": 20,
    }

    # ── Sessions ──────────────────────────────────────────────────────────────
    SESSION_COOKIE_SECURE       = True
    SESSION_COOKIE_HTTPONLY     = True
    SESSION_COOKIE_SAMESITE     = "Lax"
    SESSION_COOKIE_NAME         = "orbit_session"
    PERMANENT_SESSION_LIFETIME  = timedelta(
        seconds=int(os.environ.get("PERMANENT_SESSION_LIFETIME", 1800))
    )
    SESSION_TIMEOUT_MINUTES     = int(os.environ.get("SESSION_TIMEOUT_MINUTES", 30))

    # ── Celery ────────────────────────────────────────────────────────────────
    CELERY_BROKER_URL           = os.environ.get("CELERY_BROKER_URL")
    CELERY_RESULT_BACKEND       = os.environ.get("CELERY_RESULT_BACKEND")
    CELERY_ACCEPT_CONTENT       = ["json"]
    CELERY_TASK_SERIALIZER      = "json"
    CELERY_RESULT_SERIALIZER    = "json"
    CELERY_TIMEZONE             = "UTC"
    CELERY_ENABLE_UTC           = True
    CELERY_TASK_TRACK_STARTED   = True
    CELERY_TASK_TIME_LIMIT      = 600       # 10 min hard limit
    CELERY_TASK_SOFT_TIME_LIMIT = 540       # 9 min soft limit

    # ── Celery Beat Schedule (always-on background jobs) ──────────────────────
    CELERYBEAT_SCHEDULE = {
        "purge-old-audit-logs": {
            "task": "app.tasks.audit.purge_old_audit_logs",
            "schedule": 86400.0,   # daily
            "options": {"expires": 3600},
        },
        "process-call-forward-schedules": {
            "task": "app.tasks.call_forwarding.process_scheduled_forwards",
            "schedule": 60.0,      # every minute
        },
        "sync-did-pools": {
            "task": "app.tasks.did.sync_all_did_pools",
            "schedule": 3600.0,    # hourly
        },
    }

    # ── File Uploads ──────────────────────────────────────────────────────────
    MAX_CONTENT_LENGTH          = 20 * 1024 * 1024    # 20 MB
    UPLOAD_FOLDER               = os.path.join(os.environ.get("ORBIT_HOME", "/opt/orbit"), "app/static/uploads")
    LOGO_MAX_SIZE_BYTES         = 200 * 1024           # 200 KB
    LOGO_MAX_DIMENSIONS         = (512, 512)           # px
    ALLOWED_LOGO_EXTENSIONS     = {"png", "svg", "jpg", "jpeg"}
    ALLOWED_AUDIO_EXTENSIONS    = {"wav", "mp3", "ulaw"}
    ALLOWED_CERT_EXTENSIONS     = {"pem", "crt", "cer", "key"}

    # ── Email / SMTP ──────────────────────────────────────────────────────────
    MAIL_SERVER                 = os.environ.get("SMTP_HOST", "")
    MAIL_PORT                   = int(os.environ.get("SMTP_PORT", 587))
    MAIL_USE_TLS                = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    MAIL_USERNAME               = os.environ.get("SMTP_USERNAME", "")
    MAIL_PASSWORD               = os.environ.get("SMTP_PASSWORD", "")
    MAIL_DEFAULT_SENDER         = os.environ.get("SMTP_FROM", "noreply@orbit.local")

    # ── Webex ─────────────────────────────────────────────────────────────────
    WEBEX_ACCESS_TOKEN          = os.environ.get("WEBEX_ACCESS_TOKEN", "")
    WEBEX_ORG_ID                = os.environ.get("WEBEX_ORG_ID", "")

    # ── LDAP ──────────────────────────────────────────────────────────────────
    LDAP_HOST                   = os.environ.get("LDAP_HOST", "")
    LDAP_PORT                   = int(os.environ.get("LDAP_PORT", 389))
    LDAP_USE_SSL                = os.environ.get("LDAP_USE_SSL", "false").lower() == "true"
    LDAP_BIND_DN                = os.environ.get("LDAP_BIND_DN", "")
    LDAP_BIND_PASSWORD          = os.environ.get("LDAP_BIND_PASSWORD", "")
    LDAP_BASE_DN                = os.environ.get("LDAP_BASE_DN", "")
    LDAP_USER_FILTER            = os.environ.get("LDAP_USER_FILTER", "(mail={username})")

    # ── SSO ───────────────────────────────────────────────────────────────────
    SSO_ENABLED                 = os.environ.get("SSO_ENABLED", "false").lower() == "true"
    SSO_PROVIDER                = os.environ.get("SSO_PROVIDER", "")

    # ── ServiceNow ────────────────────────────────────────────────────────────
    SNOW_INSTANCE               = os.environ.get("SNOW_INSTANCE", "")
    SNOW_USERNAME               = os.environ.get("SNOW_USERNAME", "")
    SNOW_PASSWORD               = os.environ.get("SNOW_PASSWORD", "")
    SNOW_CATALOG_ITEM_ID        = os.environ.get("SNOW_CATALOG_ITEM_ID", "")

    # ── Audit Log ─────────────────────────────────────────────────────────────
    AUDIT_LOG_RETENTION_DAYS    = int(os.environ.get("AUDIT_LOG_RETENTION_DAYS", 120))

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    RATELIMIT_STORAGE_URL       = os.environ.get("REDIS_URL")
    RATELIMIT_DEFAULT           = "200 per day;50 per hour"
    RATELIMIT_HEADERS_ENABLED   = True

    # ── Caching ───────────────────────────────────────────────────────────────
    CACHE_TYPE                  = "RedisCache"
    CACHE_REDIS_URL             = os.environ.get("REDIS_URL")
    CACHE_DEFAULT_TIMEOUT       = 300

    # ── Server ────────────────────────────────────────────────────────────────
    SERVER_FQDN                 = os.environ.get("SERVER_FQDN", "localhost")
    ORBIT_HOME                  = os.environ.get("ORBIT_HOME", "/opt/orbit")
    CERT_PATH                   = os.environ.get("CERT_PATH", "")
    KEY_PATH                    = os.environ.get("KEY_PATH", "")


class DevelopmentConfig(BaseConfig):
    DEBUG                       = True
    TESTING                     = False
    SESSION_COOKIE_SECURE       = False
    SQLALCHEMY_DATABASE_URI     = os.environ.get("DATABASE_URL", "sqlite:///orbit_dev.db")
    CACHE_TYPE                  = "SimpleCache"
    RATELIMIT_ENABLED           = False


class ProductionConfig(BaseConfig):
    DEBUG                       = False
    TESTING                     = False
    PROPAGATE_EXCEPTIONS        = True


class TestingConfig(BaseConfig):
    TESTING                     = True
    DEBUG                       = True
    SQLALCHEMY_DATABASE_URI     = "sqlite:///:memory:"
    WTF_CSRF_ENABLED            = False
    SESSION_COOKIE_SECURE       = False
    CACHE_TYPE                  = "SimpleCache"


config_map = {
    "development": DevelopmentConfig,
    "production":  ProductionConfig,
    "testing":     TestingConfig,
    "default":     ProductionConfig,
}
