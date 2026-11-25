select
    feed,
    version_id,
    {%- for file in ['routes', 'trips', 'stop_times', 'calendar', 'calendar_dates', 'frequencies'] %}
    max(file_sha256) filter (where file_name = '{{ file }}.txt') as {{ file }}_sha256{{ ',' if not loop.last }}
    {%- endfor %}
from {{ source('bronze', 'static_version_file') }}
group by feed, version_id
