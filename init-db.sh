#!/bin/sh
set -eu
psql -v ON_ERROR_STOP=1 -U postgres -d schedule <<'SQL'
\getenv collector_password COLLECTOR_PASSWORD
CREATE ROLE collector LOGIN PASSWORD :'collector_password';
CREATE ROLE schedule_reader LOGIN;
ALTER DATABASE schedule OWNER TO collector;
GRANT USAGE ON SCHEMA public TO schedule_reader;
ALTER DEFAULT PRIVILEGES FOR ROLE collector IN SCHEMA public
    GRANT SELECT ON TABLES TO schedule_reader;
SQL
