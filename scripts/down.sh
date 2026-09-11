#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

docker compose -f docker-compose.yml down --remove-orphans
docker compose -p skyengine-online -f docker-compose-online.yaml down --remove-orphans || true
docker compose -p skyengine-batch -f docker-compose.yaml down --remove-orphans || true
