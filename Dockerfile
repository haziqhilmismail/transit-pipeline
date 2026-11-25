FROM apache/airflow:3.3.2-python3.12

# dbt and the loader get their own venv so Airflow's pinned packages stay untouched.
USER root
RUN mkdir -p /opt/transit && chown airflow: /opt/transit
USER airflow
COPY --chown=airflow: loader /tmp/loader
RUN uv venv /opt/transit \
 && uv pip install --python /opt/transit/bin/python --no-cache \
      "dbt-core~=1.12.0" "dbt-postgres>=1.9" "/tmp/loader[test]"
ENV PATH="${PATH}:/opt/transit/bin"
