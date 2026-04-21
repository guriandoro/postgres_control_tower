#!/bin/bash
# Custom wrapper around the upstream postgres image's docker-entrypoint.sh.
#
# Why we need it:
#   The pg-standalone service shares several named volumes with its sibling
#   pct-agent-standalone container (see deploy/compose/docker-compose.yml):
#     - pg-standalone-pglogs       -> /var/log/postgresql
#     - pgbr-standalone-repo       -> /var/lib/pgbackrest
#     - pgbr-standalone-logs       -> /var/log/pgbackrest
#     - pg-standalone-socket       -> /var/run/postgresql
#
#   compose creates all containers in parallel, and Docker initialises a
#   fresh named volume from whichever image's mount target gets mounted
#   first. If the agent container wins that race, the volume root inherits
#   the agent image's /var/log/ ownership (root:utmp 1775), and postgres
#   (uid 999) can't write there — boot fails with:
#     FATAL: could not open log file "...": Permission denied
#
#   Fix: chown the shared mount targets to postgres:postgres before handing
#   off to the upstream entrypoint. Idempotent and safe on subsequent boots.

set -e

if [ "$(id -u)" = "0" ]; then
    for d in /var/log/postgresql /var/log/pgbackrest /var/lib/pgbackrest; do
        if [ -d "$d" ]; then
            chown postgres:postgres "$d"
            chmod 0755 "$d"
        fi
    done
    if [ -d /var/run/postgresql ]; then
        chown postgres:postgres /var/run/postgresql
        chmod 2775 /var/run/postgresql
    fi
fi

exec /usr/local/bin/docker-entrypoint.sh "$@"
