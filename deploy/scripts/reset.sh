#!/usr/bin/env bash
# Stop the demo AND delete all named volumes.
#
# Removes:
#   - the manager DB (so admin user/jobs/alerts/forecasts go too)
#   - PG data dirs for both clusters
#   - pgBackRest repos
#   - all log volumes
#   - agent state (so agents re-register on next boot)
#
# Does NOT remove built images. Re-bootstrap with deploy/scripts/bootstrap.sh.

set -euo pipefail
cd "$(dirname "$0")/../compose"

if [ "${1:-}" != "--yes" ]; then
    read -r -p "[reset] This deletes ALL demo data. Type 'yes' to confirm: " confirm
    [ "$confirm" = "yes" ] || { echo "Aborted."; exit 1; }
fi

docker compose down --remove-orphans --volumes
echo "[reset] All containers and named volumes removed."
