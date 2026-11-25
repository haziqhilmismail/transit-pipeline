from datetime import date

import pytest
from psycopg.rows import dict_row

from transit_loader.feeds import Feed
from transit_loader.realtime import load_realtime
from transit_loader.static import load_static

pytestmark = pytest.mark.db
KL = Feed.RAPID_BUS_KL
DAY = date(2026, 11, 20)
TODAY = date(2026, 11, 21)


def counts(conn) -> dict:
    return conn.cursor(row_factory=dict_row).execute(
        "select (select count(*) from bronze.rt_vehicle_position) as positions,"
        " (select count(*) from bronze.rt_snapshot) as snapshots,"
        " (select n_bad_snapshots from bronze.rt_archive) as bad_snapshots,"
        " (select count(*) from bronze.static_version) as versions,"
        " (select count(*) from bronze.static_version_file) as version_files,"
        " (select count(*) from bronze.gtfs_row) as gtfs_rows"
    ).fetchone()


REALTIME = ("positions", "snapshots", "bad_snapshots")


def test_loads_converge_to_the_same_rows_when_run_twice(conn, archive_root):
    load_static(conn, archive_root, KL)
    load_realtime(conn, archive_root, KL, DAY, today_utc=TODAY)
    first = counts(conn)

    rerun_static = load_static(conn, archive_root, KL)
    load_realtime(conn, archive_root, KL, DAY, today_utc=TODAY)

    assert first == {"positions": 3, "snapshots": 3, "bad_snapshots": 1, "versions": 2, "version_files": 4, "gtfs_rows": 4}
    assert counts(conn) == first
    assert rerun_static.versions_loaded == 0


def test_a_failed_reload_keeps_the_previous_day(conn, archive_root):
    load_realtime(conn, archive_root, KL, DAY, today_utc=TODAY)
    path = archive_root / "realtime" / KL / "2026-11-20.tar.xz"
    good = path.read_bytes()
    path.write_bytes(good[: len(good) // 2])

    with pytest.raises(Exception):
        load_realtime(conn, archive_root, KL, DAY, today_utc=TODAY)

    after = counts(conn)
    assert {k: after[k] for k in REALTIME} == {"positions": 3, "snapshots": 3, "bad_snapshots": 1}
