{{ config(
    post_hook="create index if not exists positions_vehicle on {{ this }} (feed, service_date, vehicle_id, vehicle_at)",
) }}

with days as (
    select feed, service_date
    from {{ ref('int_service_dates') }}
    where rt_usable
    {% if is_incremental() %}and {{ window_filter('service_date') }}{% endif %}
),

scoped as (
    select p.*, {{ service_date('p.fetched_at') }} as service_date
    from {{ source('bronze', 'rt_vehicle_position') }} p
    where p.vehicle_id is not null
        and p.vehicle_ts is not null
        and p.archive_date between (select min(service_date) - 1 from days) and (select max(service_date) from days)
),

ranked as (
    select
        s.*,
        row_number() over w as copy_num,
        count(*) over (partition by s.feed, s.service_date, s.vehicle_id, s.vehicle_ts) as n_snapshots
    from scoped s
    join days d on d.feed = s.feed and d.service_date = s.service_date
    window w as (partition by s.feed, s.service_date, s.vehicle_id, s.vehicle_ts order by s.fetched_at, s.entity_id)
)

select
    feed,
    service_date,
    vehicle_id,
    to_timestamp(vehicle_ts) as vehicle_at,
    fetched_at as first_fetched_at,
    n_snapshots,
    trip_id,
    route_id as rt_route_id,
    start_date as rt_start_date,
    start_time as rt_start_time,
    direction_id as rt_direction_id,
    latitude,
    longitude,
    abs(vehicle_ts - extract(epoch from fetched_at)) <= {{ var('valid_ts_skew_s') }} as is_valid_ts,
    {% set bbox = var('valid_bbox') -%}
    coalesce(
        latitude between {{ bbox.min_lat }} and {{ bbox.max_lat }} and longitude between {{ bbox.min_lon }} and {{ bbox.max_lon }},
        false
    ) as is_valid_position
from ranked
where copy_num = 1
