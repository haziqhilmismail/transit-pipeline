{# Every API table keeps only the last `days` service dates up to the newest day with realtime data. #}
{% macro api_window(days, column='service_date') -%}
    {{ column }} > (select max(service_date) from {{ ref('int_service_dates') }} where has_realtime) - {{ days }}
{%- endmacro %}
