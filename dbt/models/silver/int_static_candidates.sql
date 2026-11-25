{{ config(materialized='ephemeral') }}

-- Every static version a service date may use: fetched before the service day ended.
select d.feed, d.service_date, v.version_id, v.fetched_at
from {{ ref('int_complete_dates') }} d
join {{ source('bronze', 'static_version') }} v
    on v.feed = d.feed
    and v.fetched_at < {{ service_day_start('d.service_date + 1') }}
