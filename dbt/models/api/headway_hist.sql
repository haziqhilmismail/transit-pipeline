-- Bins of 0.25; ratios of 3 and more share the last bin.
select
    feed,
    least(floor(headway_ratio * 4) / 4, 3)::numeric as ratio_from,
    count(*)::int as trips
from {{ ref('fct_trip') }}
where has_headway_class and {{ api_window(28) }}
group by feed, ratio_from
