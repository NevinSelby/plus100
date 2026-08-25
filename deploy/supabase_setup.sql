-- Plus100 analytics: run this ONCE in your Supabase project's SQL editor.
-- Creates the events table and the stats function the admin dashboard reads.

create table if not exists usage_events (
  id bigint generated always as identity primary key,
  ts timestamptz not null default now(),
  kind text not null,                  -- 'request' or 'prediction'
  path text,
  visitor text,                        -- one-way hash, nothing identifying
  home text, away text, context text, neutral boolean
);
create index if not exists usage_events_ts on usage_events (ts);
create index if not exists usage_events_kind on usage_events (kind);

-- locked down: only the service key (used by the server) can touch it
alter table usage_events enable row level security;

create or replace function usage_stats() returns jsonb
language sql security definer set search_path = public as $$
  select jsonb_build_object(
    'since', coalesce((select extract(epoch from min(ts)) from usage_events),
                      extract(epoch from now())),
    'unique_visitors', (select count(distinct visitor) from usage_events),
    'total_requests', (select count(*) from usage_events where kind = 'request'),
    'total_predictions', (select count(*) from usage_events where kind = 'prediction'),
    'daily', (select coalesce(jsonb_agg(d.j order by d.day), '[]'::jsonb) from (
        select ts::date as day, jsonb_build_object(
          'date', to_char(ts::date, 'YYYY-MM-DD'),
          'requests', count(*) filter (where kind = 'request'),
          'predictions', count(*) filter (where kind = 'prediction'),
          'visitors', count(distinct visitor)) as j
        from usage_events where ts > now() - interval '30 days'
        group by ts::date) d),
    'top_matchups', (select coalesce(jsonb_agg(m.j order by m.n desc), '[]'::jsonb) from (
        select count(*) as n, jsonb_build_object(
          'matchup', home || ' v ' || away, 'n', count(*)) as j
        from usage_events where kind = 'prediction' and home is not null
        group by home, away order by count(*) desc limit 10) m),
    'recent', (select coalesce(jsonb_agg(r.j), '[]'::jsonb) from (
        select jsonb_build_object(
          'ts', extract(epoch from ts)::bigint, 'home', home, 'away', away,
          'context', context, 'neutral', neutral, 'visitor', visitor) as j
        from usage_events where kind = 'prediction'
        order by ts desc limit 100) r)
  );
$$;
