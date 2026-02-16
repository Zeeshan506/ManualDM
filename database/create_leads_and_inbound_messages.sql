create table if not exists public.leads (
    id integer generated always as identity primary key,
    instagram_user_id text not null unique,
    name text,
    phone text,
    email text,
    dedup_key text,
    flow_step text not null default 'new',
    status text not null default 'new',
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    last_message_at timestamptz
);

create index if not exists idx_leads_instagram_user_id on public.leads(instagram_user_id);
create index if not exists idx_leads_dedup_key on public.leads(dedup_key);

create table if not exists public.inbound_messages (
    id integer generated always as identity primary key,
    lead_id integer references public.leads(id),
    instagram_user_id text not null,
    platform_message_id text,
    text_raw text,
    text_cleaned text,
    payload jsonb,
    processed boolean not null default false,
    received_at timestamptz not null,
    created_at timestamptz not null default now()
);

create index if not exists idx_inbound_messages_lead_id on public.inbound_messages(lead_id);
create index if not exists idx_inbound_messages_instagram_user_id on public.inbound_messages(instagram_user_id);
create index if not exists idx_inbound_messages_platform_message_id on public.inbound_messages(platform_message_id);
create index if not exists idx_inbound_messages_processed on public.inbound_messages(processed);
