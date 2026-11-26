{{ config(indexes=[{'columns': ['feed', 'route_id', 'service_date']}, {'columns': ['service_date']}]) }}

select
    d.feed,
    d.service_date,
    d.route_id,
    r.route_code,
    r.route_short_name,
    r.route_long_name,
    d.observed_trips,
    d.scheduled_trips,
    d.coverage,
    d.headway_runs,
    d.pct_on_headway,
    d.median_headway_ratio,
    d.feed_uptime_pct
from {{ ref('fct_route_day') }} d
left join {{ ref('dim_route') }} r on r.feed = d.feed and r.route_id = d.route_id
where {{ api_window(90, 'd.service_date') }}
