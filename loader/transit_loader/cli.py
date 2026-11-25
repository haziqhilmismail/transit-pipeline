import argparse
import logging
import os
from collections.abc import Callable
from datetime import UTC, date, datetime
from functools import partial
from pathlib import Path

import psycopg

from transit_loader.db import connect
from transit_loader.feeds import REALTIME_FEEDS, Feed
from transit_loader.realtime import load_realtime, missing_archive_dates
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
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(os.environ["TRANSIT_ARCHIVE_ROOT"])
    failed = 0
    with connect(os.environ["WAREHOUSE_DSN"]) as conn:
        for feed in args.feed or args.feeds:
            # One job per archive day, so one bad day does not stop the days after it.
            for job in args.jobs(conn, root, feed, args):
                try:
                    log.info("%s", job())
                except Exception:
                    log.exception("%s %s failed", args.command, feed)
                    failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
