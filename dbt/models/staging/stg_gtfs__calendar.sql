select
    file_sha256,
    {{ field('service_id') }} as service_id,
    {{ runs_on_dow() }} as runs_on_dow,
    {{ gtfs_date(field('start_date')) }} as start_date,
    {{ gtfs_date(field('end_date')) }} as end_date
from ({{ gtfs_rows('calendar') }}) r
