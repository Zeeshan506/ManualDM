create table if not exists public.webhook_events (
    id integer generated always as identity primary key,
    received_at timestamptz not null,
    "object" text,
    status text not null,
    raw_payload jsonb,
    raw_body text,
    fingerprint text
);

create index if not exists idx_webhook_events_fingerprint on public.webhook_events(fingerprint);
