{% macro gtfs_rows(file_name) -%}
    select r.file_sha256, r.row_num, r.data
    from {{ source('bronze', 'gtfs_row') }} r
    where r.file_sha256 in (
        select file_sha256 from {{ source('bronze', 'static_version_file') }} where file_name = '{{ file_name }}'
    )
{%- endmacro %}

{% macro field(name) -%}
    nullif(trim(data ->> '{{ name }}'), '')
{%- endmacro %}
