with weekly as (
    select c.feed, c.service_date, c.version_id, cal.service_id
    from {{ ref('int_static_candidates') }} c
    join {{ ref('int_version_files') }} f on f.feed = c.feed and f.version_id = c.version_id
    join {{ ref('stg_gtfs__calendar') }} cal on cal.file_sha256 = f.calendar_sha256
    where c.service_date between cal.start_date and cal.end_date
        and {{ runs_on('cal.runs_on_dow', 'c.service_date') }}
),

exceptions as (
    select c.feed, c.service_date, c.version_id, cd.service_id, cd.exception_type
    from {{ ref('int_static_candidates') }} c
    join {{ ref('int_version_files') }} f on f.feed = c.feed and f.version_id = c.version_id
    join {{ ref('stg_gtfs__calendar_dates') }} cd on cd.file_sha256 = f.calendar_dates_sha256 and cd.service_date = c.service_date
)

select w.feed, w.service_date, w.version_id, w.service_id
from weekly w
where not exists (
    select 1 from exceptions e
    where e.exception_type = 2
        and (e.feed, e.service_date, e.version_id, e.service_id) = (w.feed, w.service_date, w.version_id, w.service_id)
)
union
select feed, service_date, version_id, service_id from exceptions where exception_type = 1
