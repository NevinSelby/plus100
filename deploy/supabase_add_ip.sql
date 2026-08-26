-- Plus100 analytics: adds visitor IP capture. Run ONCE in the Supabase SQL editor.
-- Safe to re-run; existing rows simply have a null ip.

alter table usage_events add column if not exists ip text;

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
          'context', context, 'neutral', neutral, 'visitor', visitor,
          'ip', ip) as j
        from usage_events where kind = 'prediction'
        order by ts desc limit 100) r),
    'visitors', (select coalesce(jsonb_agg(v.j order by v.last desc), '[]'::jsonb) from (
        select max(extract(epoch from ts))::bigint as last, jsonb_build_object(
          'visitor', visitor,
          'ip', (array_remove(array_agg(ip order by ts desc), null))[1],
          'n', count(*),
          'last', max(extract(epoch from ts))::bigint) as j
        from usage_events where visitor is not null
        group by visitor order by max(ts) desc limit 20) v)
  );
$$;
