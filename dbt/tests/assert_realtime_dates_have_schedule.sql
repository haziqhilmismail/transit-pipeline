select d.feed, d.service_date, d.version_id
from {{ ref('int_service_dates') }} d
where d.has_realtime and not exists (
    select 1 from {{ ref('int_scheduled_departures') }} s
    where s.feed = d.feed and s.service_date = d.service_date
)
