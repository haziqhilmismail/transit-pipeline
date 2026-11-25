{# ref() here would need a depends_on hint in every caller, because callers reach it only when executing. #}
{% macro complete_dates_relation() %}
    {{ return(graph.nodes['model.' ~ project_name ~ '.int_complete_dates'].relation_name) }}
{% endmacro %}

{% macro transform_window() %}
    {% if var('start_date') or var('end_date') %}
        {% if not (var('start_date') and var('end_date')) %}
            {{ exceptions.raise_compiler_error("Set both start_date and end_date, or neither.") }}
        {% endif %}
        {{ return({'start': var('start_date'), 'end': var('end_date')}) }}
    {% endif %}
    {% if not execute %}
        {{ return(none) }}
    {% endif %}
    {% set sql %}
        select min(c.service_date)::text, max(c.service_date)::text
        from {{ complete_dates_relation() }} c
        left join silver.build_log b using (feed, service_date)
        where b.built_at is null or c.loaded_at > b.built_at
    {% endset %}
    {% set row = run_query(sql).rows[0] %}
    {{ return({'start': row[0], 'end': row[1]} if row[0] else none) }}
{% endmacro %}

{% macro window_filter(column) -%}
    {%- set w = transform_window() -%}
    {%- if w -%} {{ column }} between '{{ w.start }}'::date and '{{ w.end }}'::date {%- else -%} false {%- endif -%}
{%- endmacro %}

{% macro window_delete() %}
    {% if is_incremental() %}
        delete from {{ this }} where {{ window_filter('service_date') }}
    {% else %}
        select 1
    {% endif %}
{% endmacro %}

{% macro stamp_built_dates(results) %}
    {% if not execute %}{{ return('') }}{% endif %}
    {% set windowed = [] %}
    {% for node in graph.nodes.values() if node.resource_type == 'model' and 'windowed' in node.tags %}
        {% do windowed.append(node.unique_id) %}
    {% endfor %}
    {% set failed = results | selectattr('status', 'in', ['error', 'fail', 'skipped']) | list %}
    {% set built = results | selectattr('node.unique_id', 'in', windowed) | selectattr('status', 'equalto', 'success') | list %}
    {% if failed or built | length < windowed | length %}
        {{ return('select 1') }}
    {% endif %}
    insert into silver.build_log (feed, service_date, built_at)
    select feed, service_date, '{{ run_started_at }}'::timestamptz
    from {{ complete_dates_relation() }}
    where {{ window_filter('service_date') }}
    on conflict (feed, service_date) do update set built_at = excluded.built_at
{% endmacro %}
