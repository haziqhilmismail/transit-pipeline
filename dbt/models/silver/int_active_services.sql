-- GTFS service_ids active on each service date, for every candidate static version.
with files as (
    select feed, version_id, file_name, file_sha256 from {{ source('bronze', 'static_version_file') }}
),

weekly as (
    select c.feed, c.service_date, c.version_id, cal.service_id
    from {{ ref('int_static_candidates') }} c
    join files f on f.feed = c.feed and f.version_id = c.version_id and f.file_name = 'calendar.txt'
    join {{ ref('stg_gtfs__calendar') }} cal on cal.file_sha256 = f.file_sha256
    where c.service_date between cal.start_date and cal.end_date
        and cal.runs_on_dow[extract(dow from c.service_date)::int + 1]
),

exceptions as (
    select c.feed, c.service_date, c.version_id, cd.service_id, cd.exception_type
    from {{ ref('int_static_candidates') }} c
    join files f on f.feed = c.feed and f.version_id = c.version_id and f.file_name = 'calendar_dates.txt'
    join {{ ref('stg_gtfs__calendar_dates') }} cd on cd.file_sha256 = f.file_sha256 and cd.service_date = c.service_date
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
