-- Personal Cloud database bootstrap: role, database, privileges, extensions.
-- FIRST step of any database setup; run as a superuser against the maintenance
-- database (usually "postgres"), then migrations:
--
--   psql "postgresql://<superuser>@localhost:5432/postgres" \
--        -v db_name=personal_cloud -v db_user=cloud -v db_password=secret \
--        -f database/postgres/init_db.sql
--   python database/scripts/migrate.py up
--
-- `make db-init` does all of this with the values from DATABASE_URL in .env.
-- Idempotent: safe to run again on an existing database.

\set ON_ERROR_STOP on

\if :{?db_name}     \else \echo 'init_db: pass -v db_name=...'     \quit \endif
\if :{?db_user}     \else \echo 'init_db: pass -v db_user=...'     \quit \endif
\if :{?db_password} \else \echo 'init_db: pass -v db_password=...' \quit \endif

\echo '== init_db: role' :db_user ', database' :db_name

-- 1. Application role (login user). Created once, password kept in sync afterwards.
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_user')
\gexec

SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'db_user', :'db_password')
\gexec

-- 2. Database, owned by that role.
SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name')
\gexec

GRANT ALL PRIVILEGES ON DATABASE :"db_name" TO :"db_user";

-- 3. Everything below applies inside the new database.
\connect :"db_name"

ALTER SCHEMA public OWNER TO :"db_user";
GRANT ALL ON SCHEMA public TO :"db_user";
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO :"db_user";
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO :"db_user";
GRANT ALL PRIVILEGES ON ALL FUNCTIONS IN SCHEMA public TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO :"db_user";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON FUNCTIONS TO :"db_user";

-- 4. Extensions need superuser rights on most servers, so they are created here.
--    pg_trgm: fuzzy file-name search.
CREATE EXTENSION IF NOT EXISTS pg_trgm;

\echo '== init_db: done'
