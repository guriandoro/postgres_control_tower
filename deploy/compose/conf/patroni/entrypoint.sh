#!/bin/bash
# Patroni entrypoint: render the YAML from env vars, fix ownership on
# mounted volumes, then exec patroni as the postgres user.
#
# Required env vars (compose sets them per-service):
#   PATRONI_SCOPE        — cluster name (e.g. "ha-demo")
#   PATRONI_NAME         — node name   (e.g. "patroni-1")
#   PATRONI_ETCD_HOSTS   — comma-separated etcd peers (e.g. "etcd:2379")

set -euo pipefail

: "${PATRONI_SCOPE:?must be set}"
: "${PATRONI_NAME:?must be set}"
: "${PATRONI_ETCD_HOSTS:?must be set}"

export PATRONI_SCOPE PATRONI_NAME PATRONI_ETCD_HOSTS

envsubst < /etc/patroni/patroni.yml.tmpl > /etc/patroni/patroni.yml
chown postgres:postgres /etc/patroni/patroni.yml

# Mounted volumes are root-owned on first attach. The PG data dir must
# be 0700 / postgres:postgres or PG refuses to start.
install -d -o postgres -g postgres -m 0700 "$PGDATA"
install -d -o postgres -g postgres -m 0755 \
    "$PG_LOG_DIR" "$PATRONI_LOG_DIR" "$PGBACKREST_LOG_DIR" \
    /var/run/postgresql
install -d -o postgres -g postgres -m 0750 "$PGBACKREST_REPO"

# pgbackrest config holds plaintext-ish repo paths only; readable by
# postgres is enough.
chown -R postgres:postgres /etc/pgbackrest

echo "entrypoint-patroni: starting patroni as $PATRONI_NAME (scope=$PATRONI_SCOPE)"
exec gosu postgres "$@"
