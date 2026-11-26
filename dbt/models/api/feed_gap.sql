select feed, service_date, gap_start_at, gap_end_at, gap_s, gap_s_in_day
from {{ ref('fct_feed_gap') }}
where {{ api_window(var('api_days_long')) }}
