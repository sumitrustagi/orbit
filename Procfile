web:    gunicorn wsgi:app --workers 4 --threads 2 --bind 0.0.0.0:8000 --worker-class gevent --timeout 120 --access-logfile - --error-logfile -
worker: celery -A celery_worker.celery worker --loglevel=info --queues=default,snow,webex_sync,call_forward,maintenance,notifications --concurrency=4 --max-tasks-per-child=500
beat:   celery -A celery_worker.celery beat --loglevel=info --scheduler celery.beat:PersistentScheduler
