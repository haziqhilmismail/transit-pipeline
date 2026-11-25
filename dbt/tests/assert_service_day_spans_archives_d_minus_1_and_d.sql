select d::date as service_date
from generate_series('2026-01-01'::date, '2026-12-31'::date, interval '1 day') d
where ({{ service_day_start('d::date') }} at time zone 'UTC')::date <> d::date - 1
    or (({{ service_day_start('d::date + 1') }} - interval '1 microsecond') at time zone 'UTC')::date <> d::date
