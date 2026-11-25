{{ config(materialized='view') }}

select
    feed,
    archive_date,
    fetched_at,
    to_timestamp(header_ts) as header_at,
    n_entities,
    {{ service_date('fetched_at') }} as service_date
from {{ source('bronze', 'rt_snapshot') }}
