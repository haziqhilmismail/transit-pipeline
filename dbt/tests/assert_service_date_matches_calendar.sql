-- The derived service date (MYT, 03:00 cutoff) agrees with the static calendar: for runs whose RT
-- trip_id is in the static version, the trip's service_id is active on that date for over 99% of runs.
with runs as (
    select
        i.feed,
        count(*) as n_runs,
        count(*) filter (where a.service_id is null) as n_inactive
    from {{ ref('int_trip_instances') }} i
    join {{ ref('int_service_dates') }} d on d.feed = i.feed and d.service_date = i.service_date
    left join {{ ref('int_active_services') }} a
        on (a.feed, a.service_date, a.version_id, a.service_id) = (d.feed, d.service_date, d.version_id, i.service_id)
    where i.is_observed_run and i.trip_in_static
    group by i.feed
)

select * from runs where n_inactive > 0.01 * n_runs
