-- Observed runs active in each MYT clock hour (a run counts in every hour it spans).
select
    r.feed,
    r.route_id,
    r.service_date,
    hour_start_myt,
    count(distinct r.vehicle_id) as active_vehicles,
    count(*) as active_trips
from {{ ref('int_trip_instances') }} r
cross join generate_series(
    date_trunc('hour', r.departed_at at time zone 'Asia/Kuala_Lumpur'),
    r.last_seen_at at time zone 'Asia/Kuala_Lumpur',
    interval '1 hour'
) hour_start_myt
where r.is_observed_run
group by r.feed, r.route_id, r.service_date, hour_start_myt
