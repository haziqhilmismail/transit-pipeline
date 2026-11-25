import argparse
import logging
import os
from datetime import UTC, date, datetime
from pathlib import Path

from transit_loader.db import connect
from transit_loader.feeds import REALTIME_FEEDS, Feed
from transit_loader.realtime import load_realtime
from transit_loader.static import load_static

log = logging.getLogger("transit-load")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="transit-load")
    commands = parser.add_subparsers(dest="command", required=True)
    rt = commands.add_parser("realtime", help="replace one closed UTC archive day per feed")
    rt.add_argument("--date", required=True, type=date.fromisoformat)
    rt.add_argument("--feed", action="append", type=Feed, choices=REALTIME_FEEDS)
    st = commands.add_parser("static", help="load every static version not loaded yet")
    st.add_argument("--feed", action="append", type=Feed, choices=list(Feed))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    root = Path(os.environ["TRANSIT_ARCHIVE_ROOT"])
    failed = 0
    with connect(os.environ["WAREHOUSE_DSN"]) as conn:
        for feed in args.feed or (REALTIME_FEEDS if args.command == "realtime" else list(Feed)):
            try:
                if args.command == "realtime":
                    result = load_realtime(conn, root, feed, args.date, today_utc=datetime.now(UTC).date())
                else:
                    result = load_static(conn, root, feed)
                log.info("%s", result)
            except Exception:
                log.exception("%s %s failed", args.command, feed)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
