{{ config(contract={'enforced': true}) }}

-- One observed run. Headway is measured within (feed, service date, route, pattern).
with runs as (
    select * from {{ ref('int_trip_instances') }} where is_observed_run
),

coverage as (
    select
        r.feed,
        r.service_date,
        r.route_id,
        r.pattern_id,
        count(*) as observed_runs,
        (
            select count(*) from {{ ref('int_scheduled_departures') }} s
            where (s.feed, s.service_date, s.route_id, s.pattern_id) = (r.feed, r.service_date, r.route_id, r.pattern_id)
        ) as scheduled_departures
    from runs r
    group by r.feed, r.service_date, r.route_id, r.pattern_id
),

headways as (
    select
        r.*,
        extract(epoch from r.departed_at - lag(r.departed_at) over (
            partition by r.feed, r.service_date, r.route_id, r.pattern_id order by r.departed_at
        ))::int as observed_headway_s
    from runs r
),

matched as (
    select h.*, slot.departure_at as matched_departure_at, slot.sched_headway_s
    from headways h
    left join lateral (
        -- nearest scheduled slot, not "at or before": a run that leaves a minute early still matches its slot
        select s.departure_at, s.sched_headway_s
        from {{ ref('int_scheduled_departures') }} s
        where (s.feed, s.service_date, s.route_id, s.pattern_id) = (h.feed, h.service_date, h.route_id, h.pattern_id)
            and s.departure_at between h.departed_at - make_interval(secs => {{ var('headway_match_tolerance_s') }})
                and h.departed_at + make_interval(secs => {{ var('headway_match_tolerance_s') }})
        order by abs(extract(epoch from s.departure_at - h.departed_at)), s.departure_at
        limit 1
    ) slot on true
),

scored as (
    select
        m.*,
        c.scheduled_departures,
        round(c.observed_runs::numeric / nullif(c.scheduled_departures, 0), 3) as coverage,
        round(m.observed_headway_s::numeric / nullif(m.sched_headway_s, 0), 3) as headway_ratio
    from matched m
    join coverage c using (feed, service_date, route_id, pattern_id)
)

select
    trip_instance_id::text as trip_instance_id,
    feed::text as feed,
    service_date::date as service_date,
    route_id::text as route_id,
    pattern_id::text as pattern_id,
    vehicle_id::text as vehicle_id,
    trip_id::text as trip_id,
    departed_at::timestamptz as departed_at,
    last_seen_at::timestamptz as last_seen_at,
    duration_s::int as duration_s,
    n_pings::int as n_pings,
    is_departure_observed::boolean as is_departure_observed,
    touches_outage::boolean as touches_outage,
    observed_headway_s::int as observed_headway_s,
    matched_departure_at::timestamptz as matched_departure_at,
    sched_headway_s::int as sched_headway_s,
    headway_ratio::numeric as headway_ratio,
    scheduled_departures::int as pattern_scheduled_departures,
    coverage::numeric as pattern_coverage,
    (case
        when matched_departure_at is null then 'unscheduled'
        when coalesce(coverage, 0) < {{ var('coverage_threshold') }} then 'low_coverage'
        when headway_ratio is null then 'first_of_day'
        when headway_ratio < 0.5 then 'bunched'
        when headway_ratio <= 1.5 then 'on_headway'
        else 'gapped'
    end)::text as headway_class
from scored
