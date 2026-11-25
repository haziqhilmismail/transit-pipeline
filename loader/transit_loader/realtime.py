import hashlib
import tarfile
from collections.abc import Iterator
from dataclasses import astuple, dataclass
from datetime import UTC, date, datetime
from pathlib import Path, PurePosixPath

import psycopg
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2

from transit_loader.feeds import Feed, closed_archive_path


@dataclass(frozen=True)
class Snapshot:
    fetched_at: datetime
    header_ts: int | None
    n_entities: int


@dataclass(frozen=True)
class Position:
    entity_id: str
    vehicle_id: str | None
    vehicle_label: str | None
    license_plate: str | None
    trip_id: str | None
    route_id: str | None
    start_date: str | None
    start_time: str | None
    direction_id: int | None
    latitude: float | None
    longitude: float | None
    bearing: float | None
    speed: float | None
    vehicle_ts: int | None


def _opt(msg, field: str):
    return getattr(msg, field) if msg.HasField(field) else None


def parse_snapshot(member_name: str, payload: bytes) -> tuple[Snapshot, list[Position]] | None:
    stamp = PurePosixPath(member_name).stem
    fetched_at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    message = gtfs_realtime_pb2.FeedMessage()
    try:
        message.ParseFromString(payload)
    except DecodeError:
        return None
    positions = []
    for entity in message.entity:
        if not entity.HasField("vehicle"):
            continue
        vp = entity.vehicle
        positions.append(
            Position(
                entity_id=entity.id,
                vehicle_id=_opt(vp.vehicle, "id"),
                vehicle_label=_opt(vp.vehicle, "label"),
                license_plate=_opt(vp.vehicle, "license_plate"),
                trip_id=_opt(vp.trip, "trip_id"),
                route_id=_opt(vp.trip, "route_id"),
                start_date=_opt(vp.trip, "start_date"),
                start_time=_opt(vp.trip, "start_time"),
                direction_id=_opt(vp.trip, "direction_id"),
                latitude=_opt(vp.position, "latitude"),
                longitude=_opt(vp.position, "longitude"),
                bearing=_opt(vp.position, "bearing"),
                speed=_opt(vp.position, "speed"),
                vehicle_ts=_opt(vp, "timestamp"),
            )
        )
    snapshot = Snapshot(fetched_at, _opt(message.header, "timestamp"), len(message.entity))
    return snapshot, positions


def read_archive(path: Path) -> Iterator[tuple[Snapshot, list[Position]] | None]:
    with tarfile.open(path, "r|xz") as tar:
        for member in tar:
            if member.isfile() and member.name.endswith(".pb"):
                yield parse_snapshot(member.name, tar.extractfile(member).read())


@dataclass(frozen=True)
class RtLoadResult:
    feed: Feed
    archive_date: date
    n_snapshots: int
    n_bad_snapshots: int
    n_positions: int


POSITION_COLUMNS = (
    "feed, archive_date, fetched_at, entity_id, vehicle_id, vehicle_label, license_plate, trip_id, "
    "route_id, start_date, start_time, direction_id, latitude, longitude, bearing, speed, vehicle_ts"
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_realtime(
    conn: psycopg.Connection, root: Path, feed: Feed, archive_date: date, *, today_utc: date
) -> RtLoadResult:
    path = closed_archive_path(root, feed, archive_date, today_utc=today_utc)
    snapshots: list[Snapshot] = []
    n_bad = n_positions = 0
    with conn.transaction(), conn.cursor() as cur:
        for table in ("rt_vehicle_position", "rt_snapshot", "rt_archive"):
            cur.execute(
                f"delete from bronze.{table} where feed = %s and archive_date = %s", (feed, archive_date)
            )
        with cur.copy(f"copy bronze.rt_vehicle_position ({POSITION_COLUMNS}) from stdin") as copy:
            for parsed in read_archive(path):
                if parsed is None:
                    n_bad += 1
                    continue
                snapshot, positions = parsed
                snapshots.append(snapshot)
                for p in positions:
                    copy.write_row((feed, archive_date, snapshot.fetched_at, *astuple(p)))
                n_positions += len(positions)
        with cur.copy(
            "copy bronze.rt_snapshot (feed, archive_date, fetched_at, header_ts, n_entities) from stdin"
        ) as copy:
            for s in snapshots:
                copy.write_row((feed, archive_date, s.fetched_at, s.header_ts, s.n_entities))
        cur.execute(
            "insert into bronze.rt_archive"
            " (feed, archive_date, archive_sha256, n_snapshots, n_bad_snapshots, n_positions)"
            " values (%s, %s, %s, %s, %s, %s)",
            (feed, archive_date, _sha256(path), len(snapshots), n_bad, n_positions),
        )
    return RtLoadResult(feed, archive_date, len(snapshots), n_bad, n_positions)
