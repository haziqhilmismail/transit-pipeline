-- Roles for the public API (PostgREST). Run with `make api-roles`; safe to run again.
-- psql variable: pw (the authenticator password from .env).
\connect warehouse
select 'create role web_anon nologin' where not exists (select from pg_roles where rolname = 'web_anon') \gexec
select 'create role authenticator login noinherit' where not exists (select from pg_roles where rolname = 'authenticator') \gexec
alter role authenticator connection limit 5 password :'pw';
grant web_anon to authenticator;
alter role web_anon set statement_timeout = '2s';
alter role web_anon set default_transaction_read_only = on;
create schema if not exists api;
grant usage on schema api to web_anon;
