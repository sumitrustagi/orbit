#!/usr/bin/env bash
# ════════════════════════════════════════════════════════════════
# Orbit Docker Entrypoint
# Usage: entrypoint.sh [web|worker|beat|dev|migrate|shell]
# ════════════════════════════════════════════════════════════════
set -euo pipefail

MODE="${1:-web}"

echo "[Orbit] Starting in mode: $MODE"

wait_for_db() {
  echo "[Orbit] Waiting for database…"
  until python -c "
import os, psycopg2
try:
    psycopg2.connect(os.environ['DATABASE_URL'])
    print('  DB ready.')
except Exception as e:
    raise SystemExit(str(e))
" 2>/dev/null; do
    sleep 2
  done
}

run_migrations() {
  echo "[Orbit] Running database migrations…"
  flask db upgrade
  echo "[Orbit] Migrations complete."
}

seed_config() {
  echo "[Orbit] Seeding default config…"
  flask admin seed-config || true
}

case "$MODE" in

  web)
    wait_for_db
    run_migrations
    seed_config
    echo "[Orbit] Starting Gunicorn…"
    exec gunicorn wsgi:app \
      --workers  "${GUNICORN_WORKERS:-4}" \
      --threads  "${GUNICORN_THREADS:-2}" \
      --bind     "${GUNICORN_BIND:-0.0.0.0:8000}" \
      --timeout  "${GUNICORN_TIMEOUT:-120}" \
      --worker-class gevent \
      --access-logfile - \
      --error-logfile  - \
      --log-level "${LOG_LEVEL:-info}"
    ;;

  worker)
    wait_for_db
    echo "[Orbit] Starting Celery worker…"
    exec celery -A celery_worker.celery worker \
      --loglevel="${LOG_LEVEL:-info}" \
      --queues=default,snow,webex_sync,call_forward,maintenance,notifications \
      --concurrency="${CELERY_CONCURRENCY:-4}" \
      --max-tasks-per-child=500
    ;;

  beat)
    wait_for_db
    echo "[Orbit] Starting Celery beat…"
    exec celery -A celery_worker.celery beat \
      --loglevel="${LOG_LEVEL:-info}" \
      --scheduler celery.beat:PersistentScheduler
    ;;

  dev)
    wait_for_db
    run_migrations
    seed_config
    echo "[Orbit] Starting Flask dev server…"
    exec flask run --host=0.0.0.0 --port=8000 --reload
    ;;

  migrate)
    wait_for_db
    run_migrations
    ;;

  shell)
    wait_for_db
    exec flask shell
    ;;

  *)
    echo "[Orbit] Unknown mode: $MODE"
    echo "Usage: entrypoint.sh [web|worker|beat|dev|migrate|shell]"
    exit 1
    ;;

esac
