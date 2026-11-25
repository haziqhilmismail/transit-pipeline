import csv
import hashlib
import io
import json
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg

from transit_loader.feeds import Feed, list_static_versions

ALLOWED_FILES = frozenset(
    {
        "agency.txt",
        "routes.txt",
        "trips.txt",
        "stops.txt",
        "stop_times.txt",
        "calendar.txt",
        "calendar_dates.txt",
        "frequencies.txt",
    }
)


@dataclass(frozen=True)
class GtfsFile:
    file_name: str
    sha256: str
    payload: bytes


def read_version(path: Path) -> Iterator[GtfsFile]:
    with zipfile.ZipFile(path) as zf:
        for name in sorted(zf.namelist()):
            if name in ALLOWED_FILES:
                payload = zf.read(name)
                yield GtfsFile(name, hashlib.sha256(payload).hexdigest(), payload)


def parse_rows(f: GtfsFile) -> Iterator[dict[str, str]]:
    text = io.TextIOWrapper(io.BytesIO(f.payload), encoding="utf-8-sig", newline="")
    reader = csv.reader(text)
    header = [h.strip() for h in next(reader, [])]
    for values in reader:
        if values:
            yield dict(zip(header, values, strict=False))


@dataclass(frozen=True)
class StaticLoadResult:
    feed: Feed
    versions_seen: int
    versions_loaded: int
    files_loaded: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_static(conn: psycopg.Connection, root: Path, feed: Feed) -> StaticLoadResult:
    versions = list_static_versions(root, feed)
    with conn.cursor() as cur:
        cur.execute("select version_id from bronze.static_version where feed = %s", (feed,))
        loaded = {row[0] for row in cur.fetchall()}
    versions_loaded = files_loaded = 0
    for version in versions:
        if version.version_id in loaded:
            continue
        with conn.transaction(), conn.cursor() as cur:
            for f in read_version(version.path):
                cur.execute(
                    "select 1 from bronze.static_version_file where file_sha256 = %s limit 1", (f.sha256,)
                )
                if cur.fetchone() is None:
                    with cur.copy("copy bronze.gtfs_row (file_sha256, row_num, data) from stdin") as copy:
                        for row_num, row in enumerate(parse_rows(f)):
                            copy.write_row((f.sha256, row_num, json.dumps(row)))
                    files_loaded += 1
                cur.execute(
                    "insert into bronze.static_version_file (feed, version_id, file_name, file_sha256)"
                    " values (%s, %s, %s, %s)",
                    (feed, version.version_id, f.file_name, f.sha256),
                )
            cur.execute(
                "insert into bronze.static_version (feed, version_id, fetched_at, zip_sha256)"
                " values (%s, %s, %s, %s)",
                (feed, version.version_id, version.fetched_at, _sha256(version.path)),
            )
        versions_loaded += 1
    return StaticLoadResult(feed, len(versions), versions_loaded, files_loaded)
