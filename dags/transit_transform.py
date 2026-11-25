import json
import shlex
from datetime import timedelta

from airflow.sdk import Param, TriggerRule, dag, task
from lib.assets import BRONZE

DBT = "cd /opt/airflow/dbt && dbt"


@dag(
    schedule=[BRONZE],
    max_active_runs=1,
    default_args={"retries": 2, "retry_delay": timedelta(minutes=5)},
    params={
        "start_date": Param(None, type=["null", "string"], format="date"),
        "end_date": Param(None, type=["null", "string"], format="date"),
    },
    tags=["transit"],
)
def transit_transform():
    @task.bash
    def dbt_build(params=None) -> str:
        window = {"start_date": params["start_date"], "end_date": params["end_date"]}
        return f"{DBT} build --vars {shlex.quote(json.dumps(window))}"

    @task.bash(trigger_rule=TriggerRule.ALL_DONE, retries=0)
    def source_freshness() -> str:
        return f"{DBT} source freshness"

    dbt_build() >> source_freshness()


transit_transform()
