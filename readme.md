# Orbit — Webex Calling Management Platform

Orbit is a self-hosted, full-stack Flask web application for managing
Webex Calling infrastructure: DID pool management, ServiceNow
provisioning automation, call forward scheduling, and a real-time
analytics dashboard — all backed by PostgreSQL, Redis, and Celery.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [Quick Start (Docker)](#quick-start-docker)
4. [First-Time Setup](#first-time-setup)
5. [Configuration Reference](#configuration-reference)
6. [Development Setup](#development-setup)
7. [Running Tests](#running-tests)
8. [CLI Reference](#cli-reference)
9. [Deployment](#deployment)
10. [Project Structure](#project-structure)
11. [Contributing](#contributing)

---

## Architecture

```
┌─────────────┐     ┌──────────────────────────────────────────┐
│   Browser   │────▶│  Nginx (TLS termination, static files)   │
└─────────────┘     └─────────────────┬────────────────────────┘
                                       │
                    ┌──────────────────▼────────────────────────┐
                    │  Gunicorn (4 workers, gevent)              │
                    │  Flask Application (Orbit)                  │
                    │  Blueprints: auth, did, snow, cf,           │
                    │             reports, users, settings, tasks │
                    └──────────────────┬────────────────────────┘
                                       │
              ┌────────────────────────┼────────────────────────┐
              │                        │                         │
   ┌──────────▼──────────┐  ┌─────────▼──────────┐  ┌─────────▼──────────┐
   │  PostgreSQL 16       │  │  Redis 7            │  │  Celery Workers    │
   │  (all app data)      │  │  Broker / Cache /   │  │  + Beat Scheduler  │
   │                      │  │  Rate limit store   │  │  6 queues          │
   └─────────────────────┘  └────────────────────┘  └────────────────────┘
```

---

## Prerequisites

| Requirement | Minimum Version |
|-------------|----------------|
| Docker      | 24+            |
| Docker Compose | v2.20+      |
| Python      | 3.12+ (dev only) |
| PostgreSQL  | 16+ (provided via Docker) |
| Redis       | 7+ (provided via Docker) |

---

## Quick Start (Docker)

```bash
# 1. Clone the repository
git clone https://github.com/yourorg/orbit.git
cd orbit

# 2. Create your environment file
cp .env.example .env
# Edit .env — set SECRET_KEY and DATABASE_URL at minimum

# 3. Build and start all services
docker compose up -d --build

# 4. Create the first superadmin account
docker compose exec web flask admin create-admin

# 5. Open the application
open https://localhost
```

---

## First-Time Setup

After creating your superadmin account:

1. **Sign in** at `https://your-host/admin/login`
2. **Settings → Webex API** — paste your Webex access token and org ID,
   then click *Test Connection*
3. **Settings → ServiceNow** — configure instance URL, credentials and
   webhook secret, then copy the webhook URL into your SNOW catalog item
4. **Settings → Email / SMTP** — configure outbound SMTP and send a test email
5. **DID Management → Pools** — create at least one DID pool
6. **DID Management → DIDs** — bulk-import DIDs via CSV or add individually
7. **Task Monitor** — verify workers are online and beat is alive

---

## Configuration Reference

All sensitive values (tokens, passwords, secrets) are stored **encrypted**
in the database via Settings UI. The only values that must be in `.env`
are the bootstrap secrets needed before the DB is available:

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | ✅ | Flask secret key — long random string |
| `DATABASE_URL` | ✅ | PostgreSQL connection string |
| `REDIS_URL` | ✅ | Redis URL for cache and rate limiting |
| `CELERY_BROKER_URL` | ✅ | Redis URL for Celery broker |
| `CELERY_RESULT_BACKEND` | ✅ | Redis URL for Celery results |
| `FLASK_ENV` | | `production` / `development` (default: `production`) |
| `LOG_LEVEL` | | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `GUNICORN_WORKERS` | | Number of Gunicorn worker processes (default: `4`) |

---

## Development Setup

```bash
# Create virtualenv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt -r requirements-dev.txt

# Start infrastructure only
docker compose up -d db redis

# Set environment
export FLASK_ENV=development
export DATABASE_URL=postgresql://orbit:orbit_pass@localhost:5432/orbit
export REDIS_URL=redis://localhost:6379/2
export CELERY_BROKER_URL=redis://localhost:6379/0
export CELERY_RESULT_BACKEND=redis://localhost:6379/1
export SECRET_KEY=dev-secret-key

# Create database and seed config
flask db upgrade
flask admin seed-config
flask admin create-admin

# Start Flask dev server
flask run --reload

# In a separate terminal — start Celery worker
celery -A celery_worker.celery worker --loglevel=debug \
  --queues=default,snow,webex_sync,call_forward,maintenance,notifications

# In a separate terminal — start Beat scheduler
celery -A celery_worker.celery beat --loglevel=info
```

---

## Running Tests

```bash
# Install test dependencies
pip install -r requirements-test.txt

# Run full test suite with coverage
pytest tests/ --cov=app --cov-report=term-missing

# Run specific test module
pytest tests/test_did.py -v

# Run with live DB (slower, catches migration issues)
DATABASE_URL=postgresql://orbit:orbit_pass@localhost:5432/orbit_test \
  pytest tests/ --cov=app
```

---

## CLI Reference

All commands are run inside the container or with the virtualenv active:

```bash
# User management
flask admin create-admin                       # Interactive user creation
flask admin list-users                         # List all admin users
flask admin reset-password <username>          # Reset a user's password

# Configuration
flask admin seed-config                        # Insert default AppConfig values
flask admin show-config                        # Display all config keys
flask admin show-config --show-secrets         # Reveal encrypted values
flask admin set-config <KEY> <VALUE>           # Set a single config key
flask admin set-config <KEY> <VALUE> --encrypted  # Store encrypted

# Webex sync
flask admin sync-webex                         # Full Webex entity sync
flask admin sync-webex --no-hunt-groups        # Skip hunt groups

# Maintenance
flask admin purge-audit --days 365             # Delete old audit entries
flask admin test-connections                   # Test all external connections

# Database
flask db upgrade                               # Apply pending migrations
flask db downgrade                             # Roll back one revision
flask db history                               # Show migration history
```

---

## Deployment

### Production Deployment (Docker Compose)

```bash
# On your production server
cd /opt/orbit
git pull origin main

# Pull latest image and restart
docker compose pull
docker compose up -d --remove-orphans
docker compose exec web flask db upgrade

# Verify all services are healthy
docker compose ps
docker compose logs web --tail=50
```

### Environment Hardening Checklist

- [ ] `SECRET_KEY` is a cryptographically random string of ≥ 64 chars
- [ ] `DATABASE_URL` points to a dedicated PostgreSQL user with least privilege
- [ ] TLS certificate is valid and auto-renewing (Let's Encrypt recommended)
- [ ] Nginx `server_name` is set to your actual domain
- [ ] Docker ports `5432` and `6379` are **not** exposed externally
- [ ] `.env` is not committed to version control (`.gitignore` covers it)
- [ ] Superadmin account has a strong password with MFA enabled
- [ ] Audit log retention is set to an appropriate value (default 365 days)
- [ ] Regular database backups are configured

---

## Project Structure

```
orbit/
├── app/
│   ├── __init__.py          Application factory
│   ├── extensions.py        Flask extension instances
│   ├── models/              SQLAlchemy models
│   ├── routes/              Flask blueprints
│   ├── forms/               WTForms form classes
│   ├── services/            Business logic layer
│   ├── tasks/               Celery task modules
│   ├── utils/               Decorators, crypto, filters
│   ├── templates/           Jinja2 HTML templates
│   └── static/              CSS, JS, images
├── migrations/              Alembic migration scripts
├── tests/                   pytest test suite
├── scripts/                 Docker entrypoint and helpers
├── nginx/                   Nginx configuration
├── .github/workflows/       CI/CD pipelines
├── config.py                Flask configuration classes
├── wsgi.py                  Production WSGI entry point
├── celery_worker.py         Celery entry point
├── Dockerfile               Multi-stage production image
├── docker-compose.yml       Full stack definition
├── .env.example             Environment variable template
└── README.md                This file
```

---

## Contributing

1. Fork the repository and create a feature branch:
   `git checkout -b feature/my-improvement`
2. Run the test suite before committing: `pytest tests/ -q`
3. Run the linter: `ruff check . && black --check .`
4. Open a pull request against `develop` — CI runs automatically
5. All PRs require at least one review before merge to `main`

---

*Orbit is an internal tool — built and maintained by SEC Engineers.*
