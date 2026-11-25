{# MYT service day: 03:00 to 03:00 next day, so a late-night run stays on the day it started. #}
{% macro service_date(ts) -%}
    ((({{ ts }}) at time zone 'Asia/Kuala_Lumpur') - interval '3 hours')::date
{%- endmacro %}

{% macro service_day_start(service_date) -%}
    (({{ service_date }})::timestamp + interval '3 hours') at time zone 'Asia/Kuala_Lumpur'
{%- endmacro %}

{# GTFS HH:MM:SS; hours can pass 24. #}
{% macro gtfs_secs(expr) -%}
    (split_part({{ expr }}, ':', 1)::int * 3600 + split_part({{ expr }}, ':', 2)::int * 60 + split_part({{ expr }}, ':', 3)::int)
{%- endmacro %}

{% macro gtfs_date(expr) -%}
    to_date({{ expr }}, 'YYYYMMDD')
{%- endmacro %}
