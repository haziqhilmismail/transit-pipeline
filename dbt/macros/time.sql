{# The only place that names the time zone. Converts timestamptz to MYT wall clock, and wall clock back to timestamptz. #}
{% macro at_myt(expr) -%}
    (({{ expr }}) at time zone 'Asia/Kuala_Lumpur')
{%- endmacro %}

{% macro service_date(ts) -%}
    ({{ at_myt(ts) }} - make_interval(hours => {{ var('service_day_cutoff_h') }}))::date
{%- endmacro %}

{% macro service_day_start(service_date) -%}
    {{ at_myt('(' ~ service_date ~ ')::timestamp + make_interval(hours => ' ~ var('service_day_cutoff_h') ~ ')') }}
{%- endmacro %}

{# GTFS times count from the local midnight of the service day, not from the cutoff. #}
{% macro gtfs_departure_at(service_date, secs) -%}
    {{ at_myt('(' ~ service_date ~ ')::timestamp + make_interval(secs => ' ~ secs ~ ')') }}
{%- endmacro %}

{# GTFS H:MM:SS; hours can pass 24. A malformed value gives NULL. #}
{% macro gtfs_secs(expr) -%}
    case when {{ expr }} ~ '^\d{1,3}:\d{1,2}:\d{1,2}$' then
        split_part({{ expr }}, ':', 1)::int * 3600 + split_part({{ expr }}, ':', 2)::int * 60 + split_part({{ expr }}, ':', 3)::int
    end
{%- endmacro %}

{% macro gtfs_date(expr) -%}
    case when {{ expr }} ~ '^\d{8}$' and pg_input_is_valid({{ expr }}, 'date') then to_date({{ expr }}, 'YYYYMMDD') end
{%- endmacro %}
