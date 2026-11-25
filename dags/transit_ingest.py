from datetime import datetime, timedelta

from airflow.sdk import TriggerRule, dag, task
from airflow.timetables.interval import CronDataIntervalTimetable
from lib.assets import BRONZE


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

    # Realtime does not need static to load; static only runs first so dbt sees both.
    @task.bash(outlets=[BRONZE], trigger_rule=TriggerRule.ALL_DONE)
    def load_realtime(dag_run=None, data_interval_start=None) -> str:
        if dag_run.run_type == "backfill":
            return f"transit-load realtime --date {data_interval_start:%Y-%m-%d}"
        return "transit-load realtime"

    load_static() >> load_realtime()


transit_ingest()
