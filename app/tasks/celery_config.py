"""
Celery configuration: broker, result backend, queues, beat schedule,
serialisation, retry defaults, and task routing.

All timing values are read from AppConfig so they can be changed at
runtime via the Settings UI without restarting workers.
"""
from celery import Celery
from celery.schedules import crontab
from kombu import Exchange, Queue


# ── Queue definitions ─────────────────────────────────────────────────────────

QUEUES = (
    Queue("default",      Exchange("default"),      routing_key="default"),
    Queue("snow",         Exchange("snow"),          routing_key="snow"),
    Queue("webex_sync",   Exchange("webex_sync"),    routing_key="webex_sync"),
    Queue("call_forward", Exchange("call_forward"),  routing_key="call_forward"),
    Queue("maintenance",  Exchange("maintenance"),   routing_key="maintenance"),
    Queue("notifications",Exchange("notifications"), routing_key="notifications"),
)

# ── Task → queue routing ──────────────────────────────────────────────────────

TASK_ROUTES = {
    # SNOW
    "app.tasks.snow.*":          {"queue": "snow"},
    # Webex
    "app.tasks.webex.*":         {"queue": "webex_sync"},
    # Call Forward
    "app.tasks.call_forward.*":  {"queue": "call_forward"},
    # Maintenance
    "app.tasks.maintenance.*":   {"queue": "maintenance"},
    # Notifications
    "app.tasks.notifications.*": {"queue": "notifications"},
}

# ── Beat schedule ─────────────────────────────────────────────────────────────
#
# All intervals are conservative defaults.  Override per-deployment via
# CELERY_BEAT_SCHEDULE environment overrides or the AppConfig keys listed.

BEAT_SCHEDULE = {
    # ── Call forward schedule evaluation (every 2 minutes) ──────────────────
    "evaluate-call-forward-schedules": {
        "task":     "app.tasks.call_forward.evaluate_schedules",
        "schedule": crontab(minute="*/2"),
        "options":  {"queue": "call_forward"},
    },

    # ── SNOW retry queue (every 5 minutes) ───────────────────────────────────
    "retry-failed-snow-requests": {
        "task":     "app.tasks.snow.retry_failed_requests",
        "schedule": crontab(minute="*/5"),
        "options":  {"queue": "snow"},
    },

    # ── Webex entity syncs ───────────────────────────────────────────────────
    "sync-webex-users": {
        "task":     "app.tasks.webex.sync_webex_users",
        "schedule": crontab(minute="*/15"),
        "options":  {"queue": "webex_sync"},
    },
    "sync-hunt-groups": {
        "task":     "app.tasks.webex.sync_hunt_groups",
        "schedule": crontab(minute="*/30"),
        "options":  {"queue": "webex_sync"},
    },
    "sync-call-queues": {
        "task":     "app.tasks.webex.sync_call_queues",
        "schedule": crontab(minute="*/30"),
        "options":  {"queue": "webex_sync"},
    },
    "sync-auto-attendants": {
        "task":     "app.tasks.webex.sync_auto_attendants",
        "schedule": crontab(minute="0", hour="*/2"),
        "options":  {"queue": "webex_sync"},
    },

    # ── Quarantine release check (hourly) ─────────────────────────────────────
    "release-quarantine-dids": {
        "task":     "app.tasks.maintenance.release_quarantine_dids",
        "schedule": crontab(minute="5"),         # 5 past every hour
        "options":  {"queue": "maintenance"},
    },

    # ── Audit log purge (nightly 02:30) ──────────────────────────────────────
    "purge-old-audit-logs": {
        "task":     "app.tasks.maintenance.purge_old_audit_logs",
        "schedule": crontab(minute="30", hour="2"),
        "options":  {"queue": "maintenance"},
    },

    # ── System health ping (every 10 minutes) ────────────────────────────────
    "health-ping": {
        "task":     "app.tasks.maintenance.health_ping",
        "schedule": crontab(minute="*/10"),
        "options":  {"queue": "maintenance"},
    },
}


# ── Apply config to the Celery instance ──────────────────────────────────────

def apply_config(celery: Celery, app) -> None:
    """Push all configuration into the Celery instance."""
    celery.conf.update(
        # Broker / backend — read from Flask config (set from env)
        broker_url                   = app.config.get("CELERY_BROKER_URL",  "redis://localhost:6379/0"),
        result_backend               = app.config.get("CELERY_RESULT_BACKEND","redis://localhost:6379/1"),

        # Serialisation
        task_serializer              = "json",
        result_serializer            = "json",
        accept_content               = ["json"],
        result_expires               = 60 * 60 * 24,   # 24 hours

        # Queues
        task_queues                  = QUEUES,
        task_default_queue           = "default",
        task_routes                  = TASK_ROUTES,

        # Beat
        beat_schedule                = BEAT_SCHEDULE,
        beat_scheduler               = "celery.beat:PersistentScheduler",
        beat_schedule_filename       = "celerybeat-schedule",

        # Worker behaviour
        worker_prefetch_multiplier   = 1,
        task_acks_late               = True,
        task_reject_on_worker_lost   = True,

        # Retry defaults
        task_max_retries             = 5,
        task_default_retry_delay     = 60,             # seconds

        # Timezone
        timezone                     = "UTC",
        enable_utc                   = True,

        # Result chord / chord unlock
        result_chord_retry_interval  = 1.0,

        # Include task modules
        include = [
            "app.tasks.snow",
            "app.tasks.webex",
            "app.tasks.call_forward",
            "app.tasks.maintenance",
            "app.tasks.notifications",
        ],
    )
