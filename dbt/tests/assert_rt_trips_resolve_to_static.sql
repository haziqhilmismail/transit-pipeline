select feed, count(*) as n_runs, count(*) filter (where not trip_in_static) as n_unresolved
from {{ ref('int_trip_instances') }}
where is_observed_run
group by feed
having count(*) filter (where not trip_in_static) > 0.15 * count(*)
