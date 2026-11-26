-- No vehicle count: one bus can serve two routes in an hour, so a sum over routes would count it twice.
select
    feed,
    service_date,
    hour_start_myt,
    count(*)::int as routes_active,
    sum(active_trips)::int as active_trips
from {{ ref('fct_route_hour_activity') }}
where {{ api_window(35) }}
group by feed, service_date, hour_start_myt
