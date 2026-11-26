{{ config(indexes=[{'columns': ['feed', 'route_id', 'service_date']}]) }}

select feed, route_id, service_date, departure_at
from {{ ref('int_scheduled_departures') }}
where {{ api_window(var('api_days_short')) }}
