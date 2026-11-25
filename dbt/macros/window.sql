{% macro transform_window() %}
    {% if var('start_date') %}
        {{ return({'start': var('start_date'), 'end': var('end_date') or var('start_date')}) }}
    {% endif %}
    {% if not execute %}
        {{ return(none) }}
    {% endif %}
    {% set sql %}
        select min(c.service_date)::text, max(c.service_date)::text
        from silver.int_complete_dates c
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
    {% set failed = results | selectattr('status', 'in', ['error', 'fail', 'skipped']) | list %}
    {% set windowed = ['positions', 'int_trip_sessions', 'int_scheduled_departures'] %}
    {% set built = results | selectattr('node.name', 'in', windowed) | selectattr('status', 'equalto', 'success') | list %}
    {% if failed or built | length < windowed | length %}
        {{ return('select 1') }}
    {% endif %}
    insert into silver.build_log (feed, service_date, built_at)
    select feed, service_date, '{{ run_started_at }}'::timestamptz
    from silver.int_complete_dates
    {% if var('start_date') %}
    where service_date between '{{ var("start_date") }}'::date and '{{ var("end_date") or var("start_date") }}'::date
    {% endif %}
    on conflict (feed, service_date) do update set built_at = excluded.built_at
{% endmacro %}
