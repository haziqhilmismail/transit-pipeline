import os
from datetime import date

import pytest
from conftest import BUS, feed_message, write_tar_xz

from transit_loader.cli import main
from transit_loader.feeds import Feed

pytestmark = pytest.mark.db
KL = Feed.RAPID_BUS_KL


@pytest.fixture
def env(monkeypatch, warehouse_dsn, archive_root):
    monkeypatch.setenv("WAREHOUSE_DSN", warehouse_dsn)
    monkeypatch.setenv("TRANSIT_ARCHIVE_ROOT", str(archive_root))
    monkeypatch.setenv("TRANSIT_EARLIEST_DATE", "2026-11-19")
    return archive_root


def loaded_archives(conn) -> dict:
    return dict(conn.execute("select archive_date, loaded_at from bronze.rt_archive where feed = %s", (KL,)).fetchall())


def test_realtime_without_date_loads_each_closed_day_once_from_the_earliest_date(env, conn):
    one_ping = {"20261119T000021Z.pb": feed_message(1795046400, [{**BUS, "ts": 1795046390}])}
    write_tar_xz(env / "realtime" / KL / "2026-11-18.tar.xz", one_ping)
    write_tar_xz(env / "realtime" / KL / "2026-11-19.tar.xz", one_ping)

    assert main(["realtime"]) == 0
    first = loaded_archives(conn)
    assert main(["realtime"]) == 0

    assert sorted(first) == [date(2026, 11, 19), date(2026, 11, 20)]
    assert loaded_archives(conn) == first


def test_static_skips_a_corrupt_zip_and_a_zip_without_a_fetch_time(env, conn, monkeypatch):
    monkeypatch.setenv("TRANSIT_REPAIR", "not-a-mode")  # only the repair command reads it
    (env / "static" / KL / "20261120T010000Z_cccc.zip").write_bytes(b"PK\x03\x04 truncated")
    (env / "static" / KL / "latest.zip").write_bytes(b"")

    assert main(["static", "--feed", KL]) == 0

    assert [r[0] for r in conn.execute("select version_id from bronze.static_version order by 1")] == [
        "20261118T010000Z_aaaa",
        "20261119T010000Z_bbbb",
    ]


def snapshot_counts(conn) -> dict:
    return dict(conn.execute("select archive_date, n_snapshots from bronze.rt_archive where feed = %s", (KL,)).fetchall())


def test_realtime_reloads_a_day_only_when_its_file_content_changed(env, conn):
    path = env / "realtime" / KL / "2026-11-20.tar.xz"
    assert main(["realtime"]) == 0
    first = loaded_archives(conn)

    os.utime(path)
    assert main(["realtime"]) == 0
    assert loaded_archives(conn) == first

    write_tar_xz(path, {"20261120T000021Z.pb": feed_message(1795132800, [{**BUS, "ts": 1795132790}])})
    assert main(["realtime"]) == 0
    assert snapshot_counts(conn) == {date(2026, 11, 20): 1}
