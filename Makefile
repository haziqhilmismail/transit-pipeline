include .env
export

COMPOSE := docker compose
EXEC := $(COMPOSE) exec -T airflow-scheduler
DBT = $(COMPOSE) exec -T -w /opt/airflow/dbt airflow-scheduler dbt $(1)
HOST_DSN := postgresql://$(POSTGRES_USER):$(POSTGRES_PASSWORD)@127.0.0.1:5442/warehouse
comma := ,

.PHONY: up api-roles down test backfill dbt-build docs psql logs stats

up:
	@test -n "$(API_DB_PASSWORD)" -a -n "$(STATS_SITE_HOSTS)" || (echo "set API_DB_PASSWORD and STATS_SITE_HOSTS in .env" && exit 2)
	$(COMPOSE) up -d --wait postgres
	$(MAKE) api-roles
	$(COMPOSE) --profile stats up -d --build --wait

# Roles for PostgREST. Idempotent: run it again after a password change in .env.
# dbt grants SELECT on the api tables to web_anon, so dbt-build needs the roles first.
api-roles:
	@$(COMPOSE) exec -T postgres psql -q -v ON_ERROR_STOP=1 -v pw="$(API_DB_PASSWORD)" -U $(POSTGRES_USER) -d airflow < docker/api-roles.sql

down:
	$(COMPOSE) --profile stats down

# dbt unit tests need one finished `make dbt-build` (they read parent columns).
test:
	WAREHOUSE_DSN=$(HOST_DSN) uv run --project loader --extra test pytest -q loader/tests
	$(call DBT,test --select test_type:unit)

backfill:
	@test -n "$(FROM)" -a -n "$(TO)" || (echo "usage: make backfill FROM=YYYY-MM-DD TO=YYYY-MM-DD" && exit 2)
	$(EXEC) airflow backfill create --dag-id transit_ingest --from-date $(FROM) --to-date $(TO)T23:59:59 \
		--max-active-runs 1 --reprocess-behavior completed

dbt-build: api-roles
	$(call DBT,build --vars '{"start_date": "$(FROM)"$(comma) "end_date": "$(TO)"}')

docs:
	$(call DBT,docs generate --static)
	@echo "open dbt/target/static_index.html"

psql:
	$(COMPOSE) exec postgres psql -U $(POSTGRES_USER) warehouse

logs:
	$(COMPOSE) --profile stats logs -f --tail 100

stats:
	docker stats --no-stream $$($(COMPOSE) ps -q)
