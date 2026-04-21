#!/bin/bash
# Runs once during initdb (the upstream postgres image executes anything
# in /docker-entrypoint-initdb.d/ as the postgres user with PGDATA initialized
# and a temporary local server already running).

set -euo pipefail

echo "01-init: creating demo database and seed data..."

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-'SQL'
    -- A trivial schema so the standalone has _something_ to back up.
    CREATE TABLE IF NOT EXISTS demo_orders (
        id          bigserial PRIMARY KEY,
        customer    text NOT NULL,
        amount_cents integer NOT NULL,
        created_at  timestamptz NOT NULL DEFAULT now()
    );

    INSERT INTO demo_orders (customer, amount_cents)
    SELECT 'cust-' || g, (random() * 10000)::int
    FROM generate_series(1, 1000) g;
SQL

echo "01-init: done."
