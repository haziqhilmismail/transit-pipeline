import psycopg

BRONZE_DDL = """
create schema if not exists bronze;

create table if not exists bronze.rt_archive (
    feed text not null,
    archive_date date not null,
    archive_sha256 text not null,
    n_snapshots int not null,
    n_bad_snapshots int not null,
    n_positions int not null,
    loaded_at timestamptz not null default now(),
    primary key (feed, archive_date)
);

create table if not exists bronze.rt_snapshot (
    feed text not null,
    fetched_at timestamptz not null,
    archive_date date not null,
    header_ts bigint,
    n_entities int not null,
    primary key (feed, fetched_at)
);

create table if not exists bronze.rt_vehicle_position (
    feed text not null,
    fetched_at timestamptz not null,
    archive_date date not null,
    entity_id text not null,
    vehicle_id text,
    vehicle_label text,
    license_plate text,
    trip_id text,
    route_id text,
    start_date text,
    start_time text,
    direction_id int,
    latitude double precision,
    longitude double precision,
    bearing real,
    speed real,
    vehicle_ts bigint
);
create index if not exists rt_vehicle_position_day on bronze.rt_vehicle_position (archive_date, feed);

create table if not exists bronze.static_version (
    feed text not null,
    version_id text not null,
    fetched_at timestamptz not null,
    zip_sha256 text not null,
    loaded_at timestamptz not null default now(),
    primary key (feed, version_id)
);

create table if not exists bronze.static_version_file (
    feed text not null,
    version_id text not null,
    file_name text not null,
    file_sha256 text not null,
    primary key (feed, version_id, file_name)
);
create index if not exists static_version_file_sha on bronze.static_version_file (file_sha256);

create table if not exists bronze.rt_repair (
    feed text not null,
    archive_date date not null,
    outcome text not null check (outcome in (
        'clean', 'repaired', 'copied', 'no_fill', 'unfillable', 'skipped_malformed', 'waiting', 'repairing'
    )),
    a_sha256 text,
    b_sha256 text,
    merged_sha256 text,
    members_added int not null default 0,
    gap_s_before int not null default 0,
    gap_s_after int not null default 0,
    decided_at timestamptz not null default now(),
    primary key (feed, archive_date)
);

create table if not exists bronze.gtfs_row (
    file_sha256 text not null,
    row_num int not null,
    data jsonb not null,
    primary key (file_sha256, row_num)
);
"""


def connect(dsn: str) -> psycopg.Connection:
    conn = psycopg.connect(dsn, autocommit=True)
    with conn.transaction():
        conn.execute(BRONZE_DDL)
    return conn
