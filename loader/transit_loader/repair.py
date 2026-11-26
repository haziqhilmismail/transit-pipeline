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
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from transit_loader.feeds import archive_members, archive_path, file_sha256, parse_stamp

log = logging.getLogger(__name__)

GAP_S = 600
NEAR_S = 60
LOOKBACK_DAYS = 7
KEEP_DAYS = 30
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


@dataclass(frozen=True, kw_only=True)
class RepairResult:
    """One (feed, day) decision, as stored in bronze.rt_repair."""

    feed: str
    archive_date: date
    outcome: Outcome
    members_added: int = 0
    gap_s_before: int = 0
    gap_s_after: int = 0
    a_sha256: str | None = None
    b_sha256: str | None = None
    merged_sha256: str | None = None


@dataclass(frozen=True)
class Decision:
    result: RepairResult
    path: Path
    b_path: Path | None = None
    """B's file to merge or copy in; None means there is nothing to write, only the ledger row."""
    fill: tuple[str, ...] = ()


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
    kept = 0
    for member in merged:
        if kept < len(a_seq) and member == a_seq[kept]:
            kept += 1
    if kept != len(a_seq):
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


def read_ledger(conn: psycopg.Connection, feed: str, day: date) -> RepairResult | None:
    with conn.cursor(row_factory=dict_row) as cur:
        row = cur.execute(
            "select feed, archive_date, outcome, members_added, gap_s_before, gap_s_after,"
            " a_sha256, b_sha256, merged_sha256 from bronze.rt_repair where feed = %s and archive_date = %s",
            (feed, day),
        ).fetchone()
    return RepairResult(**{**row, "outcome": Outcome(row["outcome"])}) if row else None


def decide(
    prior: RepairResult | None, root: Path, feed: str, day: date, fetch_b: FetchB, *, now: datetime
) -> Decision | None:
    """What to do with one (feed, day), from the ledger row and the files; writes nothing.

    None: the row is final already, or the hoarder may still write the day.
    """
    if prior and prior.outcome in FINAL:
        return None
    path = archive_path(root, feed, day)
    if _untouchable(path, day, now):
        return None
    if prior and prior.outcome == Outcome.REPAIRING and path.exists() and file_sha256(path) == prior.merged_sha256:
        done = Outcome.COPIED if prior.a_sha256 is None else Outcome.REPAIRED
        return Decision(replace(prior, outcome=done), path)

    a_sha = file_sha256(path) if path.exists() else None
    try:
        a_names = member_names(path) if a_sha else []
        gaps = find_gaps(stamps_of(a_names, day), day)
    except Malformed as e:
        log.warning("%s %s: A is malformed: %s", feed, day, e)
        return Decision(RepairResult(feed=feed, archive_date=day, outcome=Outcome.SKIPPED_MALFORMED, a_sha256=a_sha), path)
    if not gaps:
        return Decision(RepairResult(feed=feed, archive_date=day, outcome=Outcome.CLEAN, a_sha256=a_sha), path)
    gap_s = sum(g.seconds for g in gaps)

    b_path = fetch_b(feed, day)
    if b_path is None:
        late = Outcome.UNFILLABLE if (now.date() - day).days > LOOKBACK_DAYS else Outcome.WAITING
        result = RepairResult(
            feed=feed, archive_date=day, outcome=late, gap_s_before=gap_s, gap_s_after=gap_s, a_sha256=a_sha
        )
        return Decision(result, path)
    unchanged = RepairResult(
        feed=feed, archive_date=day, outcome=Outcome.NO_FILL, gap_s_before=gap_s, gap_s_after=gap_s,
        a_sha256=a_sha, b_sha256=file_sha256(b_path),
    )
    try:
        b_names = member_names(b_path)
        plan = plan_repair(a_names, b_names, day)
    except Malformed as e:
        log.warning("%s %s: B is malformed: %s", feed, day, e)
        return Decision(replace(unchanged, outcome=Outcome.SKIPPED_MALFORMED), path)
    if not plan.fill:
        return Decision(unchanged, path)
    if a_sha is None:
        b_gap_s = sum(g.seconds for g in find_gaps(stamps_of(b_names, day), day))
        result = replace(unchanged, outcome=Outcome.COPIED, members_added=len(b_names), gap_s_after=b_gap_s)
        return Decision(result, path, b_path)
    result = replace(
        unchanged,
        outcome=Outcome.REPAIRED,
        members_added=len(plan.fill),
        gap_s_before=plan.gap_s_before,
        gap_s_after=plan.gap_s_after,
    )
    return Decision(result, path, b_path, plan.fill)


def apply(conn: psycopg.Connection, state: Path, decision: Decision) -> RepairResult:
    """Record the decision and, when it has B's file, write the day. A's original is kept under `state`."""
    result, path, b_path = decision.result, decision.path, decision.b_path
    if b_path is None:
        return _record(conn, result)
    if result.outcome == Outcome.COPIED:
        return _install(conn, result, b_path, path)
    _durable_copy(path, state / "replaced" / result.feed / path.name)
    with tempfile.TemporaryDirectory(dir=state) as work:
        merged = Path(work) / path.name
        write_merged(path, b_path, decision.fill, merged, Path(work))
        return _install(conn, result, merged, path)


def prune_replaced(state: Path, feed: str, now: datetime) -> None:
    """Delete A's originals kept more than KEEP_DAYS ago; a kept copy's mtime is the time it was kept."""
    cutoff = (now - timedelta(days=KEEP_DAYS)).timestamp()
    for kept in (state / "replaced" / feed).glob("*.tar.xz"):
        if kept.stat().st_mtime < cutoff:
            log.info("prune %s", kept)
            kept.unlink()


def _record(conn: psycopg.Connection, result: RepairResult) -> RepairResult:
    conn.execute(
        "insert into bronze.rt_repair (feed, archive_date, outcome, a_sha256, b_sha256, merged_sha256,"
        " members_added, gap_s_before, gap_s_after)"
        " values (%(feed)s, %(archive_date)s, %(outcome)s, %(a_sha256)s, %(b_sha256)s, %(merged_sha256)s,"
        " %(members_added)s, %(gap_s_before)s, %(gap_s_after)s)"
        " on conflict (feed, archive_date) do update set outcome = excluded.outcome,"
        " a_sha256 = excluded.a_sha256, b_sha256 = excluded.b_sha256, merged_sha256 = excluded.merged_sha256,"
        " members_added = excluded.members_added, gap_s_before = excluded.gap_s_before,"
        " gap_s_after = excluded.gap_s_after, decided_at = now()",
        asdict(result),
    )
    return result


def _install(conn: psycopg.Connection, result: RepairResult, src: Path, path: Path) -> RepairResult:
    """Intent row, then rename: a crash at any point leaves either A's file or the verified new one."""
    result = replace(result, merged_sha256=file_sha256(src))
    _record(conn, replace(result, outcome=Outcome.REPAIRING))
    _durable_copy(src, path)
    return _record(conn, result)


def _durable_copy(src: Path, dst: Path) -> None:
    """Copy, fsync, rename into place, fsync the folder. dst's mtime is the rename time, which the loader's mtime check needs."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + TMP_SUFFIX)
    try:
        shutil.copyfile(src, tmp)
        _fsync(tmp)
        os.replace(tmp, dst)
        os.utime(dst)
    finally:
        tmp.unlink(missing_ok=True)
    _fsync(dst.parent)


def _fsync(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
