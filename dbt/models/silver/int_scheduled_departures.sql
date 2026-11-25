{{ config(
    materialized='incremental',
    incremental_strategy='append',
    pre_hook="{{ window_delete() }}",
    post_hook="create index if not exists scheduled_departures_slot on {{ this }} (feed, service_date, route_id, pattern_id, departure_at)",
) }}

-- One grain for both schedule kinds: frequency windows expanded into departures, and the
-- first-stop departure of each timetable trip. A trip with frequency rows uses them.
with days as (
    select feed, service_date, version_id
    from {{ ref('int_service_dates') }}
    {% if is_incremental() %}where {{ window_filter('service_date') }}{% endif %}
),

version_files as (
    select d.feed, d.service_date, f.file_name, f.file_sha256
    from days d
    join {{ source('bronze', 'static_version_file') }} f on f.feed = d.feed and f.version_id = d.version_id
),

trips as (
    select d.feed, d.service_date, t.trip_id, t.route_id, t.pattern_id
    from days d
    join {{ ref('int_active_services') }} a
        on (a.feed, a.service_date, a.version_id) = (d.feed, d.service_date, d.version_id)
    join version_files vf on vf.feed = d.feed and vf.service_date = d.service_date and vf.file_name = 'trips.txt'
    join {{ ref('stg_gtfs__trips') }} t on t.file_sha256 = vf.file_sha256 and t.service_id = a.service_id
),

frequency_departures as (
    select t.feed, t.service_date, t.route_id, t.pattern_id, t.trip_id, slot as departure_secs
    from trips t
    join version_files vf on vf.feed = t.feed and vf.service_date = t.service_date and vf.file_name = 'frequencies.txt'
    join {{ ref('stg_gtfs__frequencies') }} fr on fr.file_sha256 = vf.file_sha256 and fr.trip_id = t.trip_id
    cross join generate_series(fr.start_secs, fr.end_secs - 1, fr.headway_secs) slot
),

timetable_departures as (
    select t.feed, t.service_date, t.route_id, t.pattern_id, t.trip_id, fd.departure_secs
    from trips t
    join version_files vf on vf.feed = t.feed and vf.service_date = t.service_date and vf.file_name = 'stop_times.txt'
    join {{ ref('int_first_departures') }} fd on fd.file_sha256 = vf.file_sha256 and fd.trip_id = t.trip_id
    where not exists (
        select 1 from frequency_departures x
        where (x.feed, x.service_date, x.trip_id) = (t.feed, t.service_date, t.trip_id)
    )
),

departures as (
    select * from frequency_departures
    union all
    select * from timetable_departures
)

select
    feed,
    service_date,
    route_id,
    pattern_id,
    trip_id,
    departure_secs,
    -- GTFS times count from the service day's local midnight
    (service_date::timestamp + make_interval(secs => departure_secs)) at time zone 'Asia/Kuala_Lumpur' as departure_at,
    departure_secs - lag(departure_secs) over (
        partition by feed, service_date, route_id, pattern_id order by departure_secs, trip_id
    ) as sched_headway_s
from departures
