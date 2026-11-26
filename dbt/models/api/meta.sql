select
    greatest(min(service_date), max(service_date) - {{ var('api_days_long') }} + 1) as data_from,
    max(service_date) as data_through,
    now() as built_at,
    array_agg(distinct feed order by feed) as feeds,
    'Data: data.gov.my GTFS, CC BY 4.0'::text as attribution,
    'https://creativecommons.org/licenses/by/4.0/'::text as licence_url
from {{ ref('int_service_dates') }}
where has_realtime
