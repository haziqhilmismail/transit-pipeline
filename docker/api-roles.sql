-- Roles for the public API (PostgREST). Run with `make api-roles`; safe to run again.
-- psql variable: pw (the authenticator password from .env). Empty: the roles are made, the password is left as it is.
\connect warehouse
select 'create role web_anon nologin' where not exists (select from pg_roles where rolname = 'web_anon') \gexec
select 'create role authenticator login noinherit' where not exists (select from pg_roles where rolname = 'authenticator') \gexec
alter role authenticator connection limit 5;
select :'pw' <> '' as has_pw \gset
\if :has_pw
alter role authenticator password :'pw';
\endif
grant web_anon to authenticator;
alter role web_anon set statement_timeout = '2s';
-- Not set: PostgREST runs each GET in a read-only transaction (its documented behavior), and web_anon has SELECT only.
alter role web_anon reset default_transaction_read_only;
create schema if not exists api;
grant usage on schema api to web_anon;
