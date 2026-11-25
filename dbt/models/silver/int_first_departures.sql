{{ config(materialized='incremental', incremental_strategy='append') }}

-- First-stop departure per trip, once per distinct stop_times.txt content (versions share most of them).
select distinct on (file_sha256, trip_id)
    file_sha256,
    trip_id,
    departure_secs
from {{ ref('stg_gtfs__stop_times') }}
{% if is_incremental() %}
where file_sha256 not in (select distinct file_sha256 from {{ this }})
{% endif %}
order by file_sha256, trip_id, stop_sequence
