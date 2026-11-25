-- One row per complete (feed, service date) with the static version it is measured against:
-- the latest version fetched before the day ended that runs at least one service that day.
-- The mrtfeeder calendar starts the day after each fetch, so "latest" alone can pick an empty one.
with candidates as (
    select
        c.feed,
        c.service_date,
        c.version_id,
        c.fetched_at,
        exists (
            select 1 from {{ ref('int_active_services') }} a
            where (a.feed, a.service_date, a.version_id) = (c.feed, c.service_date, c.version_id)
        ) as has_service
    from {{ ref('int_static_candidates') }} c
)

select distinct on (d.feed, d.service_date)
    d.feed,
    d.service_date,
    d.loaded_at,
    c.version_id,
    c.fetched_at as version_fetched_at,
    coalesce(c.has_service, false) as has_service
from {{ ref('int_complete_dates') }} d
left join candidates c on c.feed = d.feed and c.service_date = d.service_date
order by d.feed, d.service_date, c.has_service desc nulls last, c.fetched_at desc nulls last
