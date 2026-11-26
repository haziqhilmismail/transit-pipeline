with gaps as (
    select
        feed,
        service_date,
        count(*) as n_gaps,
        sum(gap_s_in_day) as gap_s,
        max(gap_s) as longest_gap_s
    from {{ ref('fct_feed_gap') }}
    where {{ api_window(90) }}
    group by feed, service_date
),

uptime as (
    select distinct feed, service_date, feed_uptime_pct
    from {{ ref('fct_route_day') }}
    where {{ api_window(90) }}
)

select
    d.feed,
    d.service_date,
    coalesce(g.n_gaps, 0)::int as n_gaps,
    coalesce(g.gap_s, 0)::int as gap_s,
    g.longest_gap_s::int as longest_gap_s,
    u.feed_uptime_pct
from {{ ref('int_service_dates') }} d
left join gaps g on g.feed = d.feed and g.service_date = d.service_date
left join uptime u on u.feed = d.feed and u.service_date = d.service_date
where d.has_realtime and {{ api_window(90, 'd.service_date') }}
