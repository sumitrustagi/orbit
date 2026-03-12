"""
Orbit — Celery Worker & Beat Entry Point
==========================================
Used by both the Celery worker process and the Beat scheduler.

The Flask app context is pushed so that all task code has access
to db, config, AppConfig, and all models.

Worker start:
    celery -A celery_worker.celery worker \\
      --loglevel=info \\
      --queues=default,snow,webex_sync,call_forward,maintenance,notifications \\
      --concurrency=4 \\
      --max-tasks-per-child=500

Beat start:
    celery -A celery_worker.celery beat \\
      --loglevel=info \\
      --scheduler celery.beat:PersistentScheduler

Flower monitoring (optional):
    celery -A celery_worker.celery flower --port=5555
"""
from app       import create_app
from app.tasks import celery_app as celery   # noqa: F401  re-exported for CLI

# Create the Flask app and push a permanent application context.
# This ensures db.session, AppConfig.get(), and all models
# are accessible inside every Celery task body.
flask_app = create_app()
flask_app.app_context().push()
