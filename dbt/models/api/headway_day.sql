select feed, service_date, headway_class, count(*)::int as trips
from {{ ref('fct_trip') }}
where {{ api_window(90) }}
group by feed, service_date, headway_class
