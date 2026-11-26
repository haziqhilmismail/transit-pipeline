with gaps as (
    select
        feed,
        service_date,
        count(*) as n_gaps,
        sum(gap_s_in_day) as gap_s,
        max(gap_s) as longest_gap_s
    from {{ ref('fct_feed_gap') }}
    where {{ api_window(90) }}
    group by feed, service_date
),

-- feed_uptime_pct is the same on every route row of a day, so max() picks it.
routes as (
    select
        feed,
        service_date,
        count(*) filter (where observed_trips > 0) as routes_observed,
        sum(observed_trips) as observed_trips,
        sum(scheduled_trips) as scheduled_trips,
        sum(headway_runs) as headway_runs,
        sum(on_headway_runs) as on_headway_runs,
        max(feed_uptime_pct) as feed_uptime_pct
    from {{ ref('fct_route_day') }}
    where {{ api_window(90) }}
    group by feed, service_date
)

select
    d.feed,
    d.service_date,
    coalesce(g.n_gaps, 0)::int as n_gaps,
    coalesce(g.gap_s, 0)::int as gap_s,
    g.longest_gap_s::int as longest_gap_s,
    r.routes_observed::int as routes_observed,
    r.observed_trips::int as observed_trips,
    r.scheduled_trips::int as scheduled_trips,
    round(r.observed_trips::numeric / nullif(r.scheduled_trips, 0), 3) as coverage,
    r.headway_runs::int as headway_runs,
    round(100.0 * r.on_headway_runs / nullif(r.headway_runs, 0), 1) as pct_on_headway,
    r.feed_uptime_pct
from {{ ref('int_service_dates') }} d
left join gaps g on g.feed = d.feed and g.service_date = d.service_date
left join routes r on r.feed = d.feed and r.service_date = d.service_date
where d.has_realtime and {{ api_window(90, 'd.service_date') }}
