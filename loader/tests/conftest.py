import io
import os
import tarfile
import zipfile
from pathlib import Path

import psycopg
import pytest
from google.transit import gtfs_realtime_pb2


def feed_message(header_ts: int, vehicles: list[dict]) -> bytes:
    message = gtfs_realtime_pb2.FeedMessage()
    message.header.gtfs_realtime_version = "2.0"
    message.header.timestamp = header_ts
    for v in vehicles:
        entity = message.entity.add()
        entity.id = v["id"]
        vp = entity.vehicle
        vp.vehicle.id = v["id"]
        vp.trip.trip_id = v["trip_id"]
        if "start_time" in v:
            vp.trip.start_time = v["start_time"]
        vp.position.latitude = v["lat"]
        vp.position.longitude = v["lon"]
        vp.timestamp = v["ts"]
    return message.SerializeToString()


def write_tar_xz(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(path, "w:xz") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return path


def write_zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, payload in members.items():
            zf.writestr(name, payload)
    return path


BUS = {"id": "WVK361", "trip_id": "weekday_U3000_0", "start_time": "16:30:10", "lat": 3.14, "lon": 101.69}


@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    members = {
        "20261120T000021Z.pb": feed_message(1795132800, [{**BUS, "ts": 1795132790}]),
        "20261120T000041Z.pb": feed_message(1795132820, [{**BUS, "ts": 1795132810}, {**BUS, "id": "B2", "ts": 1}]),
        "20261120T000101Z.pb": feed_message(1795132840, []),
        "20261120T000121Z.pb": b"\xff\xff not a protobuf",
    }
    write_tar_xz(tmp_path / "realtime" / "prasarana-rapid-bus-kl" / "2026-11-20.tar.xz", members)
    (tmp_path / "realtime" / "prasarana-rapid-bus-kl" / "2026-11-21.tar").write_bytes(b"")
    routes = b"\xef\xbb\xbfroute_id ,route_short_name\r\nU3000,300\r\nB1000,SUNWAY LINE\r\n"
    for version, calendar in (("20261118T010000Z_aaaa", b"service_id\nweekday\n"), ("20261119T010000Z_bbbb", b"service_id\nweekend\n")):
        write_zip(
            tmp_path / "static" / "prasarana-rapid-bus-kl" / f"{version}.zip",
            {"routes.txt": routes, "calendar.txt": calendar, "shapes.txt": b"shape_id\nx\n", "__MACOSX/._routes.txt": b"\x00\x05"},
        )
    (tmp_path / "static" / "prasarana-rapid-bus-kl" / "state.json").write_text("{}")
    return tmp_path


@pytest.fixture
def conn():
    dsn = os.environ.get("WAREHOUSE_DSN")
    if not dsn:
        pytest.skip("WAREHOUSE_DSN not set")
    try:
        admin = psycopg.connect(dsn, autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as e:
        pytest.skip(f"warehouse not reachable: {e}")
    with admin:
        admin.execute("drop database if exists transit_test with (force)")
        admin.execute("create database transit_test")
    test_dsn = psycopg.conninfo.make_conninfo(dsn, dbname="transit_test")
    from transit_loader.db import connect

    with connect(test_dsn) as c:
        yield c
