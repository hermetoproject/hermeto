#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-only
# Start Nexus via podman-compose, initialize it, then attach to logs.
# Press Ctrl+C to stop and clean up.
set -o errexit -o nounset -o pipefail

DIR=$(dirname "$(realpath "${BASH_SOURCE[0]}")")

cleanup() {
    echo -e "\n--- Stopping Nexus ---"
    podman-compose -f "$DIR/docker-compose.yml" down -v
}
trap cleanup EXIT

echo "--- Starting Nexus via podman-compose ---"
# Ensure clean state (remove stale volumes from previous runs)
podman-compose -f "$DIR/docker-compose.yml" down -v 2>/dev/null || true
podman-compose -f "$DIR/docker-compose.yml" up -d

echo "--- Initializing Nexus ---"
python "$DIR/start.py"

echo -e "\nPress Ctrl+C to stop and cleanup...\n"
podman-compose -f "$DIR/docker-compose.yml" logs -f
