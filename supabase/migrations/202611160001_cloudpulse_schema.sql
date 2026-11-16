-- Optional Supabase persistence schema. Local Docker demo uses SQLite by default.
create extension if not exists pgcrypto;
create table if not exists public.sensor_devices (id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade, device_key text not null, metric_type text not null, created_at timestamptz not null default now(), unique(user_id, device_key));
create table if not exists public.sensor_readings (id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade, device_id uuid references public.sensor_devices(id) on delete set null, metric_type text not null, value double precision not null, measured_at timestamptz not null, created_at timestamptz not null default now());
create table if not exists public.dlq_messages (id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade, message_id text not null, payload jsonb not null, failure_reason text not null, retry_count integer not null default 0, created_at timestamptz not null default now());
create table if not exists public.scaling_events (id uuid primary key default gen_random_uuid(), user_id uuid not null references auth.users(id) on delete cascade, old_workers integer not null, new_workers integer not null, reason text not null, backlog integer not null, created_at timestamptz not null default now());
alter table public.sensor_devices enable row level security;
alter table public.sensor_readings enable row level security;
alter table public.dlq_messages enable row level security;
alter table public.scaling_events enable row level security;
create policy "users manage own devices" on public.sensor_devices for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "users manage own readings" on public.sensor_readings for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "users manage own dlq" on public.dlq_messages for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
create policy "users manage own scaling events" on public.scaling_events for all using (auth.uid() = user_id) with check (auth.uid() = user_id);
