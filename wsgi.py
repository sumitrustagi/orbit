"""
Orbit — Production WSGI Entry Point
=====================================
Used by Gunicorn, uWSGI, and any WSGI-compatible server.

Gunicorn (recommended):
    gunicorn wsgi:app \\
      --workers 4 \\
      --threads 2 \\
      --worker-class gevent \\
      --bind 0.0.0.0:8000 \\
      --timeout 120 \\
      --access-logfile - \\
      --error-logfile -

uWSGI alternative:
    uwsgi --module wsgi:app --http :8000 --processes 4 --threads 2

Direct Python (dev only — use flask run instead):
    python wsgi.py
"""
from app import create_app

app = create_app()

if __name__ == "__main__":
    # Only reached when run directly — not via Gunicorn
    app.run(host="0.0.0.0", port=8000)
