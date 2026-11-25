{{ config(materialized='view') }}

select
    file_sha256,
    {{ field('trip_id') }} as trip_id,
    {{ int_field('stop_sequence') }} as stop_sequence,
    {{ gtfs_secs(field('departure_time')) }} as departure_secs
from ({{ gtfs_rows('stop_times') }}) r
