select
    file_sha256,
    {{ field('service_id') }} as service_id,
    {{ gtfs_date(field('date')) }} as service_date,
    {{ int_field('exception_type') }} as exception_type
from ({{ gtfs_rows('calendar_dates') }}) r
