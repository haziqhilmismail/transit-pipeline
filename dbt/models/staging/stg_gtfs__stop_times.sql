select
    file_sha256,
    {{ field('trip_id') }} as trip_id,
    {{ field('stop_sequence') }}::int as stop_sequence,
    {{ gtfs_secs(field('departure_time')) }} as departure_secs
from ({{ gtfs_rows('stop_times.txt') }}) r
