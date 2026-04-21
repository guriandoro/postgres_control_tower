#!/usr/bin/env bash
# Insert synthetic rows into one or both demo clusters so subsequent
# pgBackRest backups have something to capture and the WAL stream gets
# real traffic. Idempotent: creates the demo schema if it doesn't exist.
#
# Usage:
#   ./deploy/scripts/demo-insert.sh                          # all, 10000 rows
#   ./deploy/scripts/demo-insert.sh standalone               # 10000 rows
#   ./deploy/scripts/demo-insert.sh ha-demo 50000            # 50k into HA
#   ./deploy/scripts/demo-insert.sh all 5000 --switch-wal    # also force WAL switches
#
# Cluster selector: standalone | ha-demo | all (default).
# Row count defaults to 10000.
#
# `--switch-wal` calls pg_switch_wal() once after the insert so a fresh
# WAL segment is queued for archival — handy when you want the agent's
# next probe to pick up movement.

set -euo pipefail
cd "$(dirname "$0")/../compose"

CLUSTER="${1:-all}"
ROWS="${2:-10000}"
SWITCH_WAL=0
for arg in "$@"; do
    [ "$arg" = "--switch-wal" ] && SWITCH_WAL=1
done

case "$CLUSTER" in
    standalone|ha-demo|all) ;;
    *) echo "Unknown cluster '$CLUSTER' (expected: standalone | ha-demo | all)" >&2; exit 1 ;;
esac
if ! [[ "$ROWS" =~ ^[0-9]+$ ]] || [ "$ROWS" -le 0 ]; then
    echo "Row count must be a positive integer, got '$ROWS'." >&2
    exit 1
fi

# Resolve the current Patroni leader by asking any node's REST API.
ha_leader() {
    docker compose exec -T patroni-1 curl -fsS http://patroni-1:8008/cluster \
        | python3 -c 'import json, sys
data = json.load(sys.stdin)
for m in data.get("members", []):
    if m.get("role") == "leader":
        print(m["name"])
        sys.exit(0)
sys.exit("no leader found")'
}

# Run a SQL block as the postgres superuser inside the chosen container.
psql_in() {
    local container="$1"
    docker compose exec -T -u postgres "$container" \
        psql -v ON_ERROR_STOP=1 -U postgres -d postgres
}

ensure_schema_and_insert() {
    local label="$1" container="$2" rows="$3"
    echo "[demo-insert] $label -> $container: ensuring schema and inserting $rows rows..."
    psql_in "$container" <<-SQL
        CREATE TABLE IF NOT EXISTS demo_orders (
            id          bigserial PRIMARY KEY,
            customer    text NOT NULL,
            amount_cents integer NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        );

        INSERT INTO demo_orders (customer, amount_cents)
        SELECT 'load-' || g, (random() * 10000)::int
        FROM generate_series(1, $rows) g;

        SELECT count(*) AS demo_orders_total FROM demo_orders;
SQL
    if [ "$SWITCH_WAL" -eq 1 ]; then
        echo "[demo-insert] $label: forcing pg_switch_wal()..."
        psql_in "$container" <<-'SQL'
            SELECT pg_switch_wal();
SQL
    fi
}

run_standalone() {
    ensure_schema_and_insert "standalone" pg-standalone "$ROWS"
}

run_ha() {
    local leader
    leader="$(ha_leader)"
    ensure_schema_and_insert "ha-demo" "$leader" "$ROWS"
}

case "$CLUSTER" in
    standalone) run_standalone ;;
    ha-demo)    run_ha ;;
    all)        run_standalone; run_ha ;;
esac

cat <<EOF

[demo-insert] Done. Watch in the UI:
    Cluster -> $([ "$CLUSTER" = all ] && echo "standalone & ha-demo" || echo "$CLUSTER")
      - WAL panel: last_archived_wal advances within ~30s
      - pgBackRest panel: next snapshot (~60s) reflects the new dataset size

  Queue an incremental backup to capture just these inserts:
    ./deploy/scripts/demo-backup.sh incr ${CLUSTER}
EOF
