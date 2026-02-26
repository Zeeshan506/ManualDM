# CRM Backend (FastAPI + Celery)

## Overview

This service handles:
- Instagram webhook ingestion and persistence
- Lead/contact/message lifecycle
- Meta Conversion event creation + async dispatch
- Staff authentication and user management
- Lead payment recording (custom payments now, Stripe path reserved)

## Backend Structure (Cleaned)

Canonical code now lives under:

- `app/core/` - database, security, dependencies, celery setup
- `app/db/` - SQLAlchemy models
- `app/api/routes/` - API route modules
- `app/services/` - feature services
- `app/tasks/` - Celery task modules
- `app/scripts/` - reserved for operational scripts

Top-level compatibility shims have been removed; runtime and imports should use `app.*` module paths.

Webhook handling is intentionally non-blocking:
1. Save webhook payload to `webhook_events`
2. Enqueue async processing (`tasks.process_webhook_event`)
3. Return quickly

## Run

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

## Route Map

### Webhook / Legacy App Endpoints (`app/main.py`)

- `GET /webhook` - Meta webhook verification
# CRM Backend (FastAPI + Celery)

This repository contains the backend API and worker for the CRM system. The service exposes HTTP APIs (FastAPI) and runs background jobs with Celery.

**Quick overview**
- API server: `app.main:app` (FastAPI)
- Celery worker: `app.core.celery_app.celery_app`
- DB models: `app/db/models.py` (SQLAlchemy)
- Migrations: `migrations/` (Alembic)

**Run locally (development)**

Start the API (reload on changes):

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Start Celery worker (in another shell):

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

For production use on Railway, bind to the `PORT` env var (see Railway section).

**Railway deployment (recommended commands)**

When deploying to Railway, ensure the service command binds to `$PORT`. Example `start` commands:

- Simple (dev / quick test):

```bash
uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

- Recommended production option (use Gunicorn + Uvicorn workers):

```bash
gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT --workers 2
```

If you run Celery on Railway as a separate service, set a separate service with the command:

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info --concurrency=1
```

Railway environment variables to set (at minimum): `DATABASE_URL`, `REDIS_CONNECTION_STRING`, `VERIFY_TOKEN`, `ACCESS_TOKEN`, `IG_ACCOUNT_ID` (or `INSTAGRAM_ACCOUNT_ID`).

Example minimal `Procfile` (if using one):

```
web: gunicorn -k uvicorn.workers.UvicornWorker app.main:app --bind 0.0.0.0:$PORT
worker: celery -A app.core.celery_app.celery_app worker --loglevel=info
```

File structure (backend-focused)

```
test_server/
├─ alembic.ini
├─ pyproject.toml
├─ requirements.txt
├─ README.md
├─ payload.json
├─ app/
│  ├─ __init__.py
│  ├─ main.py                # FastAPI app and legacy webhook handlers
│  ├─ core/
│  │  ├─ __init__.py
│  │  ├─ celery_app.py      # Celery app instance
│  │  ├─ database.py        # DB session / engine
│  │  ├─ dependencies.py
│  │  └─ security.py
│  ├─ api/
│  │  ├─ __init__.py
│  │  └─ routes/            # api route modules (auth, users, leads, admin, etc.)
│  ├─ db/
│  │  ├─ __init__.py
│  │  └─ models.py          # SQLAlchemy models (leads, users, invoices, etc.)
│  ├─ services/
│  │  └─ meta_conversion_events.py
│  ├─ tasks/
│  │  └─ meta_tasks.py      # Celery tasks
│  └─ scripts/
│     ├─ backfill_lead_referrals.py
│     └─ seed_user.py
├─ database/                 # raw SQL helpers / schema SQL
├─ migrations/               # Alembic migration versions
└─ ProgressionPlan.md
```

Notes about key folders

- `app/core/` — application wiring: DB, Celery, auth/ security helpers and dependency injection.
- `app/api/routes/` — HTTP endpoints. Prefer adding new endpoints under `routes/` and keeping `app.main` small.
- `app/db/models.py` — canonical SQLAlchemy models used across the app and Celery tasks.
- `app/services/` — domain logic (event building, payment processing, referral resolution).
- `app/tasks/` — background tasks that post to external APIs (Meta CAPI) and update DB state.

Environment variables

Required (at least for a running deployment):

- `DATABASE_URL` — Postgres connection string
- `REDIS_CONNECTION_STRING` — Redis for Celery broker/result backend
- `VERIFY_TOKEN` — webhook verification token
- `ACCESS_TOKEN` — Meta/IG access token used by the services

Optional / feature flags:

- `IG_ACCOUNT_ID` or `INSTAGRAM_ACCOUNT_ID`
- `META_GRAPH_VERSION` (defaults to v25.0 in code)
- `AUTO_APPLY_MIGRATIONS` — if true, the app will run alembic migrations on startup

Developer scripts

- Backfill lead referrals (dry-run):

```bash
python app/scripts/backfill_lead_referrals.py --dry-run
```

Tips and recommendations

- Bind to `$PORT` on Railway. Use Gunicorn+Uvicorn for production.
- Run Celery as a separate Railway service (or use a dedicated worker dyno) and configure Redis accordingly.
- Keep secrets in Railway environment variables, not in the repo.

If you'd like, I can also add a `Procfile` or a Railway service template and a minimal `Dockerfile` for explicit container deployments.

