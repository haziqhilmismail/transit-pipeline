from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path


class Feed(StrEnum):
    RAPID_BUS_KL = "prasarana-rapid-bus-kl"
    MRT_FEEDER = "prasarana-rapid-bus-mrtfeeder"
    KTMB = "ktmb"
    RAPID_RAIL_KL = "prasarana-rapid-rail-kl"


REALTIME_FEEDS: tuple[Feed, ...] = (Feed.RAPID_BUS_KL, Feed.MRT_FEEDER, Feed.KTMB)


class ArchiveNotClosed(Exception):
    """The .tar.xz for this day does not exist yet. Airflow retries on it."""


def closed_archive_path(root: Path, feed: Feed, archive_date: date, *, today_utc: date) -> Path:
    """Return <root>/realtime/<feed>/<date>.tar.xz. Never the open, uncompressed `.tar` of today."""
    if archive_date >= today_utc:
        raise ValueError(f"{archive_date} is not a closed UTC day (today is {today_utc})")
    path = root / "realtime" / feed / f"{archive_date.isoformat()}.tar.xz"
    if not path.is_file():
        raise ArchiveNotClosed(str(path))
    return path


@dataclass(frozen=True)
class StaticVersion:
    feed: Feed
    version_id: str
    fetched_at: datetime
    path: Path


def list_static_versions(root: Path, feed: Feed) -> list[StaticVersion]:
    """All <root>/static/<feed>/*.zip, oldest first. File stem: 20261124T194911Z_<hash>."""
    versions = []
    for path in (root / "static" / feed).glob("*.zip"):
        stamp = path.stem.split("_", 1)[0]
        fetched_at = datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        versions.append(StaticVersion(feed, path.stem, fetched_at, path))
    return sorted(versions, key=lambda v: (v.fetched_at, v.version_id))
