with routes as (
    select
        feed,
        service_date,
        count(*) filter (where observed_trips > 0) as routes_observed,
        sum(observed_trips) as observed_trips,
        sum(scheduled_trips) as scheduled_trips,
        max(feed_uptime_pct) as feed_uptime_pct
    from {{ ref('fct_route_day') }}
    where {{ api_window(90) }}
    group by feed, service_date
),

headway as (
    select
        feed,
        service_date,
        count(*) filter (where has_headway_class) as headway_runs,
        count(*) filter (where headway_class = 'on_headway') as on_headway_runs
    from {{ ref('fct_trip') }}
    where {{ api_window(90) }}
    group by feed, service_date
)

select
    r.feed,
    r.service_date,
    r.routes_observed::int as routes_observed,
    r.observed_trips::int as observed_trips,
    r.scheduled_trips::int as scheduled_trips,
    round(r.observed_trips::numeric / nullif(r.scheduled_trips, 0), 3) as coverage,
    coalesce(h.headway_runs, 0)::int as headway_runs,
    round(100.0 * h.on_headway_runs / nullif(h.headway_runs, 0), 1) as pct_on_headway,
    r.feed_uptime_pct
from routes r
left join headway h on h.feed = r.feed and h.service_date = r.service_date
