# transit-pipeline design

This file records the final design. The last section lists where the build differs from the reviewed design, and why.

## 1. What the data says

These facts come from probes of the archive (2026-11-20 to 2026-11-23). They set the shape of the pipeline.

| Fact | Consequence |
|---|---|
| A UTC archive day starts at 08:00 MYT. | The unit of transformation is the MYT service date (03:00 cutoff). Service date D needs archives D-1 and D. |
| On kl, RT `start_time` jitters within a run and is stamped when the feed first sees a run. | RT `start_time` is not a trip key. Silver sessionizes pings into runs. |
| mrtfeeder and ktmb send no `start_time`. ktmb sends no `route_id`. | One sessionization rule for all feeds. Route comes from static `trips` through `trip_id`. |
| mrtfeeder RT `route_id` (`T100`) equals static `route_long_name`, not `route_id`. | A seed (`feed_config.rt_route_match`) names the match column per feed. |
| Only rapid-bus-kl and rapid-rail-kl have `frequencies.txt`. | One departures model for frequency and timetable feeds. |
| The kl feed tracks part of the fleet. | `coverage` is a lower bound on delivery, not operator performance. |
| ktmb realtime is 9 fixed placeholder positions, with `vehicle.id` = `trip_id`. | ktmb stays in bronze and `fct_feed_gap`. `feed_config.rt_usable = false` keeps it out of run models. |
| One feeder bus reports a 2029 timestamp. Some positions are near (0, 0). Some snapshots are empty. | Silver flags `is_valid_ts` and `is_valid_position`. Empty snapshots stay in bronze and count as feed gaps. |
| Static versions change daily, but mostly only `calendar.txt`. rapid-rail-kl zips hold `__MACOSX/` entries. | Static bronze is content-addressed per file and schema-free (JSONB). |
| The mrtfeeder calendar starts the day after each fetch. | The static version for a date must run at least one service on that date. |

## 2. Layers

### Bronze (schema `bronze`, written only by `transit-load`)

| Table | Grain | Notes |
|---|---|---|
| `rt_archive` | (feed, archive_date) | Counts, sha256, `loaded_at`. Written last in the load transaction. |
| `rt_snapshot` | (feed, fetched_at) | Empty snapshots included. |
| `rt_vehicle_position` | one entity in one snapshot | Protobuf values as given. NULL = absent field. Index (archive_date, feed). |
| `static_version` | (feed, version_id) | Written last in its transaction: the commit marker of a version. |
| `static_version_file` | (feed, version_id, file_name) | sha256 of each allowed GTFS file. |
| `gtfs_row` | (file_sha256, row_num) | One CSV row as JSONB, stored once per distinct content. |

Loads are idempotent by replacement. `load_realtime` deletes and copies one (feed, archive day) in one transaction.
`load_static` skips versions in `static_version` and file contents that are already loaded.

### Silver (schema `silver`, dbt)

| Model | Materialization | Purpose |
|---|---|---|
| `stg_rt__snapshots`, `stg_gtfs__stop_times` | view | Typed columns. |
| `stg_gtfs__routes`, `__trips`, `__calendar`, `__calendar_dates`, `__frequencies` | table | JSONB to typed columns. Tables, because each is joined many times. |
| `int_complete_dates` | view | (feed, service date) with both archives loaded. |
| `int_active_services` | table | Active service_ids per date for every candidate static version. |
| `int_service_dates` | table | Complete dates and the static version each is measured against. |
| `int_feed_gaps` | table | Gaps over 120 s between non-empty snapshots. |
| `positions` | incremental, window | One row per ping (dedup across snapshots) with validity flags. |
| `int_trip_sessions` | incremental, window | Runs from gaps and islands. Unit tested. |
| `int_trip_instances` | table | Runs with static route and pattern. |
| `int_first_departures` | incremental by file sha256 | First-stop departure per trip per `stop_times.txt` content. |
| `int_scheduled_departures` | incremental, window | Departures per service date, both schedule kinds. Unit tested. |

### Gold (schema `gold`, dbt tables)

| Model | Grain | Main columns |
|---|---|---|
| `dim_route` | (feed, route_id) | route_code, names, route_type, in_static. All static versions, plus RT-only routes. |
| `fct_trip` (contract) | one observed run | departed_at, observed and scheduled headway, headway_ratio, pattern_coverage, headway_class |
| `fct_route_day` (contract) | (feed, service_date, route_id) | observed_trips, scheduled_trips, coverage, pct_on_headway, median_headway_ratio, feed_uptime_pct |
| `fct_route_hour_activity` | (feed, route_id, service_date, hour_start_myt) | active_vehicles, active_trips |
| `fct_feed_gap` | (feed, service_date, gap_start_at) | gap_s, gap_s_in_day |

## 3. Rules

- **Service date.** `((ts at time zone 'Asia/Kuala_Lumpur') - interval '3 hours')::date`, from the snapshot time.
- **Static version for date D.** The latest version fetched before D+1 03:00 MYT that runs at least one service on D.
  If no version runs a service, the latest version. A singular test fails if a realtime date has no departures.
- **Dedup.** One row per (feed, service_date, vehicle_id, vehicle_ts). The earliest snapshot wins.
- **Sessionization.** Per (feed, service_date, vehicle_id), valid pings only, ordered by vehicle time. A new run starts when:
  - the `trip_id` changes;
  - the vehicle was silent for more than 30 min while the feed was up (time inside feed gaps does not count);
  - the RT `start_time` of the same `trip_id` moves forward by more than 10 min (the re-stamp of a new run).
- **Observed run.** At least 5 pings and a position extent of at least 500 m. Gold uses observed runs only.
- **Route.** The static trip of the RT `trip_id`; else the RT `route_id` matched on `feed_config.rt_route_match`;
  else the raw RT value. `pattern_id` = static `shape_id`, else `direction_id`.
- **Scheduled departures.** A trip with frequency rows: `generate_series(start, end - 1, headway)`. Other trips:
  departure at the lowest `stop_sequence`. `sched_headway_s` = gap to the previous departure of the same route and pattern.
- **Headway.** `observed_headway_s` = gap to the previous observed run of the same route and pattern that day.
  Each run matches the nearest scheduled departure within 30 min. `headway_class` is `unscheduled` (no slot),
  `low_coverage` (pattern coverage under 0.9), `first_of_day`, `bunched` (< 0.5), `on_headway` (0.5 to 1.5), or `gapped` (> 1.5).
- **Feed uptime.** Share of the scheduled service span (first to last departure) that is not inside a feed gap.

All thresholds are dbt vars in `dbt/dbt_project.yml`.

## 4. Orchestration

- `transit_ingest`: `CronDataIntervalTimetable("30 0 * * *")`, `catchup=False`, 3 retries 20 min apart.
  `load_static >> load_realtime`. `load_realtime` loads the archive of its data interval start and emits the asset `transit_bronze`.
  Backfill uses `airflow backfill create` (`make backfill`).
- `transit_transform`: scheduled on `transit_bronze`. `dbt_build`, then `source_freshness` (trigger rule `all_done`,
  so a failed build still gets a freshness check, and the freshness check never blocks the build).
- The dbt window: with vars `start_date`/`end_date`, that range. Without them, the macro `transform_window` reads
  `silver.int_complete_dates` and `silver.build_log`. It returns the range of complete dates never built, or loaded
  after their last build. Each windowed model deletes the window in a pre-hook, then appends. So a rebuild that gives 0
  rows still removes old rows. The `on-run-end` hook writes build stamps only when no node failed and all
  three windowed models ran.
- `AIRFLOW__CORE__PARALLELISM=1`: one task runs at a time across both DAGs.

## 5. Tests

- Loader (pytest): protobuf decode with absent fields, corrupt payloads, the open-archive rule, the zip filter, the CSV
  BOM and CRLF, and two Postgres tests: a second load gives the same rows, and a failed reload keeps the previous day.
- dbt unit tests: sessionization (jitter, re-stamp, silence while the feed is up, silence inside an outage, an
  invalid timestamp, a trip change, the movement gate) and scheduled departures (frequency expansion, timetable headways,
  inactive service).
- dbt data tests: unique keys, not null, relationships of `fct_trip` and `fct_route_day` to `dim_route`,
  accepted values, contracts on `fct_trip` and `fct_route_day`, and 3 singular tests (static calendar agrees with the
  service date for over 99% of runs; every realtime date has departures; at most 15% of runs have an RT `trip_id`
  missing from static).
- Source freshness on `rt_snapshot.fetched_at` (warn 27 h, error 50 h) and `static_version.fetched_at` (warn 3 days, error 8 days).

## 6. Implementation reconciliation

Each item is a change from the reviewed design (synthesis plus verdict), with the reason.

1. **`gtfs_row` has no `feed` or `file_name` column.** The same content can belong to two feeds. A content counts as
   loaded when a `static_version_file` row has its sha256, and both commit in one transaction.
2. **`rt_vehicle_position` has no primary key.** The feeds do not promise a unique entity id per snapshot. A key
   violation would fail a whole day. Dedup happens in silver.
3. **Route resolution moved out of `positions`.** It runs per run in `int_trip_instances`, not per ping. Sessionization
   is its own model (`int_trip_sessions`) with two inputs, so its unit test stays small.
4. **The window is computed in dbt, not in an Airflow task.** The macro reads bronze state and `silver.build_log`.
   The DAG has one build task, and a manual `dbt build` uses the same rule.
5. **No `schedule_kind` or `route_code_column` in `feed_config`.** A trip with frequency rows uses them, so the
   data decides the kind. `route_code` = short name, else long name, which is correct for all 4 feeds.
6. **`observed_share` is named `coverage`.** It is the same ratio as the verdict's coverage. One term for one meaning.
   The pattern-level value is `fct_trip.pattern_coverage`.
7. **`fct_route_hour_activity` has no `pings` column.** It counts runs that span each hour, so it reads one table of runs.
8. **`feed_uptime_pct` uses the scheduled service span, not the full day.** The feeds send empty snapshots at night,
   so a full-day value was about 81% on kl for a normal day.
9. **Small static staging models are tables.** As views, every join rescanned the 2M-row `gtfs_row` table
   (`int_trip_instances` took 57 s).
10. **Departures keep the GTFS service day.** GTFS times count from the local midnight of the service day, so a
    departure at 24:09 belongs to that day. The 03:00 rule applies to realtime pings only.
11. **Dedup is per service date.** A ping repeated across the 03:00 cutoff can appear in both days. It is rare
    (the feeds are almost empty at 03:00).
12. **The static loader loads every version on disk.** 123 zips give about 2M `gtfs_row` rows. The warehouse was
    about 1.9 GB after the 7-day load.
13. **Ports bind to 127.0.0.1.** The Airflow UI uses the simple auth manager with all users as admin, so it must not be on the LAN.
14. **`make docs` writes `static_index.html` and does not serve it.** Port 8081 is in use on the host.
15. **Memory limits:** Postgres 320 MB, API server 336 MB, scheduler 704 MB, DAG processor 240 MB (sum 1.6 GB).
    The first limits gave the scheduler 576 MB, and a dbt build reached 557 MB (later 599 MB). The DAG processor reached
    191 MB to 225 MB (no OOM kill). The API server used under 290 MB, so its limit went down.
16. **`is_departure_observed`.** True when the feed had no gap in the 30 min before the first ping, and the ping is
    at least 30 min after the service day starts.
17. **dbt-core 1.12 and dbt-postgres 1.11.** dbt 1.10 prints a deprecation warning.

## 7. Open points

- A static version that arrives late does not start a rebuild. The window follows `rt_archive.loaded_at` only. Run a
  manual range (`make dbt-build FROM= TO=`) after such a load.
- On kl, 1,030 of 8,031 observed runs (13%) are `unscheduled`: no departure of the same route and pattern within 30 min.
  616 of them have an RT `trip_id` that is not in the static version, so the pattern falls back to `direction_id`.
  The other 414 have a static trip but no departure within 30 min. Their cause is not checked yet.
- The thresholds (30 min, 10 min, 500 m, 5 pings, 0.9) come from weekday and weekend probes of one week. Check them again on more data.
