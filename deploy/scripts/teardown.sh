#!/usr/bin/env bash
# Stop the demo without deleting volumes (so a re-bootstrap is fast).
set -euo pipefail
cd "$(dirname "$0")/../compose"
docker compose down --remove-orphans
echo "[teardown] Containers stopped. Data volumes preserved (use reset.sh to wipe)."
