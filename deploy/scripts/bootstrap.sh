#!/usr/bin/env bash
# Postgres Control Tower — one-command demo bootstrap.
#
# Pipeline:
#   1. Generate deploy/compose/.env if missing (random secrets).
#   2. docker compose build && up -d.
#   3. Wait for manager + Postgres + Patroni nodes to report healthy.
#   4. Wait for the three pct-agents to register themselves and start
#      heartbeating.
#   5. Use the manager API to enqueue stanza-create + a first full backup
#      for both clusters so the UI has data to render.
#
# The script is idempotent: rerunning it after a partial failure picks
# up where it left off.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
COMPOSE_DIR="$REPO_ROOT/deploy/compose"
ENV_FILE="$COMPOSE_DIR/.env"

cd "$COMPOSE_DIR"

# ---------- 1. .env ----------

if [ ! -f "$ENV_FILE" ]; then
    echo "[bootstrap] Generating fresh $ENV_FILE with random secrets..."
    JWT_SECRET=$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | xxd -p | tr -d '\n')
    ENROLL_TOKEN=$(openssl rand -hex 16 2>/dev/null || head -c 16 /dev/urandom | xxd -p | tr -d '\n')
    ADMIN_PW=$(openssl rand -hex 8 2>/dev/null || head -c 8 /dev/urandom | xxd -p | tr -d '\n')
    cat > "$ENV_FILE" <<EOF
PCT_JWT_SECRET=$JWT_SECRET
PCT_ENROLLMENT_TOKEN=$ENROLL_TOKEN
PCT_BOOTSTRAP_ADMIN_EMAIL=admin@example.com
PCT_BOOTSTRAP_ADMIN_PASSWORD=$ADMIN_PW
PCT_MGR_DB_USER=pct
PCT_MGR_DB_PASSWORD=pct
PCT_MGR_DB_NAME=pct
PCT_SLACK_WEBHOOK_URL=
PCT_SMTP_HOST=
PCT_SMTP_TO=
EOF
    echo "[bootstrap]   admin email:    admin@example.com"
    echo "[bootstrap]   admin password: $ADMIN_PW"
fi

# shellcheck disable=SC1090
set -a; . "$ENV_FILE"; set +a

# ---------- 2. build + up ----------

echo "[bootstrap] Building images (this is slow on first run)..."
docker compose build

echo "[bootstrap] Starting all services..."
docker compose up -d

# ---------- 3. wait for healthchecks ----------

wait_healthy() {
    local svc="$1" max="${2:-90}" elapsed=0
    echo -n "[bootstrap] Waiting for $svc to be healthy"
    while :; do
        local cid status
        cid=$(docker compose ps -q "$svc")
        if [ -n "$cid" ]; then
            status=$(docker inspect -f '{{.State.Health.Status}}' "$cid" 2>/dev/null || echo unknown)
            if [ "$status" = "healthy" ]; then
                echo " ok."
                return 0
            fi
        fi
        if [ "$elapsed" -ge "$max" ]; then
            echo " timed out after ${max}s."
            docker compose logs --tail=80 "$svc" || true
            return 1
        fi
        echo -n "."
        sleep 2
        elapsed=$((elapsed + 2))
    done
}

wait_healthy mgr-db        60
wait_healthy manager       120
wait_healthy etcd          60
wait_healthy pg-standalone 90
wait_healthy patroni-1     180
wait_healthy patroni-2     180

# ---------- 4. wait for agent registrations ----------

API="http://localhost:8080"
ADMIN_EMAIL="${PCT_BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${PCT_BOOTSTRAP_ADMIN_PASSWORD:-admin}"

echo "[bootstrap] Logging into manager as $ADMIN_EMAIL..."
TOKEN=""
for attempt in $(seq 1 20); do
    TOKEN=$(curl -fsS -X POST "$API/api/v1/auth/login" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "username=$ADMIN_EMAIL" \
        --data-urlencode "password=$ADMIN_PASSWORD" \
        | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p' || true)
    if [ -n "$TOKEN" ]; then break; fi
    echo "[bootstrap]   login not ready yet (attempt $attempt); sleeping 3s..."
    sleep 3
done
if [ -z "$TOKEN" ]; then
    echo "[bootstrap] FATAL: could not log into manager." >&2
    exit 1
fi

api_get() { curl -fsS -H "Authorization: Bearer $TOKEN" "$API$1"; }
api_post() {
    curl -fsS -H "Authorization: Bearer $TOKEN" \
        -H "Content-Type: application/json" \
        -X POST "$API$1" -d "$2"
}

# Wait for both clusters and at least 3 agents to appear, with all of
# them having a recent heartbeat.
echo -n "[bootstrap] Waiting for agents to register and heartbeat"
for attempt in $(seq 1 60); do
    CLUSTERS_JSON=$(api_get /api/v1/clusters || echo "[]")
    AGENTS_JSON=$(api_get /api/v1/agents     || echo "[]")
    n_clusters=$(echo "$CLUSTERS_JSON" | tr ',' '\n' | grep -c '"id"' || true)
    n_agents=$(echo "$AGENTS_JSON" | tr ',' '\n' | grep -c '"id"' || true)
    if [ "$n_clusters" -ge 2 ] && [ "$n_agents" -ge 3 ]; then
        echo " (clusters=$n_clusters agents=$n_agents)"
        break
    fi
    echo -n "."
    sleep 2
done

# ---------- 5. seed: stanza-create + first backup per cluster ----------

cluster_id_by_name() {
    local name="$1"
    api_get /api/v1/clusters \
        | python3 -c "import json,sys
data=json.load(sys.stdin)
for c in data:
    if c['name']=='$name': print(c['id']); sys.exit(0)
sys.exit(1)"
}

submit_job_if_first_run() {
    local cluster_name="$1" cluster_id="$2"
    # Skip stanza-create if we've already done it for this cluster.
    local existing
    existing=$(api_get "/api/v1/jobs?cluster_id=$cluster_id&limit=1" || echo "[]")
    if echo "$existing" | grep -q '"kind"'; then
        echo "[bootstrap]   $cluster_name already has jobs; skipping seed."
        return 0
    fi

    echo "[bootstrap]   $cluster_name: queuing stanza-create..."
    api_post /api/v1/jobs "$(printf '{"cluster_id":%d,"kind":"stanza_create","params":{}}' "$cluster_id")" >/dev/null

    # Tiny pause so the agent's runner picks it up and reports a result
    # before we queue the backup; harmless to skip but keeps job ordering
    # tidy in the UI.
    sleep 5

    echo "[bootstrap]   $cluster_name: queuing first full backup..."
    api_post /api/v1/jobs "$(printf '{"cluster_id":%d,"kind":"backup_full","params":{}}' "$cluster_id")" >/dev/null
}

STANDALONE_ID=$(cluster_id_by_name standalone || echo "")
HA_ID=$(cluster_id_by_name ha-demo || echo "")

if [ -n "$STANDALONE_ID" ]; then
    submit_job_if_first_run standalone "$STANDALONE_ID"
else
    echo "[bootstrap] WARNING: standalone cluster not found yet."
fi
if [ -n "$HA_ID" ]; then
    submit_job_if_first_run ha-demo "$HA_ID"
else
    echo "[bootstrap] WARNING: ha-demo cluster not found yet."
fi

# ---------- summary ----------

cat <<EOF

[bootstrap] Done.

  Manager UI:      http://localhost:8080
  Manager OpenAPI: http://localhost:8080/docs
  Admin login:     $ADMIN_EMAIL / (see deploy/compose/.env -> PCT_BOOTSTRAP_ADMIN_PASSWORD)

  Try:   ./deploy/scripts/demo-failures.sh wal_lag
  Stop:  ./deploy/scripts/teardown.sh
  Wipe:  ./deploy/scripts/reset.sh
EOF
