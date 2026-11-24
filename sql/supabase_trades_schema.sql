create table if not exists public.historical_trades (
    id bigserial primary key,
    index_name text not null,
    session_date date not null,
    weekday text not null,
    entry_time text not null,
    exit_time text,
    direction text not null check (direction in ('CALL', 'PUT')),
    entry_price numeric(12, 2) not null,
    sl_price numeric(12, 2) not null,
    target_price numeric(12, 2) not null,
    exit_price numeric(12, 2),
    exit_reason text,
    points numeric(12, 2) not null default 0,
    r_val numeric(10, 4) not null default 0,
    source_variant text not null default '3_REFINED_ALL_INDICES',
    created_at timestamptz not null default now(),
    constraint uq_historical_trade unique (index_name, session_date, entry_time, direction, source_variant)
);

create table if not exists public.live_trades (
    id bigserial primary key,
    trade_uid text unique,
    index_name text not null,
    session_date date not null,
    weekday text not null,
    entry_time text not null,
    exit_time text,
    direction text not null check (direction in ('CALL', 'PUT')),
    entry_price numeric(12, 2) not null,
    sl_price numeric(12, 2) not null,
    target_price numeric(12, 2) not null,
    exit_price numeric(12, 2),
    exit_reason text,
    points numeric(12, 2) not null default 0,
    r_val numeric(10, 4) not null default 0,
    trade_status text not null default 'OPEN',
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.signals (
    id bigserial primary key,
    signal_uid text unique,
    index_name text not null,
    session_date date not null,
    weekday text not null,
    bar_time text not null,
    direction text check (direction in ('CALL', 'PUT')),
    status text not null,
    status_reason text,
    last_close numeric(12, 2),
    sma2 numeric(12, 2),
    sma10 numeric(12, 2),
    entry_price numeric(12, 2),
    sl_price numeric(12, 2),
    target_price numeric(12, 2),
    trades_today integer not null default 0,
    avoid_today boolean not null default false,
    gap_excluded boolean not null default false,
    payload jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);

create table if not exists public.alerts (
    id bigserial primary key,
    alert_uid text unique,
    index_name text not null,
    session_date date not null,
    alert_time text not null,
    alert_type text not null,
    direction text check (direction in ('CALL', 'PUT')),
    r_level numeric(10, 4),
    trigger_price numeric(12, 2),
    message text not null,
    delivered boolean not null default false,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);

create index if not exists idx_historical_trades_session_index
    on public.historical_trades (session_date desc, index_name);

create index if not exists idx_live_trades_session_index
    on public.live_trades (session_date desc, index_name);

create index if not exists idx_signals_session_index
    on public.signals (session_date desc, index_name, bar_time desc);

create index if not exists idx_alerts_session_index
    on public.alerts (session_date desc, index_name, alert_time desc);

alter table public.historical_trades enable row level security;
alter table public.live_trades enable row level security;
alter table public.signals enable row level security;
alter table public.alerts enable row level security;

drop policy if exists "service_role_all_historical_trades" on public.historical_trades;
create policy "service_role_all_historical_trades"
    on public.historical_trades for all to service_role using (true) with check (true);

drop policy if exists "authenticated_read_historical_trades" on public.historical_trades;
create policy "authenticated_read_historical_trades"
    on public.historical_trades for select to authenticated using (true);

drop policy if exists "service_role_all_live_trades" on public.live_trades;
create policy "service_role_all_live_trades"
    on public.live_trades for all to service_role using (true) with check (true);

drop policy if exists "authenticated_read_live_trades" on public.live_trades;
create policy "authenticated_read_live_trades"
    on public.live_trades for select to authenticated using (true);

drop policy if exists "service_role_all_signals" on public.signals;
create policy "service_role_all_signals"
    on public.signals for all to service_role using (true) with check (true);

drop policy if exists "authenticated_read_signals" on public.signals;
create policy "authenticated_read_signals"
    on public.signals for select to authenticated using (true);

drop policy if exists "service_role_all_alerts" on public.alerts;
create policy "service_role_all_alerts"
    on public.alerts for all to service_role using (true) with check (true);

drop policy if exists "authenticated_read_alerts" on public.alerts;
create policy "authenticated_read_alerts"
    on public.alerts for select to authenticated using (true);