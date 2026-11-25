select
    file_sha256,
    {{ field('trip_id') }} as trip_id,
    {{ field('route_id') }} as route_id,
    {{ field('service_id') }} as service_id,
    {{ field('shape_id') }} as shape_id,
    {{ int_field('direction_id') }} as direction_id,
    -- shape_id is the usable direction: most kl trips carry direction_id 0 and most routes are one-shape loops.
    coalesce({{ field('shape_id') }}, {{ field('direction_id') }}, 'none') as pattern_id
from ({{ gtfs_rows('trips') }}) r
