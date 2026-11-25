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
    def load_realtime(ds=None) -> str:
        # ds is the data interval start: the UTC archive day that closed just before this run.
        return f"transit-load realtime --date {ds}"

    load_static() >> load_realtime()


transit_ingest()
