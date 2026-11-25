import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path

log = logging.getLogger(__name__)


class Feed(StrEnum):
    RAPID_BUS_KL = "prasarana-rapid-bus-kl"
    MRT_FEEDER = "prasarana-rapid-bus-mrtfeeder"
    KTMB = "ktmb"
    RAPID_RAIL_KL = "prasarana-rapid-rail-kl"


REALTIME_FEEDS: tuple[Feed, ...] = (Feed.RAPID_BUS_KL, Feed.MRT_FEEDER, Feed.KTMB)


def parse_stamp(stamp: str) -> datetime:
    """Hoarder fetch time, as in `20261120T000021Z.pb` and `20261118T010000Z_<hash>.zip`."""
    return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ArchiveNotClosed(Exception):
    pass


def closed_archive_path(root: Path, feed: Feed, archive_date: date, *, today_utc: date) -> Path:
    if archive_date >= today_utc:
        raise ValueError(f"{archive_date} is not a closed UTC day (today is {today_utc})")
    path = root / "realtime" / feed / f"{archive_date.isoformat()}.tar.xz"
    if not path.is_file():
        raise ArchiveNotClosed(str(path))
    return path


def closed_archive_dates(root: Path, feed: Feed, *, today_utc: date, earliest: date) -> list[date]:
    dates = []
    for path in (root / "realtime" / feed).glob("*.tar.xz"):
        try:
            archive_date = date.fromisoformat(path.name.removesuffix(".tar.xz"))
        except ValueError:
            log.warning("skip %s: name is not an archive date", path)
            continue
        if earliest <= archive_date < today_utc:
            dates.append(archive_date)
    return sorted(dates)


@dataclass(frozen=True)
class StaticVersion:
    feed: Feed
    version_id: str
    fetched_at: datetime
    path: Path


def list_static_versions(root: Path, feed: Feed) -> list[StaticVersion]:
    versions = []
    for path in (root / "static" / feed).glob("*.zip"):
        try:
            fetched_at = parse_stamp(path.stem.split("_", 1)[0])
        except ValueError:
            log.warning("skip %s: name has no fetch time", path)
            continue
        versions.append(StaticVersion(feed, path.stem, fetched_at, path))
    return sorted(versions, key=lambda v: (v.fetched_at, v.version_id))
