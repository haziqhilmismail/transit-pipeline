from datetime import UTC, date, datetime

import pytest
from conftest import BUS, feed_message, write_tar_xz

from transit_loader.feeds import ArchiveNotClosed, Feed, closed_archive_path
from transit_loader.realtime import Position, Snapshot, load_realtime, parse_snapshot, read_archive

KL = Feed.RAPID_BUS_KL


def test_parse_snapshot_keeps_absent_fields_as_none():
    payload = feed_message(1795132800, [{**BUS, "ts": 1795132790}, {"id": "9209", "trip_id": "9209", "lat": 0.0027, "lon": 0.0154, "ts": 5}])

    snapshot, positions = parse_snapshot("20261120T000021Z.pb", payload)

    assert snapshot == Snapshot(datetime(2026, 11, 20, 0, 0, 21, tzinfo=UTC), 1795132800, 2)
    assert positions[0] == Position(
        entity_id="WVK361", vehicle_id="WVK361", vehicle_label=None, license_plate=None,
        trip_id="weekday_U3000_0", route_id=None, start_date=None, start_time="16:30:10",
        direction_id=None, latitude=pytest.approx(3.14), longitude=pytest.approx(101.69),
        bearing=None, speed=None, vehicle_ts=1795132790,
    )
    assert positions[1].start_time is None
    assert positions[1].trip_id == "9209"


def test_parse_snapshot_returns_none_for_a_corrupt_payload():
    assert parse_snapshot("20261120T000121Z.pb", b"\xff\xff not a protobuf") is None


def test_read_archive_streams_every_snapshot_in_order(archive_root):
    path = archive_root / "realtime" / KL / "2026-11-20.tar.xz"

    parsed = list(read_archive(path))

    assert [p and (p[0].fetched_at.second, p[0].n_entities, len(p[1])) for p in parsed] == [(21, 1, 1), (41, 2, 2), (1, 0, 0), None]


def test_closed_archive_path_never_returns_the_open_day(archive_root):
    today = date(2026, 11, 21)

    assert closed_archive_path(archive_root, KL, date(2026, 11, 20), today_utc=today).name == "2026-11-20.tar.xz"
    with pytest.raises(ValueError):
        closed_archive_path(archive_root, KL, date(2026, 11, 21), today_utc=today)
    with pytest.raises(ArchiveNotClosed):
        closed_archive_path(archive_root, KL, date(2026, 11, 19), today_utc=today)


@pytest.mark.db
def test_load_counts_unnamed_and_repeated_members_as_bad_snapshots(conn, tmp_path):
    ping = feed_message(1795132800, [{**BUS, "ts": 1795132790}])
    members = {"20261120T000021Z.pb": ping, "retry/20261120T000021Z.pb": ping, "latest.pb": ping}
    write_tar_xz(tmp_path / "realtime" / KL / "2026-11-20.tar.xz", members)

    result = load_realtime(conn, tmp_path, KL, date(2026, 11, 20), today_utc=date(2026, 11, 21))

    assert (result.n_snapshots, result.n_bad_snapshots, result.n_positions) == (1, 2, 1)
