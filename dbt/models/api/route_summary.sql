with days as (
    select
        feed,
        route_id,
        min(service_date) as window_start,
        max(service_date) as window_end,
        count(*) as days,
        sum(observed_trips) as observed_trips,
        sum(scheduled_trips) as scheduled_trips
    from {{ ref('fct_route_day') }}
    where {{ api_window(28) }}
    group by feed, route_id
),

headway as (
    select
        feed,
        route_id,
        count(*) filter (where has_headway_class) as headway_runs,
        count(*) filter (where headway_class = 'on_headway') as on_headway_runs,
        percentile_cont(0.5) within group (order by headway_ratio) filter (where has_headway_class) as median_headway_ratio
    from {{ ref('fct_trip') }}
    where {{ api_window(28) }}
    group by feed, route_id
)

select
    d.feed,
    d.route_id,
    r.route_code,
    r.route_short_name,
    r.route_long_name,
    d.window_start,
    d.window_end,
    d.days::int as days,
    d.observed_trips::int as observed_trips,
    d.scheduled_trips::int as scheduled_trips,
    round(d.observed_trips::numeric / nullif(d.scheduled_trips, 0), 3) as coverage,
    coalesce(h.headway_runs, 0)::int as headway_runs,
    round(100.0 * h.on_headway_runs / nullif(h.headway_runs, 0), 1) as pct_on_headway,
    round(h.median_headway_ratio::numeric, 3) as median_headway_ratio
from days d
left join headway h on h.feed = d.feed and h.route_id = d.route_id
left join {{ ref('dim_route') }} r on r.feed = d.feed and r.route_id = d.route_id
