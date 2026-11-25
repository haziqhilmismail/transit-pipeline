-- Feed gaps (no non-empty snapshot for more than feed_gap_s), split at service-day bounds.
with days as (
    select d.feed, d.service_date, {{ service_day_start('d.service_date') }} as day_start
    from {{ ref('int_service_dates') }} d
    join {{ ref('feed_config') }} c on c.feed = d.feed and c.has_realtime
)

select
    d.feed,
    d.service_date,
    g.gap_start_at,
    g.gap_end_at,
    g.gap_s,
    extract(epoch from least(g.gap_end_at, d.day_start + interval '1 day') - greatest(g.gap_start_at, d.day_start))::int
        as gap_s_in_day
from days d
join {{ ref('int_feed_gaps') }} g
    on g.feed = d.feed and g.gap_start_at < d.day_start + interval '1 day' and g.gap_end_at > d.day_start
