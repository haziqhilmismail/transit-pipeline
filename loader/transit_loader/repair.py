"""Fill outage gaps in collector A's closed archive days from collector B's copy of the same day.

The only code outside the hoarder that writes under the archive: it replaces a packed day
`realtime/<feed>/<date>.tar.xz` at most once, by rename, or puts in a whole day that A does not have.
"""

import hashlib
import io
import logging
import lzma
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import psycopg

from transit_loader.feeds import archive_members, archive_path, file_sha256, parse_stamp

log = logging.getLogger(__name__)

GAP_S = 600
NEAR_S = 60
LOOKBACK_DAYS = 7
QUIET_S = 300
TMP_SUFFIX = ".repair.tmp"

FetchB = Callable[[str, date], Path | None]


class Mode(StrEnum):
    OFF = "off"
    DRY = "dry"
    ON = "on"


class Outcome(StrEnum):
    CLEAN = "clean"
    REPAIRED = "repaired"
    COPIED = "copied"
    NO_FILL = "no_fill"
    UNFILLABLE = "unfillable"
    SKIPPED_MALFORMED = "skipped_malformed"
    WAITING = "waiting"
    REPAIRING = "repairing"


FINAL = frozenset(Outcome) - {Outcome.WAITING, Outcome.REPAIRING}


class Malformed(ValueError):
    pass


@dataclass(frozen=True)
class Gap:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> int:
        return int((self.end - self.start).total_seconds())


@dataclass(frozen=True)
class Plan:
    fill: tuple[str, ...]
    gap_s_before: int
    gap_s_after: int


@dataclass(frozen=True)
class RepairResult:
    feed: str
    archive_date: date
    outcome: Outcome
    members_added: int = 0
    gap_s_before: int = 0
    gap_s_after: int = 0
    dry_run: bool = False


def stamps_of(names: Sequence[str], day: date) -> list[datetime]:
    """Fetch times of a day's members. Raises Malformed unless each name is a stamp of `day`, never going back."""
    try:
        stamps = [parse_stamp(name.removesuffix(".pb")) for name in names]
    except ValueError as e:
        raise Malformed(str(e)) from e
    if any(s.date() != day for s in stamps):
        raise Malformed(f"a member is not stamped {day}")
    if any(a > b for a, b in zip(stamps, stamps[1:])):
        raise Malformed("member names go back in time")
    return stamps


def find_gaps(stamps: Sequence[datetime], day: date) -> tuple[Gap, ...]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    edges = [start, *stamps, start + timedelta(days=1)]
    return tuple(Gap(a, b) for a, b in zip(edges, edges[1:]) if (b - a).total_seconds() > GAP_S)


def plan_repair(a_names: Sequence[str], b_names: Sequence[str], day: date) -> Plan:
    """B's members that lie strictly inside one of A's gaps with no A member within NEAR_S.

    A fixed point: planning again against the merged day finds nothing to fill.
    """
    a_stamps = stamps_of(a_names, day)
    b_stamps = stamps_of(b_names, day)
    gaps = find_gaps(a_stamps, day)
    a_set = set(a_stamps)
    near = timedelta(seconds=NEAR_S)

    def fills(t: datetime) -> bool:
        for g in gaps:
            if g.start < t < g.end:
                return all(abs(t - edge) > near for edge in (g.start, g.end) if edge in a_set)
        return False

    fill = tuple(name for name, t in zip(b_names, b_stamps) if fills(t))
    after = find_gaps(sorted(a_stamps + stamps_of(fill, day)), day)
    return Plan(fill, sum(g.seconds for g in gaps), sum(g.seconds for g in after))


def member_names(path: Path) -> list[str]:
    try:
        return [info.name for info, _ in archive_members(path)]
    except (lzma.LZMAError, tarfile.TarError, EOFError) as e:
        raise Malformed(f"{path}: {e}") from e


def _digests(path: Path) -> list[tuple[str, str]]:
    return [(info.name, hashlib.sha256(data).hexdigest()) for info, data in archive_members(path)]


def write_merged(a_path: Path, b_path: Path, fill: Sequence[str], out: Path, work: Path) -> None:
    """Stable merge by member name (A first on ties) of A's members and B's `fill` members, xz-compressed to `out`.

    Reads `out` back and raises unless it holds A's members byte for byte and in order, plus exactly `fill`.
    """
    wanted = set(fill)
    extra = ((info, data) for info, data in archive_members(b_path) if info.name in wanted)
    pending = next(extra, None)
    tar_path = work / "merged.tar"
    with tarfile.open(tar_path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for info, data in archive_members(a_path):
            while pending and pending[0].name < info.name:
                tar.addfile(pending[0], io.BytesIO(pending[1]))
                pending = next(extra, None)
            tar.addfile(info, io.BytesIO(data))
        while pending:
            tar.addfile(pending[0], io.BytesIO(pending[1]))
            pending = next(extra, None)
    with out.open("wb") as f:
        subprocess.run(["xz", "-6", "-T1", "-c", str(tar_path)], stdout=f, check=True)

    a_seq, merged = _digests(a_path), _digests(out)
    names = [name for name, _ in merged]
    rest = iter(merged)
    if not all(m in rest for m in a_seq):
        raise RuntimeError(f"{out}: A's members are not all kept in order")
    if sorted(names) != sorted([n for n, _ in a_seq] + list(fill)) or names != sorted(names):
        raise RuntimeError(f"{out}: merged member names are wrong or out of order")


def _untouchable(packed: Path, day: date, now: datetime) -> bool:
    """The hoarder may still write this day: it is today, still open, being packed, or packed too recently."""
    feed_dir = packed.parent
    if day >= now.date() or (feed_dir / f"{day}.tar").exists():
        return True
    if any(not p.name.endswith(TMP_SUFFIX) for p in feed_dir.glob(f"{day}.*tmp")):
        return True
    return packed.exists() and now.timestamp() - packed.stat().st_mtime < QUIET_S


def _record(conn: psycopg.Connection, result: RepairResult, **shas: str | None) -> RepairResult:
    if not result.dry_run:
        conn.execute(
            "insert into bronze.rt_repair (feed, archive_date, outcome, a_sha256, b_sha256, merged_sha256,"
            " members_added, gap_s_before, gap_s_after)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (feed, archive_date) do update set outcome = excluded.outcome,"
            " a_sha256 = excluded.a_sha256, b_sha256 = excluded.b_sha256, merged_sha256 = excluded.merged_sha256,"
            " members_added = excluded.members_added, gap_s_before = excluded.gap_s_before,"
            " gap_s_after = excluded.gap_s_after, decided_at = now()",
            (
                result.feed, result.archive_date, result.outcome,
                shas.get("a_sha256"), shas.get("b_sha256"), shas.get("merged_sha256"),
                result.members_added, result.gap_s_before, result.gap_s_after,
            ),
        )
    return result


def _install(conn: psycopg.Connection, result: RepairResult, tmp: Path, path: Path, **shas: str | None) -> RepairResult:
    """Intent row, rename, final row: a crash at any point leaves either A's file or the verified new one."""
    _fsync(tmp)
    shas["merged_sha256"] = file_sha256(tmp)
    _record(conn, replace(result, outcome=Outcome.REPAIRING), **shas)
    os.replace(tmp, path)
    _fsync(path.parent)
    return _record(conn, result, **shas)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def repair_day(
    conn: psycopg.Connection,
    root: Path,
    state: Path | None,
    feed: str,
    day: date,
    fetch_b: FetchB,
    *,
    now: datetime,
    dry_run: bool,
) -> RepairResult | None:
    """Decide one (feed, day) and, unless dry_run, act on it. None: final already, or the hoarder may still write it."""
    row = conn.execute(
        "select outcome, a_sha256, merged_sha256, members_added, gap_s_before, gap_s_after"
        " from bronze.rt_repair where feed = %s and archive_date = %s",
        (feed, day),
    ).fetchone()
    if row and row[0] in FINAL:
        return None
    path = archive_path(root, feed, day)
    if _untouchable(path, day, now):
        return None
    if row and row[0] == Outcome.REPAIRING and path.exists() and file_sha256(path) == row[2]:
        done = Outcome.COPIED if row[1] is None else Outcome.REPAIRED
        result = RepairResult(feed, day, done, *row[3:], dry_run=dry_run)
        return _record(conn, result, a_sha256=row[1], merged_sha256=row[2])

    a_sha = file_sha256(path) if path.exists() else None
    try:
        a_names = member_names(path) if a_sha else []
        gaps = find_gaps(stamps_of(a_names, day), day)
    except Malformed as e:
        log.warning("%s %s: A is malformed: %s", feed, day, e)
        return _record(conn, RepairResult(feed, day, Outcome.SKIPPED_MALFORMED, dry_run=dry_run), a_sha256=a_sha)
    gap_s = sum(g.seconds for g in gaps)
    if not gaps:
        return _record(conn, RepairResult(feed, day, Outcome.CLEAN, dry_run=dry_run), a_sha256=a_sha)

    b_path = fetch_b(feed, day)
    if b_path is None:
        late = Outcome.UNFILLABLE if (now.date() - day).days > LOOKBACK_DAYS else Outcome.WAITING
        return _record(conn, RepairResult(feed, day, late, 0, gap_s, gap_s, dry_run), a_sha256=a_sha)
    b_sha = file_sha256(b_path)
    try:
        plan = plan_repair(a_names, member_names(b_path), day)
    except Malformed as e:
        log.warning("%s %s: B is malformed: %s", feed, day, e)
        result = RepairResult(feed, day, Outcome.SKIPPED_MALFORMED, 0, gap_s, gap_s, dry_run)
        return _record(conn, result, a_sha256=a_sha, b_sha256=b_sha)
    if not plan.fill:
        result = RepairResult(feed, day, Outcome.NO_FILL, 0, gap_s, gap_s, dry_run)
        return _record(conn, result, a_sha256=a_sha, b_sha256=b_sha)

    done = Outcome.REPAIRED if a_sha else Outcome.COPIED
    result = RepairResult(feed, day, done, len(plan.fill), plan.gap_s_before, plan.gap_s_after, dry_run)
    if dry_run:
        log.info("%s %s: would add %d members, %s to %s", feed, day, len(plan.fill), plan.fill[0], plan.fill[-1])
        return result
    if state is None:
        raise ValueError("TRANSIT_STATE must be set to repair")
    tmp = path.with_name(path.name + TMP_SUFFIX)
    try:
        if a_sha:
            keep = state / "replaced" / feed / path.name
            keep.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, keep.with_name(keep.name + ".tmp"))
            _fsync(keep.with_name(keep.name + ".tmp"))
            os.replace(keep.with_name(keep.name + ".tmp"), keep)
            with tempfile.TemporaryDirectory(dir=state) as work:
                write_merged(path, b_path, plan.fill, tmp, Path(work))
        else:
            shutil.copyfile(b_path, tmp)
        return _install(conn, result, tmp, path, a_sha256=a_sha, b_sha256=b_sha)
    finally:
        tmp.unlink(missing_ok=True)
