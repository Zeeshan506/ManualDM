# Social Lead Operations Platform — Backend

This backend orchestrates the operational path from inbound Instagram/Meta webhook activity to persistent lead and conversation records, CRM work queues, staff actions, notifications, manual payment records, and queued Meta Conversion API feedback. It is designed as the API and asynchronous processing layer behind a social-lead workflow—not simply as a web server.

## Project Context

This service is the backend/API and asynchronous processing layer of a larger frontend + backend system. It receives external events, stores operational data, exposes CRM and administration APIs, coordinates background work, and publishes realtime updates for a frontend consumer.

## The Problem

Social leads arrive as messaging and referral events, while the business needs a durable record of the conversation, a lead that can be worked by staff, and explicit operational state. Without that layer, inbound activity is fragmented, follow-up and lead ownership are manual, delivery to external APIs can delay webhook acknowledgement, and downstream conversion outcomes are not structured for feedback to Meta.

## The Solution

The service centralizes Instagram webhook ingestion, raw-event durability, lead and message persistence, CRM workflows, role-based staff administration, realtime notifications, manual payment capture, and Meta Conversion API event dispatch. Redis and Celery move retryable external or non-interactive work out of the webhook/API request path.

## Core Capabilities

- Instagram webhook verification and intake, including request-size and in-process rate guards.
- Durable webhook-event records with idempotency keys, enqueue state, processing state, retry metadata, and a periodic enqueue redrive.
- Normalization of supported Messenger-style and Instagram `changes` message payloads into contacts, leads, and inbound messages.
- A lead/inbox API for lead lists, chat history, lead engagement claims/releases, dead-lead requests and administrator marking, custom outbound messages, dashboard data, and notification retrieval.
- Lead lifecycle fields for `new`, `paid`, and `dead` business status, plus separate assignment/engagement state and dead-lead request metadata.
- JWT-based sign-in, active-account checks, and `sales_rep`, `admin`, and `sudo_admin` roles; privileged user-management and team-activity endpoints are restricted by role.
- Asynchronous audit-log persistence and Redis-backed websocket broadcasts for messages and system notifications.
- Manual payment recording that creates invoice and payment-event records, marks a lead paid, and can queue a Purchase conversion event. There is no live Stripe checkout or Stripe webhook integration in this repository.
- Persisted Meta conversion-event records, including referral/contact handling, `LeadSubmitted`, Purchase, and custom event creation; queued outbound delivery to the Meta Graph events endpoint.

## System Architecture

```text
Instagram / Meta
       │  GET/POST /webhook
       ▼
FastAPI API ──► SQL database: webhook events, contacts, messages, leads,
       │                    invoices/payments, conversion events, audit/notifications
       │
       ├──► Redis broker / PubSub ◄──► WebSocket frontend consumers
       │            │
       │            ▼
       │         Celery workers
       │            ├── event processing and reply tasks
       │            ├── Meta conversion delivery
       │            └── audit-log persistence
       │
       └────────────────────────────► Meta Graph API / Conversion API
```

FastAPI provides the HTTP and websocket interfaces. SQLAlchemy supports SQLite for local fallback and PostgreSQL URLs for deployed databases. Redis is used both by Celery (broker by default; result backend on a separate Redis DB by default) and by the websocket Pub/Sub layer. The related frontend consumes the REST and websocket interfaces.

## Lead Processing Workflow

1. Meta verifies `GET /webhook` with the configured verify token, then posts Instagram webhook payloads to `POST /webhook`.
2. The API stores the raw payload as a `WebhookEvent`. Its idempotency key uses the source and platform message ID when available, otherwise a hash of the body. A duplicate returns the existing event rather than creating another record.
3. For an Instagram event, the API attempts to enqueue `tasks.process_webhook_event` and still returns an acknowledgement if enqueueing fails. Unprocessed pending/failed events are eligible for periodic redrive.
4. The worker extracts a supported sender ID and message form, upserts the Instagram contact and its one-to-one lead, records referral data when present, and persists inbound messages. New leads begin unassigned with business status `new`.
5. The worker publishes new-message updates and persists/publishes an incoming-message notification when it has a lead. Staff can claim or release engagement, sales reps can request a dead marking, and administrators can mark a lead dead. A manual payment marks a lead `paid`.
6. When contact details include both email and phone, the API creates a `LeadSubmitted` conversion-event record and queues it. Manual payment capture creates a Purchase event; event delivery is handled asynchronously.

## Asynchronous Processing

Celery keeps external calls, event processing, retries, and audit writes off request paths. The configured queues are `events`, `meta`, and `audit`:

- `events`: webhook processing, webhook enqueue redrive, and automated Instagram replies.
- `meta`: conversion-event delivery to Meta.
- `audit`: activity/audit-log persistence.

The app routes named tasks to those queues and configures a periodic `redrive_webhook_enqueue` task every 30 seconds. Task failures use exponential backoff with a configurable repeat count (default: three retries); webhook records retain failure and next-retry information. The worker avoids reprocessing events already marked processed.

## Meta Integration

### Inbound webhook side

`/webhook` accepts Instagram object payloads, persists the original event data, and supports both `entry.messaging` and `entry.changes[].value.messages` forms for sender/message extraction. Referral-bearing payloads are stored with the contact/lead; the current outbound sender intentionally normalizes a referral Contact event to `ViewContent` and skips sending it.

### Outbound conversion feedback

`MetaConversionEvent` records hold the event payload and linkage to a lead. The service can build and queue `LeadSubmitted`, Purchase, and custom events to the Meta Graph `/{dataset-or-pixel}/events` endpoint. For generated lead events it normalizes and SHA-256 hashes email and phone before placing them in `user_data`; it can include the configured Instagram business-account ID.

Configuration requires a dataset/pixel identifier and access token for outbound delivery. It also requires the relevant Instagram business account and Graph API settings to send Instagram messages. Never place these values in the README or source control.

## Authentication & Authorization

Authentication issues signed JWT bearer tokens after username/password login and rejects inactive database users. The modeled roles are:

- `sales_rep`: lead viewing/engagement, custom messaging, payment access, and dead-lead requests.
- `admin`: administrator-only lead deletion/dead marking and scoped staff-management actions, in addition to staff operations.
- `sudo_admin`: highest role; can manage roles and sudo-admin creation, subject to safeguards against changing or deleting another sudo-admin account.

Activity-producing paths enqueue audit records, and administrator APIs expose activity logs and team performance. Access enforcement is route-specific in the current codebase; review all public routes and websocket endpoints before production exposure.

## Engineering Highlights

- Durable webhook ingestion separates acknowledgement from processing and records enough state to redrive queue failures.
- Stable idempotency keys protect the raw webhook record from duplicate delivery.
- Payload handling bridges two supported Meta message shapes while retaining the original event/message payload for traceability.
- Redis Pub/Sub allows worker-originated messages and notifications to reach websocket connections across API processes.
- Conversion outcomes are persisted before being dispatched, linking feedback events to the operational lead and, for manual payments, to the payment record.
- Separate business status, chat engagement, ownership, and dead-mark request metadata avoid collapsing operational workflow into one field.

## Technology Stack

- API and validation: FastAPI, Pydantic, Uvicorn
- Data: SQLAlchemy, Alembic, SQLite (local fallback), PostgreSQL
- Background and realtime: Celery, Redis, Redis Pub/Sub, WebSockets
- Integrations: Meta Graph API / Conversion API, Instagram messaging
- Security and operations: JWT (`python-jose`), bcrypt/passlib, structured logging, CORS, request IDs, configurable guards

## Repository Role

This repository contains the backend/API and asynchronous processing layer. The user interface lives in the related frontend repository.

## Related Repository

[ManualDM Frontend](https://github.com/Zeeshan506/ManualDM_Frontend)

## Project Structure

```text
app/
  api/routes/       HTTP, auth, user, and admin routes
  core/             database, security, Celery, Redis, logging, websockets
  db/models.py      persistent operational models
  services/         webhook, event, Meta, audit, and notification logic
  tasks/            Celery task implementations
  scripts/          operational seed/backfill scripts
migrations/         Alembic migrations
.env.example        configuration template
main.py              local application entry point
```

## Local Setup

Prerequisites: Python 3.10+, Redis, and either a PostgreSQL database or the built-in SQLite fallback. PostgreSQL URLs receive `sslmode=require` if no SSL mode is supplied. The repository includes a `uv.lock`, but dependencies are currently enumerated in `requirements.txt`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Set appropriate non-placeholder values in `.env`, then run the API:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The application initializes SQLite tables on startup. For PostgreSQL it runs Alembic migrations on startup, controlled by `AUTO_APPLY_MIGRATIONS`.

## Environment Configuration

Use `.env.example` as the template; configure names and purposes, not secrets:

- Runtime: `PORT`, `LOG_LEVEL`, `FRONTEND_URL`.
- Database: `DATABASE_URL`, `AUTO_APPLY_MIGRATIONS`.
- Authentication and webhook verification: `AUTH_SECRET_KEY`, `ACCESS_TOKEN_EXPIRE_MINUTES`, `VERIFY_TOKEN`; the `SUDO_*` variables enable the code’s special sudo/login paths, and `CEO_*` values are used only by the seed script.
- Request guards: `REQUEST_WINDOW_SECONDS`, `LOGIN_RATE_LIMIT_PER_WINDOW`, `WEBHOOK_RATE_LIMIT_PER_WINDOW`, `WEBHOOK_VERIFY_RATE_LIMIT_PER_WINDOW`, `WEBHOOK_MAX_BODY_BYTES`.
- Redis/Celery: `REDIS_CONNECTION_STRING`, optional `CELERY_BROKER_URL`, optional `CELERY_RESULT_BACKEND`, and the task retry-count variables (`TASK_REPEAT_COUNT`, `REPEAT_COUNT`, and task-specific overrides).
- Instagram messaging: the Instagram access-token variable(s), `IG_ACCOUNT_ID`, `IG_GRAPH_VERSION`, `IG_MESSAGING_PRODUCT`, and optional `IG_AUTOREPLY_TEXT`. The code checks a legacy token override before `IG_ACCESS_TOKEN`; keep that private identifier out of public documentation.
- Meta conversion delivery: `ACCESS_TOKEN`, `DATASET_ID` or `META_PIXEL_ID`, `META_GRAPH_VERSION`; `INSTAGRAM_ACCOUNT_ID` and `IG_USER_ID` are fallbacks for the business-account identifier.

Do not use the development defaults for authentication or webhook verification in a deployed environment.

## Running Workers

Run a worker that consumes all configured queues:

```bash
celery -A app.core.celery_app.celery_app worker --loglevel=info -Q events,meta,audit
```

Run the scheduler as a separate process to execute the configured enqueue redrive every 30 seconds:

```bash
celery -A app.core.celery_app.celery_app beat --loglevel=info
```

Workers can instead be separated by queue (`-Q events`, `-Q meta`, or `-Q audit`) when deploying distinct worker pools. Beat should run once per environment.

## Current Status

The implemented backend includes durable Instagram webhook handling, contact/lead/message persistence, CRM operations, roles and admin management, audit and notification records, Redis-backed realtime broadcasts, manual payment capture, and queued Meta conversion delivery. Database migrations for webhook durability/idempotency, activity logs, dead-lead workflow, and notification events are present.

The current payment path is manual/custom and mock-event oriented; no Stripe SDK, checkout-session endpoint, or Stripe webhook is implemented. The codebase also does not contain an automated lead-routing task or message-history pagination.

## Limitations / Future Work

Planned work documented in `ProgressionPlan.md` includes live Stripe payments and webhook handling, admin lead reassignment/read-only chat controls, automated lead routing, message pagination/infinite scroll support, additional indexing, and automated tests/CI. These are not represented as completed capabilities above.
