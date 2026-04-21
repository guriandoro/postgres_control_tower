#!/usr/bin/env bash
# Trigger one of the demo failure scenarios so the operator can watch
# PCT detect, alert, and (where applicable) heal.
#
# Usage:
#   ./deploy/scripts/demo-failures.sh wal_lag            [standalone|ha-demo|all]
#   ./deploy/scripts/demo-failures.sh wal_slow_archive   [standalone|ha-demo|all]
#   ./deploy/scripts/demo-failures.sh wal_repo_break     [standalone|ha-demo|all]
#   ./deploy/scripts/demo-failures.sh wal_recover        [standalone|ha-demo|all]
#   ./deploy/scripts/demo-failures.sh failover
#   ./deploy/scripts/demo-failures.sh backup_fail
#   ./deploy/scripts/demo-failures.sh clock_drift
#   ./deploy/scripts/demo-failures.sh restore_attempt
#   ./deploy/scripts/demo-failures.sh restore   (alias)
#
# Each scenario prints what to look at in the UI after it runs.

set -euo pipefail
cd "$(dirname "$0")/../compose"
SCN="${1:-help}"
TARGET="${2:-all}"

# ---------- shared helpers ----------

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
        | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'
}

validate_target() {
    case "$1" in
        standalone|ha-demo|all) ;;
        *) echo "Unknown target '$1' (expected: standalone | ha-demo | all)" >&2; exit 2 ;;
    esac
}

# Resolve the current Patroni leader so we hit the writable node.
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

# Set archive_command on a cluster. For standalone we use ALTER SYSTEM
# (postgresql.auto.conf) + pg_reload_conf; for HA we go through Patroni's
# REST API so the change lands in DCS and isn't reverted on the next
# Patroni sync. ``$2`` is the literal SQL string to assign.
set_archive_command() {
    local cluster="$1" cmd="$2"
    case "$cluster" in
        standalone)
            docker compose exec -T -u postgres pg-standalone \
                psql -v ON_ERROR_STOP=1 -d postgres <<-SQL
                ALTER SYSTEM SET archive_command = '$cmd';
                SELECT pg_reload_conf();
SQL
            ;;
        ha-demo)
            local leader; leader=$(ha_leader)
            # PATCH merges; we only touch archive_command.
            docker compose exec -T patroni-1 curl -fsS -X PATCH \
                "http://${leader}:8008/config" \
                -H 'Content-Type: application/json' \
                -d "$(printf '{"postgresql":{"parameters":{"archive_command":"%s"}}}' "$cmd")" \
                >/dev/null
            # Patroni applies dynamic config within ~loop_wait (default 10s).
            ;;
        *) echo "set_archive_command: unsupported cluster '$cluster'" >&2; exit 2 ;;
    esac
}

# Same as above but for resetting the repo permissions / archive_command
# back to a working state. Standalone's postgresql.conf already carries
# the working value so RESET is enough; HA needs us to write the
# canonical command back through Patroni.
restore_archive_command() {
    local cluster="$1"
    case "$cluster" in
        standalone)
            docker compose exec -T -u postgres pg-standalone \
                psql -v ON_ERROR_STOP=1 -d postgres <<-'SQL'
                ALTER SYSTEM RESET archive_command;
                SELECT pg_reload_conf();
SQL
            ;;
        ha-demo)
            set_archive_command ha-demo \
                'pgbackrest --stanza=ha-demo archive-push %p'
            ;;
    esac
}

# Force one WAL switch on every primary in the cluster so something
# gets queued for the (now-broken) archive_command to chew on.
force_wal_switch() {
    local cluster="$1"
    case "$cluster" in
        standalone)
            docker compose exec -T -u postgres pg-standalone \
                psql -d postgres -c "SELECT pg_switch_wal();" >/dev/null
            ;;
        ha-demo)
            local leader; leader=$(ha_leader)
            docker compose exec -T -u postgres "$leader" \
                psql -d postgres -c "SELECT pg_switch_wal();" >/dev/null
            ;;
    esac
}

# Expand "all" → both clusters; otherwise just echo the single target.
each_target() {
    if [ "$1" = "all" ]; then
        echo standalone; echo ha-demo
    else
        echo "$1"
    fi
}

# ---------- scenarios ----------

case "$SCN" in
    wal_lag)
        validate_target "$TARGET"
        echo "[demo:wal_lag] Breaking archive_command on: $TARGET"
        # Point archive_command at /bin/false so every WAL segment fails.
        # archive_lag_seconds will climb past the 60s alert threshold and
        # gap_detected flips to true (last_failed_time > last_archived_time).
        for c in $(each_target "$TARGET"); do
            set_archive_command "$c" "/bin/false"
            force_wal_switch "$c"
        done
        cat <<EOF

  Wait ~2 minutes (one WAL probe @ 30s + one alerter pass @ 60s) and watch:

    UI -> Alerts:        new 'wal_lag' alert appears (warn after 60s, crit after 5m)
    UI -> Cluster page:  WAL archive lag chart climbs

  To recover (or run all of them at once):
    $0 wal_recover $TARGET
EOF
        ;;

    wal_slow_archive)
        validate_target "$TARGET"
        echo "[demo:wal_slow_archive] Slowing archive_command on: $TARGET"
        # Wrap the real archive command in a sleep so segments still
        # archive successfully (no gap_detected) but lag accumulates
        # because every push takes 30s. Demonstrates the time-based
        # threshold without the failure-based gap heuristic.
        for c in $(each_target "$TARGET"); do
            case "$c" in
                standalone) STANZA=demo ;;
                ha-demo)    STANZA=ha-demo ;;
            esac
            set_archive_command "$c" \
                "bash -c 'sleep 30; pgbackrest --stanza=$STANZA archive-push %p'"
            force_wal_switch "$c"
        done
        cat <<EOF

  archive-push will now take 30s per segment. With WAL traffic from
  demo-insert.sh the lag will outrun the archiver and climb steadily,
  but gap_detected stays false (every push eventually succeeds).

  Watch (~2 min):
    UI -> Cluster page:  WAL archive lag line ramps up, no gap badge
    UI -> Alerts:        'wal_lag' alert opens after threshold (60s)

  To recover:
    $0 wal_recover $TARGET
EOF
        ;;

    wal_repo_break)
        validate_target "$TARGET"
        echo "[demo:wal_repo_break] Removing pgBackRest repo write perms on: $TARGET"
        # Realistic outage: repo unwritable (full disk, perms snafu, NFS
        # blip). archive_command itself is unchanged, but pgbackrest
        # archive-push fails with "Permission denied". Differs from
        # wal_lag in that you'll see the actual pgbackrest error in the
        # logs tab — operationally more useful than /bin/false.
        for c in $(each_target "$TARGET"); do
            case "$c" in
                standalone) PG_SVC=pg-standalone ;;
                # On HA both nodes write archives via the shared repo
                # volume; chmod-ing on the leader is enough since only
                # the primary archives.
                ha-demo)    PG_SVC=$(ha_leader) ;;
            esac
            docker compose exec -T --user root "$PG_SVC" \
                chmod 0000 /var/lib/pgbackrest/archive
            force_wal_switch "$c"
        done
        cat <<EOF

  Watch (~2 min):
    UI -> Cluster page:  WAL archive lag climbs, gap_detected = true
    UI -> Logs (postgres / pgbackrest source):
        "ERROR: [047]: unable to open ... Permission denied"
    UI -> Alerts:        'wal_lag' alert opens after threshold (60s)

  To recover:
    $0 wal_recover $TARGET
EOF
        ;;

    wal_recover)
        validate_target "$TARGET"
        echo "[demo:wal_recover] Restoring WAL archival on: $TARGET"
        for c in $(each_target "$TARGET"); do
            case "$c" in
                standalone) PG_SVC=pg-standalone ;;
                ha-demo)    PG_SVC=$(ha_leader) ;;
            esac
            # Always undo perms first; chmod is a no-op when nothing's
            # broken so this is safe regardless of which scenario ran.
            docker compose exec -T --user root "$PG_SVC" \
                chmod 0750 /var/lib/pgbackrest/archive || true
            restore_archive_command "$c"
            force_wal_switch "$c"
        done
        cat <<EOF

  archive_command + repo perms restored. The next agent tick (~30s)
  should report lag dropping and gap_detected back to false. WAL
  segments queued during the outage will be flushed by the recovered
  archiver and the storage runway will jump accordingly.
EOF
        ;;

    failover)
        echo "[demo:failover] Stopping patroni-1 to force failover..."
        docker compose stop patroni-1
        cat <<EOF

  Patroni's leader race will promote patroni-2 within ~30s.
  Watch:
    UI -> Cluster ha-demo:  primary swaps from patroni-1 -> patroni-2
    UI -> Logs:             role transition rows ('promoted' / 'demoted')

  Restart the old node when you're ready:
    docker compose start patroni-1
EOF
        ;;

    backup_fail)
        echo "[demo:backup_fail] Queuing a backup against a broken stanza..."
        TOKEN=$(login_token)
        if [ -z "$TOKEN" ]; then echo "Login failed"; exit 1; fi
        # Make pgbackrest fail by removing repo write access for one moment;
        # easier: queue a job for an invalid stanza name. The job runner
        # forwards stdin and the agent reports exit_code != 0.
        STANDALONE_ID=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API/api/v1/clusters" \
            | python3 -c "import json,sys
for c in json.load(sys.stdin):
    if c['name']=='standalone': print(c['id']); break")
        curl -fsS -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -X POST "$API/api/v1/jobs" \
            -d "$(printf '{"cluster_id":%d,"kind":"backup_full","params":{"stanza":"does-not-exist"}}' "$STANDALONE_ID")" \
            >/dev/null
        cat <<EOF

  Watch:
    UI -> Jobs:    new 'backup_full' job, fails within ~30s
    UI -> Alerts:  'backup_failed' alert opens after the next eval pass
EOF
        ;;

    clock_drift)
        echo "[demo:clock_drift] Setting agent container clocks 10s in the past..."
        # The agent reports clock_skew_ms on each heartbeat; manager logs
        # the delta vs its own NOW(). Setting host clock backwards inside
        # one container reproduces clock_drift > 2000ms quickly.
        for svc in pct-agent-standalone pct-agent-patroni-1; do
            docker compose exec -T --user root "$svc" \
                date -u -s "@$(($(date -u +%s) - 10))" || true
        done
        cat <<EOF

  Watch:
    UI -> Alerts:  'clock_drift' alert opens after next eval (~60s)

  Note: the agent container's clock will eventually re-sync via the host;
  the alert will resolve on its own.
EOF
        ;;

    restore|restore_attempt)
        echo "[demo:restore_attempt] Trying to queue a 'restore' job..."
        TOKEN=$(login_token)
        if [ -z "$TOKEN" ]; then echo "Login failed"; exit 1; fi
        STANDALONE_ID=$(curl -fsS -H "Authorization: Bearer $TOKEN" "$API/api/v1/clusters" \
            | python3 -c "import json,sys
for c in json.load(sys.stdin):
    if c['name']=='standalone': print(c['id']); break")
        # Manager allowlist rejects anything not in JOB_KINDS — this MUST
        # 422. The point of the scenario is to demonstrate it.
        echo
        echo "Manager response (expecting HTTP 422):"
        curl -isS -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -X POST "$API/api/v1/jobs" \
            -d "$(printf '{"cluster_id":%d,"kind":"restore","params":{}}' "$STANDALONE_ID")" \
            | sed -n '1,15p'
        cat <<EOF

  -> The manager schema rejects 'restore' before it ever reaches an agent.
     Even if the manager were compromised, agent/pct_agent/runner.py
     keeps its own ALLOWED_KINDS allowlist (defense in depth).
EOF
        ;;

    help|*)
        cat <<EOF
Usage: $0 <scenario> [target]

Scenarios:
  wal_lag [target]         Break archive_command -> wal_lag alert + gap
  wal_slow_archive [tgt]   30s sleep wrapper -> lag climbs, no gap
  wal_repo_break [tgt]     chmod 000 the repo -> archive-push fails
  wal_recover [tgt]        Undo every WAL break above (chmod + reset)
  failover                 Stop patroni-1 -> Patroni promotes patroni-2
  backup_fail              Queue a backup with a bad stanza -> alert
  clock_drift              Skew an agent's clock by 10s -> clock_drift alert
  restore                  Try to queue a 'restore' job -> manager rejects (422)

target = standalone | ha-demo | all  (default: all, only for wal_* scenarios)
EOF
        exit 1
        ;;
esac
