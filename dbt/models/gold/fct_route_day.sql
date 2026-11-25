{{ config(contract={'enforced': true}) }}

with days as (
    select feed, service_date
    from {{ ref('int_service_dates') }}
    where rt_usable
),

scheduled as (
    select feed, service_date, route_id, count(*) as scheduled_trips
    from {{ ref('int_scheduled_departures') }}
    group by feed, service_date, route_id
),

observed as (
    select
        feed,
        service_date,
        route_id,
        count(*) as observed_trips,
        count(*) filter (where has_headway_class) as headway_runs,
        count(*) filter (where headway_class = 'on_headway') as on_headway_runs,
        percentile_cont(0.5) within group (order by headway_ratio)
            filter (where has_headway_class) as median_headway_ratio
    from {{ ref('fct_trip') }}
    group by feed, service_date, route_id
),

routes as (
    select feed, service_date, route_id from scheduled
    union
    select feed, service_date, route_id from observed
),

-- Uptime over the scheduled service span only: overnight the feeds send empty snapshots, which
-- count as gaps but are not outages.
service_span as (
    select feed, service_date, min(departure_at) as first_at, max(departure_at) as last_at
    from {{ ref('int_scheduled_departures') }}
    group by feed, service_date
),

uptime as (
    select
        d.feed,
        d.service_date,
        round((100 * (1 - coalesce(sum(
            extract(epoch from least(g.gap_end_at, sp.last_at) - greatest(g.gap_start_at, sp.first_at))
        ) filter (where g.feed is not null), 0) / nullif(extract(epoch from sp.last_at - sp.first_at), 0)))::numeric, 1) as feed_uptime_pct
    from days d
    join service_span sp on sp.feed = d.feed and sp.service_date = d.service_date
    left join {{ ref('fct_feed_gap') }} g
        on g.feed = d.feed and g.service_date = d.service_date
        and g.gap_start_at < sp.last_at and g.gap_end_at > sp.first_at
    group by d.feed, d.service_date, sp.first_at, sp.last_at
)

select
    r.feed::text as feed,
    r.service_date::date as service_date,
    r.route_id::text as route_id,
    coalesce(o.observed_trips, 0)::int as observed_trips,
    coalesce(s.scheduled_trips, 0)::int as scheduled_trips,
    round(coalesce(o.observed_trips, 0)::numeric / nullif(s.scheduled_trips, 0), 3)::numeric as coverage,
    coalesce(o.headway_runs, 0)::int as headway_runs,
    round(100.0 * o.on_headway_runs / nullif(o.headway_runs, 0), 1)::numeric as pct_on_headway,
    round(o.median_headway_ratio::numeric, 3)::numeric as median_headway_ratio,
    u.feed_uptime_pct::numeric as feed_uptime_pct
from routes r
join days d on d.feed = r.feed and d.service_date = r.service_date
join uptime u on u.feed = r.feed and u.service_date = r.service_date
left join scheduled s on s.feed = r.feed and s.service_date = r.service_date and s.route_id = r.route_id
left join observed o on o.feed = r.feed and o.service_date = r.service_date and o.route_id = r.route_id
