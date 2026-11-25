select
    file_sha256,
    {{ field('route_id') }} as route_id,
    {{ field('route_short_name') }} as route_short_name,
    {{ field('route_long_name') }} as route_long_name,
    coalesce({{ field('route_short_name') }}, {{ field('route_long_name') }}, {{ field('route_id') }}) as route_code,
    {{ field('route_type') }}::int as route_type
from ({{ gtfs_rows('routes.txt') }}) r
