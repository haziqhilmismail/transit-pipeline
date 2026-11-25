{% macro gtfs_rows(file) -%}
    select r.file_sha256, r.row_num, r.data
    from {{ source('bronze', 'gtfs_row') }} r
    where r.file_sha256 in (select {{ file }}_sha256 from {{ ref('int_version_files') }})
{%- endmacro %}

{% macro field(name) -%}
    nullif(trim(data ->> '{{ name }}'), '')
{%- endmacro %}

{# A malformed number gives NULL instead of failing the whole build. #}
{% macro int_field(name) -%}
    case when pg_input_is_valid({{ field(name) }}, 'integer') then {{ field(name) }}::int end
{%- endmacro %}

{# runs_on_dow[1] is Sunday. #}
{% macro runs_on_dow() -%}
    array[
        {%- for day in ['sunday', 'monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday'] -%}
        {{ field(day) }} = '1'{{ ', ' if not loop.last }}
        {%- endfor -%}
    ]
{%- endmacro %}

{% macro runs_on(runs_on_dow, day) -%}
    {{ runs_on_dow }}[extract(dow from {{ day }})::int + 1]
{%- endmacro %}
