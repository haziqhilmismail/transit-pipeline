include .env
export

COMPOSE := docker compose
EXEC := $(COMPOSE) exec -T airflow-scheduler
DBT = $(EXEC) bash -c 'cd /opt/airflow/dbt && dbt $(1)'
HOST_DSN := postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@127.0.0.1:5442/warehouse
comma := ,

.PHONY: up down test backfill dbt-build docs psql logs stats

up:
	$(COMPOSE) up -d --build --wait

down:
	$(COMPOSE) down

# dbt unit tests need one finished `make dbt-build` (they read parent columns).
test:
	WAREHOUSE_DSN=$(HOST_DSN) uv run --project loader --extra test pytest -q loader/tests
	$(call DBT,test --select test_type:unit)

backfill:
	@test -n "$(FROM)" -a -n "$(TO)" || (echo "usage: make backfill FROM=YYYY-MM-DD TO=YYYY-MM-DD" && exit 2)
	$(EXEC) airflow backfill create --dag-id transit_ingest --from-date $(FROM) --to-date $(TO)T23:59:59 \
		--max-active-runs 1 --reprocess-behavior completed

dbt-build:
	$(call DBT,build $(if $(FROM),--vars \"{start_date: $(FROM)$(comma) end_date: $(TO)}\"))

docs:
	$(call DBT,docs generate --static)
	@echo "open dbt/target/static_index.html"

psql:
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) warehouse

logs:
	$(COMPOSE) logs -f --tail 100

stats:
	docker stats --no-stream $$($(COMPOSE) ps -q)
