-- Every route in any loaded static version (latest attributes win), plus routes seen only in realtime.
with static_routes as (
    select distinct on (v.feed, r.route_id)
        v.feed,
        r.route_id,
        r.route_code,
        r.route_short_name,
        r.route_long_name,
        r.route_type
    from {{ source('bronze', 'static_version') }} v
    join {{ source('bronze', 'static_version_file') }} f
        on f.feed = v.feed and f.version_id = v.version_id and f.file_name = 'routes.txt'
    join {{ ref('stg_gtfs__routes') }} r on r.file_sha256 = f.file_sha256
    order by v.feed, r.route_id, v.fetched_at desc
)

select feed, route_id, route_code, route_short_name, route_long_name, route_type, true as in_static
from static_routes
union all
select distinct i.feed, i.route_id, i.route_id, null, null, null::int, false
from {{ ref('int_trip_instances') }} i
where not exists (select 1 from static_routes s where s.feed = i.feed and s.route_id = i.route_id)
