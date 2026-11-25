-- Some RT trip_ids are missing from every static version (measured 43 of 541 on kl, 103 of 2,903 on
-- mrtfeeder). Fail only when more than 15% of a feed's observed runs do not resolve.
select feed, count(*) as n_runs, count(*) filter (where not trip_in_static) as n_unresolved
from {{ ref('int_trip_instances') }}
where is_observed_run
group by feed
having count(*) filter (where not trip_in_static) > 0.15 * count(*)
