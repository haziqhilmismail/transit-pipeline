select
    file_sha256,
    {{ field('service_id') }} as service_id,
    array[
        {% for day in ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'] -%}
        {{ field(day) }} = '1'{{ ',' if not loop.last }}
        {% endfor -%}
    ] as runs_on_dow,
    {{ gtfs_date(field('start_date')) }} as start_date,
    {{ gtfs_date(field('end_date')) }} as end_date
from ({{ gtfs_rows('calendar.txt') }}) r
