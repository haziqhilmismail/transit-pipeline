{{ config(indexes=[{'columns': ['feed', 'route_id', 'service_date']}]) }}

-- No vehicle_id or trip_id: trip_no (order of departure in the day) is the only per-trip key.
select
    feed,
    route_id,
    service_date,
    row_number() over (partition by feed, route_id, service_date order by departed_at, last_seen_at, trip_instance_id)::int as trip_no,
    departed_at,
    last_seen_at,
    duration_s,
    is_departure_observed,
    touches_outage,
    observed_headway_s,
    sched_headway_s,
    headway_ratio,
    headway_class
from {{ ref('fct_trip') }}
where {{ api_window(var('api_days_short')) }}
