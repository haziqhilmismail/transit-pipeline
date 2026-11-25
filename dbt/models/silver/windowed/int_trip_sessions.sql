with pings as (
    select feed, service_date, vehicle_id, vehicle_at, trip_id, rt_route_id, rt_start_time, rt_direction_id, latitude, longitude
    from {{ ref('positions') }}
    where is_valid_ts and is_valid_position
    {% if is_incremental() %}and {{ window_filter('service_date') }}{% endif %}
),

stepped as (
    select
        p.*,
        lag(vehicle_at) over w as prev_at,
        lag(trip_id) over w as prev_trip_id,
        lag(rt_start_time) over w as prev_start_time
    from pings p
    window w as (partition by feed, service_date, vehicle_id order by vehicle_at)
),

gapped as (
    select
        s.*,
        o.outage_s,
        extract(epoch from s.vehicle_at - s.prev_at) - coalesce(o.outage_s, 0) as silent_while_feed_up_s
    from stepped s
    left join lateral (
        select sum(extract(epoch from least(g.gap_end_at, s.vehicle_at) - greatest(g.gap_start_at, s.prev_at))) as outage_s
        from {{ ref('int_feed_gaps') }} g
        where g.feed = s.feed and g.gap_start_at < s.vehicle_at and g.gap_end_at > s.prev_at
    ) o on s.vehicle_at - s.prev_at > make_interval(secs => {{ var('feed_gap_s') }})
),

flagged as (
    select
        *,
        prev_at is null
            or trip_id is distinct from prev_trip_id
            or silent_while_feed_up_s > {{ var('session_gap_s') }}
            -- On kl, start_time is stamped when a run is first sighted, so it jitters by a minute
            -- within a run and is re-stamped when a frequency trip_id starts its next run.
            or coalesce({{ gtfs_secs('rt_start_time') }} - {{ gtfs_secs('prev_start_time') }} > {{ var('start_time_jump_s') }}, false)
            as starts_run
    from gapped
),

numbered as (
    select
        *,
        sum(starts_run::int) over (partition by feed, service_date, vehicle_id order by vehicle_at) as run_num,
        not starts_run and coalesce(outage_s, 0) > 0 as bridged_outage
    from flagged
),

runs as (
    select
        feed,
        service_date,
        vehicle_id,
        run_num,
        min(trip_id) as trip_id,
        min(vehicle_at) as departed_at,
        max(vehicle_at) as last_seen_at,
        count(*) as n_pings,
        (array_agg(rt_route_id order by vehicle_at))[1] as rt_route_id,
        (array_agg(rt_start_time order by vehicle_at))[1] as rt_start_time,
        (array_agg(rt_direction_id order by vehicle_at))[1] as rt_direction_id,
        bool_or(bridged_outage) as touches_outage,
        sqrt(
            power((max(latitude) - min(latitude)) * 111320, 2)
            + power((max(longitude) - min(longitude)) * 111320 * cos(radians(avg(latitude))), 2)
        ) as extent_m
    from numbered
    group by feed, service_date, vehicle_id, run_num
)

select
    md5(concat_ws('|', r.feed, r.service_date, r.vehicle_id, r.trip_id, r.departed_at)) as trip_instance_id,
    r.feed,
    r.service_date,
    r.vehicle_id,
    r.trip_id,
    r.rt_route_id,
    r.rt_start_time,
    r.rt_direction_id,
    r.departed_at,
    r.last_seen_at,
    extract(epoch from r.last_seen_at - r.departed_at)::int as duration_s,
    r.n_pings,
    round(r.extent_m::numeric, 1) as extent_m,
    r.touches_outage,
    r.n_pings >= {{ var('min_pings') }} and r.extent_m >= {{ var('min_extent_m') }} as is_observed_run,
    r.departed_at >= {{ service_day_start('r.service_date') }} + make_interval(secs => {{ var('session_gap_s') }})
        and not exists (
            select 1 from {{ ref('int_feed_gaps') }} g
            where g.feed = r.feed
                and g.gap_start_at < r.departed_at
                and g.gap_end_at > r.departed_at - make_interval(secs => {{ var('session_gap_s') }})
        ) as is_departure_observed
from runs r
