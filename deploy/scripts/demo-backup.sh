#!/usr/bin/env bash
# Queue a pgBackRest job through the manager API for one or both demo
# clusters and (optionally) wait for it to finish, printing the result.
#
# Usage:
#   ./deploy/scripts/demo-backup.sh full                          # all clusters
#   ./deploy/scripts/demo-backup.sh diff standalone
#   ./deploy/scripts/demo-backup.sh incr ha-demo
#   ./deploy/scripts/demo-backup.sh check all --no-wait           # fire-and-forget
#
# Kind:    full | diff | incr | check | stanza_create
# Cluster: standalone | ha-demo | all (default).
#
# By default the script polls /api/v1/jobs/{id} every 3s for up to ~10
# minutes per job and prints status + tail. Pass --no-wait to return
# immediately after enqueueing.
#
# Why go through the manager rather than `docker exec pgbackrest backup`?
#   - Exercises the full Safe Ops path (auth -> jobs queue -> agent
#     long-poll -> agent runner -> result POST) so the UI's Jobs page
#     reflects everything.
#   - Keeps the kind allowlist in one place (manager + agent).

set -euo pipefail
cd "$(dirname "$0")/../compose"

KIND="${1:-help}"
CLUSTER="${2:-all}"
WAIT=1
for arg in "$@"; do
    [ "$arg" = "--no-wait" ] && WAIT=0
done

case "$KIND" in
    full)          JOB_KIND="backup_full" ;;
    diff)          JOB_KIND="backup_diff" ;;
    incr)          JOB_KIND="backup_incr" ;;
    check)         JOB_KIND="check" ;;
    stanza_create) JOB_KIND="stanza_create" ;;
    help|*)
        cat <<EOF
Usage: $0 <kind> [cluster] [--no-wait]

Kinds:    full | diff | incr | check | stanza_create
Clusters: standalone | ha-demo | all  (default: all)

Examples:
  $0 full                # full backup of both clusters, wait for results
  $0 incr ha-demo        # incremental on the HA cluster only
  $0 check all --no-wait # queue check on both, return immediately
EOF
        exit 1
        ;;
esac

case "$CLUSTER" in
    standalone|ha-demo|all) ;;
    *) echo "Unknown cluster '$CLUSTER' (expected: standalone | ha-demo | all)" >&2; exit 1 ;;
esac

# ---------- env + auth ----------

API="http://localhost:8080"
ENV_FILE="$(pwd)/.env"
if [ -f "$ENV_FILE" ]; then
    # shellcheck disable=SC1090
    set -a; . "$ENV_FILE"; set +a
fi
ADMIN_EMAIL="${PCT_BOOTSTRAP_ADMIN_EMAIL:-admin@example.com}"
ADMIN_PASSWORD="${PCT_BOOTSTRAP_ADMIN_PASSWORD:-admin}"

login_token() {
    curl -fsS -X POST "$API/api/v1/auth/login" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "username=$ADMIN_EMAIL" \
        --data-urlencode "password=$ADMIN_PASSWORD" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["access_token"])'
}

TOKEN="$(login_token)"
if [ -z "$TOKEN" ]; then
    echo "[demo-backup] FATAL: could not log into manager." >&2
    exit 1
fi

api() { curl -fsS -H "Authorization: Bearer $TOKEN" "$@"; }

# ---------- helpers ----------

cluster_id_by_name() {
    local name="$1"
    api "$API/api/v1/clusters" \
        | python3 -c "import json, sys
for c in json.load(sys.stdin):
    if c['name'] == '$name':
        print(c['id'])
        sys.exit(0)
sys.exit('cluster $name not found')"
}

queue_job() {
    local cluster_id="$1"
    api -H "Content-Type: application/json" -X POST "$API/api/v1/jobs" \
        -d "$(printf '{"cluster_id":%d,"kind":"%s","params":{}}' \
                "$cluster_id" "$JOB_KIND")" \
        | python3 -c 'import json, sys; print(json.load(sys.stdin)["id"])'
}

wait_for_job() {
    local job_id="$1" cluster_name="$2"
    local elapsed=0 max=600
    echo -n "[demo-backup] $cluster_name: job #$job_id "
    while :; do
        local payload status exit_code
        payload="$(api "$API/api/v1/jobs/$job_id")"
        status=$(echo "$payload" | python3 -c 'import json, sys; print(json.load(sys.stdin)["status"])')
        case "$status" in
            succeeded|failed)
                exit_code=$(echo "$payload" | python3 -c 'import json, sys; print(json.load(sys.stdin).get("exit_code"))')
                echo " -> $status (exit=$exit_code)"
                echo "$payload" | python3 -c 'import json, sys
j = json.load(sys.stdin)
tail = (j.get("stdout_tail") or "").strip().splitlines()
print("    " + ("\n    ".join(tail[-12:]) if tail else "(no output captured)"))'
                [ "$status" = "succeeded" ] && return 0 || return 1
                ;;
            pending|running)
                echo -n "."
                ;;
            *)
                echo " -> unexpected status '$status'; aborting wait."
                return 2
                ;;
        esac
        if [ "$elapsed" -ge "$max" ]; then
            echo " timed out after ${max}s; check the UI / docker logs."
            return 124
        fi
        sleep 3
        elapsed=$((elapsed + 3))
    done
}

run_one() {
    local cluster_name="$1" cluster_id job_id
    cluster_id="$(cluster_id_by_name "$cluster_name")"
    job_id="$(queue_job "$cluster_id")"
    echo "[demo-backup] $cluster_name (id=$cluster_id): queued $JOB_KIND as job #$job_id"
    if [ "$WAIT" -eq 1 ]; then
        wait_for_job "$job_id" "$cluster_name" || true
    fi
}

# ---------- main ----------

case "$CLUSTER" in
    standalone) run_one standalone ;;
    ha-demo)    run_one ha-demo ;;
    all)        run_one standalone; run_one ha-demo ;;
esac

if [ "$WAIT" -eq 0 ]; then
    cat <<EOF

[demo-backup] Jobs queued (--no-wait). Track them at:
  curl -H "Authorization: Bearer \$TOKEN" "$API/api/v1/jobs?limit=10"
or in the UI under Jobs.
EOF
fi
