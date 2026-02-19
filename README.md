# Webhook Processing (Non-Blocking)

Webhook endpoints now do minimal work in-request:
- Persist raw webhook event
- Enqueue `tasks.process_webhook_event`
- Return response

Heavy database processing and outbound Meta calls run in Celery workers.

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
- `REPEAT_COUNT` (global retry count for all Celery tasks)
- `TASK_REPEAT_COUNT` (global retry count fallback)
- `<TASK_NAME>_REPEAT_COUNT` (per-task override; example: `SEND_AUTOMATION_REPLY_REPEAT_COUNT=5`)

