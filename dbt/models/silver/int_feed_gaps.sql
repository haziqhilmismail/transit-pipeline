with snapshots as (
    select feed, fetched_at from {{ ref('stg_rt__snapshots') }} where n_entities > 0
),

lagged as (
    select feed, lag(fetched_at) over (partition by feed order by fetched_at) as gap_start_at, fetched_at as gap_end_at
    from snapshots
)

select feed, gap_start_at, gap_end_at, extract(epoch from gap_end_at - gap_start_at)::int as gap_s
from lagged
where gap_end_at - gap_start_at > make_interval(secs => {{ var('feed_gap_s') }})
