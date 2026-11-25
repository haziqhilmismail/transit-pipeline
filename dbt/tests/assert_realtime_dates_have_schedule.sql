-- Every complete realtime service date has scheduled departures. Catches a wrong static version pick.
select d.feed, d.service_date, d.version_id
from {{ ref('int_service_dates') }} d
join {{ ref('feed_config') }} c on c.feed = d.feed and c.has_realtime
where not exists (
    select 1 from {{ ref('int_scheduled_departures') }} s
    where s.feed = d.feed and s.service_date = d.service_date
)
