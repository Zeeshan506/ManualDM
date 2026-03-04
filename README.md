# CRM Backend (FastAPI + Celery)

Backend API and worker stack for the PSC CRM system.

## What this service does

- Ingests Instagram webhooks and persists raw events.
- Processes events asynchronously via Celery.
- Manages leads, messages, auth, activity logs, and admin flows.
- Builds and dispatches Meta Conversion API events.

## Quick start

```bash
cd test_server
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Run API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Run worker (separate terminal):

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info
```

## Handoff health checks

Run these before handoff:

```bash
python -m compileall app
```

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Important: startup runs `init_db()` and migration sync. If `DATABASE_URL` is unreachable, API boot fails immediately.

## Environment variables

See `.env.example` for a complete template. Core variables:

- `DATABASE_URL`
- `REDIS_CONNECTION_STRING`
- `VERIFY_TOKEN`
- `AUTH_SECRET_KEY`
- `ACCESS_TOKEN`
- `INSTAGRAM_ACCOUNT_ID` (or `IG_ACCOUNT_ID`)
- `DATASET_ID` (or `META_PIXEL_ID`)

## Deployment notes

- Bind API to `$PORT` in hosted environments.
- Run Celery as a separate process/service.
- Keep secrets in environment variables, never in Git.

## Progression roadmap

Future work is tracked in `ProgressionPlan.md` with 5 phases:

1. Admin overrides + read-only admin chat
2. Team activity analytics wiring
3. Stripe live payments
4. Scaling (routing, pagination, indexing, infinite scroll)
5. Reliability + CI/CD tests
