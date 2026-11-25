{{ config(materialized='view') }}

with archives as (
    select feed, archive_date, loaded_at from {{ source('bronze', 'rt_archive') }}
),

realtime as (
    select cur.feed, cur.archive_date as service_date, greatest(cur.loaded_at, prev.loaded_at) as loaded_at
    from archives cur
    join archives prev on prev.feed = cur.feed and prev.archive_date = cur.archive_date - 1
)

select feed, service_date, loaded_at from realtime
union all
select c.feed, d.service_date, d.loaded_at
from {{ ref('feed_config') }} c
cross join (select service_date, max(loaded_at) as loaded_at from realtime group by 1) d
where not c.has_realtime
