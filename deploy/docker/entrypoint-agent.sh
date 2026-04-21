#!/bin/sh
# Agent entrypoint: ensure runtime dirs exist, then drop privileges and exec.
#
# The agent registers itself on first boot if PCT_AGENT_ENROLLMENT_TOKEN
# and PCT_AGENT_CLUSTER_NAME are set and no token has been persisted yet.
# Subsequent boots see the token in /var/lib/pct-agent/state.json and skip
# registration.

set -eu

STATE_DIR=${PCT_AGENT_STATE_DIR:-/var/lib/pct-agent}
STATE_FILE=${PCT_AGENT_STATE:-${STATE_DIR}/state.json}
SPOOL_DIR=${PCT_AGENT_SPOOL_DIR:-${STATE_DIR}/spool}

# Make sure mounted volumes have the right ownership for our unprivileged
# user. Bind mounts default to root-owned on first attach.
install -d -o pct-agent -g pct-agent -m 0750 "$STATE_DIR" "$SPOOL_DIR"
if [ -f "$STATE_FILE" ]; then
    chown pct-agent:pct-agent "$STATE_FILE" 2>/dev/null || true
fi

# One-shot registration: only when we have a token AND we don't already have
# state. Failures here are fatal because everything downstream depends on
# the bearer token landing in state.json.
if [ ! -f "$STATE_FILE" ] && [ -n "${PCT_AGENT_ENROLLMENT_TOKEN:-}" ] \
    && [ -n "${PCT_AGENT_CLUSTER_NAME:-}" ]; then

    HOSTNAME_ARG=${PCT_AGENT_HOSTNAME:-$(hostname)}
    echo "entrypoint-agent: registering with manager at ${PCT_AGENT_MANAGER_URL:-http://manager:8080}"

    attempt=0
    until gosu pct-agent pct-agent register \
        --manager-url "${PCT_AGENT_MANAGER_URL:-http://manager:8080}" \
        --enrollment-token "$PCT_AGENT_ENROLLMENT_TOKEN" \
        --cluster-name "$PCT_AGENT_CLUSTER_NAME" \
        --cluster-kind "${PCT_AGENT_CLUSTER_KIND:-standalone}" \
        --hostname "$HOSTNAME_ARG"
    do
        attempt=$((attempt + 1))
        if [ "$attempt" -ge 30 ]; then
            echo "entrypoint-agent: register failed after $attempt attempts; giving up." >&2
            exit 1
        fi
        echo "entrypoint-agent: register attempt $attempt failed; sleeping 2s..."
        sleep 2
    done
fi

exec gosu pct-agent "$@"
