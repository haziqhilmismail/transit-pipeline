from datetime import datetime, timedelta

from airflow.sdk import Asset, dag, task
from airflow.timetables.interval import CronDataIntervalTimetable

BRONZE = Asset("transit_bronze")


@dag(
    # The hoarder closes the UTC day's archive at about 00:08 UTC.
    schedule=CronDataIntervalTimetable("30 0 * * *", timezone="UTC"),
    start_date=datetime(2026, 11, 18),
    catchup=False,
    max_active_runs=1,
    default_args={"retries": 3, "retry_delay": timedelta(minutes=20)},
    tags=["transit"],
)
def transit_ingest():
    @task.bash
    def load_static() -> str:
        return "transit-load static"

    @task.bash(outlets=[BRONZE])
    def load_realtime(data_interval_start=None) -> str:
        # The interval [D 00:30, D+1 00:30) loads archive D, which closed at about D+1 00:08.
        return f"transit-load realtime --date {data_interval_start:%Y-%m-%d}"

    load_static() >> load_realtime()


transit_ingest()
