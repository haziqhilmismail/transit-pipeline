FROM apache/airflow:3.3.2-python3.12

# dbt and the loader get their own venvs: Airflow's pinned packages stay untouched, and the loader's
# locked protobuf 7 conflicts with dbt's protobuf 6.
USER root
RUN mkdir -p /opt/dbt /opt/loader && chown airflow: /opt/dbt /opt/loader
USER airflow
RUN uv venv /opt/dbt \
 && uv pip install --python /opt/dbt/bin/python --no-cache "dbt-core~=1.12.0" "dbt-postgres~=1.11.0"
COPY --chown=airflow: loader /tmp/loader
RUN cd /tmp/loader && UV_PROJECT_ENVIRONMENT=/opt/loader uv sync --frozen --no-dev --no-editable --no-cache
ENV PATH="${PATH}:/opt/dbt/bin:/opt/loader/bin"
