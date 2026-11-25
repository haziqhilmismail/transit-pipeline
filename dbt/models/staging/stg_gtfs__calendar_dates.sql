select
    file_sha256,
    {{ field('service_id') }} as service_id,
    {{ gtfs_date(field('date')) }} as service_date,
    {{ field('exception_type') }}::int as exception_type
from ({{ gtfs_rows('calendar_dates.txt') }}) r
