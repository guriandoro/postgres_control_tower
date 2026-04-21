#!/bin/sh
# Manager entrypoint: run Alembic to head, then exec the app.
#
# We tolerate the manager DB not being ready yet — compose starts mgr-db
# in parallel and the manager retries connection during migration.
set -eu

cd /app/manager

if [ "${PCT_SKIP_MIGRATIONS:-0}" = "1" ]; then
    echo "entrypoint-manager: PCT_SKIP_MIGRATIONS=1, skipping alembic upgrade head."
else
    # Wait for the database to accept connections before alembic
    # starts. We poll with a short sleep so the bootstrap log is
    # readable and we never hot-loop.
    attempt=0
    until alembic upgrade head 2>&1; do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "entrypoint-manager: alembic upgrade head failed after $attempt attempts; giving up." >&2
            exit 1
        fi
        echo "entrypoint-manager: alembic not ready yet (attempt $attempt); sleeping 2s..."
        sleep 2
    done
fi

exec "$@"
