import argparse
import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path

import psycopg

from transit_loader.db import connect
from transit_loader.feeds import REALTIME_FEEDS, Feed, archive_path
from transit_loader.realtime import load_realtime, missing_archive_dates
from transit_loader.repair import LOOKBACK_DAYS, FetchB, Mode, RepairResult, apply, decide, prune_replaced, read_ledger
from transit_loader.static import load_static

log = logging.getLogger("transit-load")

Job = Callable[[], object]


def realtime_jobs(conn: psycopg.Connection, root: Path, feed: Feed, args: argparse.Namespace) -> list[Job]:
    today = datetime.now(UTC).date()
    if args.date:
        dates = [args.date]
    else:
        earliest = date.fromisoformat(os.environ["TRANSIT_EARLIEST_DATE"])
        dates = missing_archive_dates(conn, root, feed, today_utc=today, earliest=earliest)
    return [partial(load_realtime, conn, root, feed, d, today_utc=today) for d in dates]


def static_jobs(conn: psycopg.Connection, root: Path, feed: Feed, args: argparse.Namespace) -> list[Job]:
    return [partial(load_static, conn, root, feed)]


def fetch_b_from_dir(feed: str, day: date) -> Path | None:
    # shortcut: B's archive is a local folder laid out like A's (B_DIR/realtime/<feed>/<date>.tar.xz); a download step replaces this.
    b_dir = os.environ.get("B_DIR")
    path = archive_path(Path(b_dir), feed, day) if b_dir else None
    return path if path and path.is_file() else None


def repair_jobs(conn: psycopg.Connection, root: Path, feed: str, args: argparse.Namespace) -> list[Job]:
    if args.mode == Mode.OFF:
        return []
    now = datetime.now(UTC)
    state = Path(os.environ["TRANSIT_STATE"]) if args.mode == Mode.ON else None
    days = [now.date() - timedelta(days=n) for n in range(1, args.days + 1)]
    jobs: list[Job] = [partial(repair_day, conn, root, feed, d, fetch_b_from_dir, now=now, state=state) for d in days]
    if state is None:
        log.info("repair %s: dry run, decisions are logged and not applied", feed)
        return jobs
    return [partial(prune_replaced, state, feed, now), *jobs]


def repair_day(
    conn: psycopg.Connection, root: Path, feed: str, day: date, fetch_b: FetchB, *, now: datetime, state: Path | None
) -> RepairResult | None:
    """Decide one day and apply the decision. state None is a dry run: decide only, write nothing."""
    decision = decide(read_ledger(conn, feed, day), root, feed, day, fetch_b, now=now)
    if decision is None or state is None:
        return decision and decision.result
    return apply(conn, state, decision)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transit-load")
    commands = parser.add_subparsers(dest="command", required=True)
    rt = commands.add_parser(
        "realtime",
        help="replace one closed UTC archive day per feed (--date), or load every closed day not loaded yet",
    )
    rt.add_argument("--date", type=date.fromisoformat)
    rt.add_argument("--feed", action="append", type=Feed, choices=REALTIME_FEEDS)
    rt.set_defaults(jobs=realtime_jobs, feeds=REALTIME_FEEDS)
    st = commands.add_parser("static", help="load every static version not loaded yet")
    st.add_argument("--feed", action="append", type=Feed, choices=list(Feed))
    st.set_defaults(jobs=static_jobs, feeds=list(Feed))
    rp = commands.add_parser("repair", help="fill outage gaps in closed archive days from collector B's copy")
    rp.add_argument("--mode", type=Mode, choices=list(Mode), default=os.environ.get("TRANSIT_REPAIR", "dry"))
    rp.add_argument("--days", type=int, default=LOOKBACK_DAYS + 1, help="closed days to visit, newest first")
    rp.add_argument("--feed", action="append")
    rp.set_defaults(jobs=repair_jobs, feeds=None)
    args = parser.parse_args(argv)
    if args.command == "repair" and args.mode == Mode.ON and "TRANSIT_STATE" not in os.environ:
        parser.error("repair --mode on needs TRANSIT_STATE, the folder that keeps A's original files")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(os.environ["TRANSIT_ARCHIVE_ROOT"])
    failed = 0
    with connect(os.environ["WAREHOUSE_DSN"]) as conn:
        feeds = args.feed or args.feeds or sorted(p.name for p in (root / "realtime").iterdir() if p.is_dir())
        for feed in feeds:
            # One job per archive day, so one bad day does not stop the days after it.
            for job in args.jobs(conn, root, feed, args):
                try:
                    if (result := job()) is not None:
                        log.info("%s", result)
                except Exception:
                    log.exception("%s %s failed", args.command, feed)
                    failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
