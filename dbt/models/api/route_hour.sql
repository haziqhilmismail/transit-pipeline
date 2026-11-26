{{ config(indexes=[{'columns': ['feed', 'route_id', 'service_date']}, {'columns': ['feed', 'service_date']}]) }}

select
    h.feed,
    h.route_id,
    r.route_code,
    h.service_date,
    h.hour_start_myt,
    h.active_vehicles::int as active_vehicles,
    h.active_trips::int as active_trips
from {{ ref('fct_route_hour_activity') }} h
left join {{ ref('dim_route') }} r on r.feed = h.feed and r.route_id = h.route_id
where {{ api_window(35, 'h.service_date') }}
