select
    file_sha256,
    {{ field('trip_id') }} as trip_id,
    {{ gtfs_secs(field('start_time')) }} as start_secs,
    {{ gtfs_secs(field('end_time')) }} as end_secs,
    {{ int_field('headway_secs') }} as headway_secs
from ({{ gtfs_rows('frequencies') }}) r
