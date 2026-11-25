with version_files as (
    select d.feed, d.service_date, f.file_name, f.file_sha256
    from {{ ref('int_service_dates') }} d
    join {{ source('bronze', 'static_version_file') }} f on f.feed = d.feed and f.version_id = d.version_id
),

static_trips as (
    select vf.feed, vf.service_date, t.trip_id, t.route_id, t.pattern_id, t.service_id
    from version_files vf
    join {{ ref('stg_gtfs__trips') }} t on t.file_sha256 = vf.file_sha256
    where vf.file_name = 'trips.txt'
),

static_routes as (
    select distinct on (vf.feed, vf.service_date, match_key)
        vf.feed,
        vf.service_date,
        r.route_id,
        case c.rt_route_match when 'route_id' then r.route_id when 'route_long_name' then r.route_long_name end as match_key
    from version_files vf
    join {{ ref('stg_gtfs__routes') }} r on r.file_sha256 = vf.file_sha256
    join {{ ref('feed_config') }} c on c.feed = vf.feed
    where vf.file_name = 'routes.txt' and c.rt_route_match is not null
    order by vf.feed, vf.service_date, match_key, r.route_id
)

select
    s.*,
    coalesce(st.route_id, sr.route_id, s.rt_route_id, 'unknown') as route_id,
    coalesce(st.pattern_id, s.rt_direction_id::text, 'none') as pattern_id,
    st.service_id,
    st.trip_id is not null as trip_in_static,
    st.route_id is not null or sr.route_id is not null as route_in_static
from {{ ref('int_trip_sessions') }} s
left join static_trips st on st.feed = s.feed and st.service_date = s.service_date and st.trip_id = s.trip_id
left join static_routes sr on sr.feed = s.feed and sr.service_date = s.service_date and sr.match_key = s.rt_route_id
