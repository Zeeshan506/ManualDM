# Webhook Processing (Non-Blocking)

Webhook endpoints now do minimal work in-request:
- Persist raw webhook event
- Enqueue `tasks.process_webhook_event`
- Return response

Heavy database processing and outbound Meta calls run in Celery workers.

## API Route Organization

All API endpoints are now properly registered through the FastAPI router pattern:
- Routes defined in `routes/api.py` using `APIRouter`
- Router included in `main.py` via `app.include_router(api_router)`
- All endpoints prefixed with `/api` and properly structured

**Available Endpoints:**
- `GET /api/leads` - Fetch all leads
- `GET /api/leads/{lead_id}` - Get specific lead details
- `GET /api/leads/{lead_id}/messages` - Fetch chat history for a lead
- `GET /api/dashboard/stats` - Get dashboard metrics

## Database Schema Sync (Postgres)

The API now auto-runs Alembic on startup when using Postgres:
- Runs `upgrade head`
- Detects model drift from `models.py`
- Auto-generates a migration file in `migrations/versions/`
- Applies the new migration immediately

You can disable auto-generation (while still applying existing migrations) with:
- `AUTO_APPLY_MIGRATIONS=false`

## Prerequisites

- Redis available via `REDIS_CONNECTION_STRING` (hosted or local)
- Python dependencies installed from `requirements.txt`

## Run API

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Run Celery Worker

```bash
celery -A celery_app.celery_app worker --loglevel=info
```

## Optional Env Overrides

- `REDIS_CONNECTION_STRING` (default base for Celery Redis URLs)
- `CELERY_BROKER_URL` (default: `REDIS_CONNECTION_STRING` with db `0`, fallback `redis://127.0.0.1:6379/0`)
- `CELERY_RESULT_BACKEND` (default: `REDIS_CONNECTION_STRING` with db `1`, fallback `redis://127.0.0.1:6379/1`)
- `AUTO_APPLY_MIGRATIONS` (default: `true`; Postgres only)
- `REPEAT_COUNT` (global retry count for all Celery tasks)
- `TASK_REPEAT_COUNT` (global retry count fallback)
- `<TASK_NAME>_REPEAT_COUNT` (per-task override; example: `SEND_AUTOMATION_REPLY_REPEAT_COUNT=5`)

## Meta Conversions API Credentials

For posting conversion events to Meta, the service reads:
- `DATASET_ID` (used as Pixel/Dataset id in `/{dataset_id}/events`)
- `ACCESS_TOKEN` (Meta token passed as `access_token`)

Backward-compatible fallbacks are still supported:
- `META_PIXEL_ID`
- `META_ACCESS_TOKEN`

Graph API version resolution order:
- `META_GRAPH_VERSION`
- `IG_GRAPH_VERSION`
- default `v25.0`

## Create Custom Meta Event From Database Lead

Use this endpoint to create a custom event row from a lead record and optionally queue async posting to Meta:

```bash
curl -X POST http://localhost:8000/leads/123/meta-events \
	-H 'Content-Type: application/json' \
	-d '{
		"event_name": "Purchase",
		"custom_data": {
			"currency": "usd",
			"value": 123.45,
			"contents": [{"id": "product123", "quantity": 1}]
		},
		"send_now": true
	}'
```

Notes:
- The event is persisted in `meta_conversion_events`.
- `user_data` is automatically enriched from lead/contact data (hashed email/phone and ig sid when available).
- Setting `send_now=false` stores the event only (no queue to Meta post task).

## Active Meta Event Flow (Current)

Implemented events are now limited to:
- `Contact`: created when an incoming Instagram webhook contains a `referral` section.
- `LeadSubmitted`: created when both email and phone are present for a lead (invoice is mocked for now).
- `Purchase`: currently mocked via API endpoint (no Stripe webhook integration yet).

Trigger mocked purchase event:

```bash
curl -X POST http://localhost:8000/leads/123/mock-purchase \
	-H 'Content-Type: application/json' \
	-d '{
		"value": 123.00,
		"currency": "USD",
		"send_now": true
	}'
```

