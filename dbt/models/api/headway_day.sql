select feed, service_date, headway_class, count(*)::int as trips
from {{ ref('fct_trip') }}
where {{ api_window(var('api_days_long')) }}
group by feed, service_date, headway_class
