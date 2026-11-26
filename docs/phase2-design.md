> Design record for phase 2, as approved on 2026-11-26. Kept as written; later changes live in the code and README.

# transit-pipeline phase 2: design record

Approved by the user on 2026-11-26 after an architect arena (opus, fable, sonnet, haiku; cross-judge picked fable's design as the base) and an interrogate round (4 reviewers). This file is the merged result. Where an "Interrogate fix" and a base decision differ, the fix wins.

## Goal

1. Two always-on collectors of the api.data.gov.my GTFS feeds. A is Harith's hoarder on tower (his site needs it within 10 s). B is the same hoarder image on a free GCP e2-micro with its own public address, so it has its own rate limit. There is no failover switch. B exists to fill A's gaps (tower loses power now and then).
2. Gap repair: B's packed days are uploaded to a private Cloud Storage bucket. Airflow repairs A's closed packed days from B's copy, then loads bronze and runs dbt.
3. Health: Airflow checks A (status.json) and B (its last upload) and alerts.
4. Stats API: PostgREST, read-only role, on tables built by dbt in schema `api` over gold.
5. Dashboard: separate public repo haziqhilmismail/transit-dashboard, one static TNB-style page, served at https://transit.haziqhilmi.com through a new Cloudflare tunnel.
6. Harith's site (github.com/harithilmi/kl-transit-v2): PR 1 re-renders a repaired day (3 h settle), PR 2 adds a per-route reliability panel that calls our API cross-origin. Deploys only with the user's approval.

## Key decisions

- B runs Harith's unchanged image (`transit-server:local`), shipped from tower through the bucket; settings as his compose.hoarder.yaml except REALTIME_INTERVAL=30. No build on the VM, no deploy key.
- B costs $0 (user rule): no external IPv4 (billed). api.data.gov.my has IPv6 (2606:4700:7::60), so B is IPv6-only. A budget alert covers the GCP project.
- B uploads packed days every 5 min (no-clobber) and status.json + heartbeat.json (written, ext_ipv6, image_id) every 15 min. Private bucket, 90-day lifecycle. B's service account: objectCreator + objectViewer only; tower reads with a separate objectViewer key kept outside git.
- Tower reads the bucket with rclone (not google-cloud-storage, to avoid the protobuf pin conflict).
- Ownership: the hoarder owns every open .tar, the first .tar.xz, static and status.json. `transit-load repair` may replace a closed, packed, quiet day (> 300 s, no .tar, no .tmp) at most once, by rename. Ledger `bronze.rt_repair` with an intent row (`repairing`) for crash recovery. Originals kept 30 days in ${TRANSIT_STATE}/replaced/. TRANSIT_REPAIR=off|dry|on, default dry.
- The loader reloads a day whose archive sha changed; dbt needs no change (loaded_at > built_at).
- API: tables (not views, because dbt drops gold with cascade), time-windowed (vars api_days_long 90, api_days_short 35, api_days_summary 28), no vehicle or trip ids, max 2,000 rows, statement_timeout 2 s, pool 3, OpenAPI off. Caddy: GET/HEAD only, request headers normalised (Accept forced to JSON; Range, Authorization, Prefer, Accept-Profile, Content-Profile removed), no-store on errors, public max-age=600 on 2xx, at most 8 in flight with a 2 s queue limit, static `Access-Control-Allow-Origin: https://transit.harithilmi.com`.
- Harith's panel calls the API cross-origin (his firewall blocks his Caddy from our containers). His deploy needs CSP_CONNECT=https://transit.haziqhilmi.com in his tower .env.
- Late repair after his site rendered a day: purge the changed /v1 URLs at Cloudflare with a cache-purge-only token for harithilmi.com (user decision).
- Alerts: Telegram (existing bot) on collector state change and every 6 h while bad; healthchecks.io dead-man ping on every finished health run.

## Interrogate fixes (binding)

1. Real power-cut days hold duplicate member names. Accept non-decreasing names; stable merge, A first on ties; A's member sequence kept in order. Malformed A -> `skipped_malformed` (final). Malformed B -> `waiting` until the lookback ends.
2. Repair runs every hour; load and dbt run only when something changed or a new day closed. Harith's SETTLE is 3 h from pack mtime.
3. Outage threshold GAP_S = 600 s (shorter gaps are routine). B's day is downloaded only when A has a gap >= 600 s. NEAR_S = 60 s: a B member is added only when A has no member within 60 s of it.
4. A whole missing day (no .tar, .tar.xz or .tmp in A) is copied from B: outcome `copied`.
5. UNFILLABLE: visit LOOKBACK_DAYS + 1.
6. load_static gets trigger_rule ALL_DONE; B late is a soft `waiting` state that does not fail the DAG run.
7. Merged tar in tarfile.USTAR_FORMAT; compression with the `xz -6` CLI as a subprocess.
8. Write access: AIRFLOW_UID=1000 (same uid as the hoarder) and a scheduler-only rw mount of realtime/. No setfacl.
9. Cloudflare cache rule and rate-limit rule scoped to `http.host eq "transit.haziqhilmi.com"` only (else they would also match other sites in the zone). JSON paths need a "cache everything" rule for /api/*.
10. CORS: one static allowed origin (Harith's), no echo; no preflight (GET with no custom headers only). Panel fetch has a 3 s timeout.
11. Dead-man ping on every finished health run; collector state to Telegram only; grace above the longest DAG run; weekly `airflow db clean`.
12. Dashboard Caddy config mounted as a directory.
13. Budget alert; external IPv4 avoided (IPv6-only VM).
14. B heartbeat image id compared to tower's image id; mismatch = warning.
15. Prune replaced/ originals after 30 days.

## Build units

1. Repair core + loader reload-on-sha (dry). Done on phase2/repair.
2. GCP project, bucket, IPv6-only VM, collector B, uploads. In progress.
3. Harith PR 1 (SETTLE + re-render on change + docs). Own thread.
4. Repair in Airflow (hourly, dry for 2 days, then on), rclone fetch of B, rw mount, AIRFLOW_UID=1000.
5. Health DAG + Telegram + healthchecks.io.
6. API tables, roles, PostgREST, Caddy. Done on phase2/api.
7. Dashboard repo. Own thread; public at transit.haziqhilmi.com since 2026-11-26 17:51 UTC through its own Cloudflare tunnel.
8. Cloudflare cache + rate rules (needs a Zone Rules token).
9. Harith PR 2 (route panel, CORS). Own thread.
