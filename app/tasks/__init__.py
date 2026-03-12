"""
Celery application factory.

Import this module's `celery_app` wherever a Celery instance is needed.
The factory pattern ensures Flask app context is always available inside tasks.

Usage in tasks:
    from app.tasks import celery_app

Usage in Flask app factory (app/__init__.py):
    from app.tasks import celery_app, init_celery
    init_celery(celery_app, app)
"""
from celery import Celery

# Created without a Flask app — init_celery() binds it later.
celery_app = Celery(__name__)


def init_celery(celery: Celery, app) -> None:
    """
    Bind a Celery instance to a Flask app so that every task body
    runs inside an active Flask application context.
    """
    from app.tasks.celery_config import apply_config
    apply_config(celery, app)

    class ContextTask(celery.Task):
        abstract = True

        def __call__(self, *args, **kwargs):
            with app.app_context():
                return super().__call__(*args, **kwargs)

    celery.Task = ContextTask
