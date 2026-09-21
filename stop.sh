#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$project_root"

command -v docker >/dev/null 2>&1 || {
  printf 'stop.sh: Docker is not installed; nothing to stop.\n'
  exit 0
}

if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  printf 'stop.sh: Docker Compose is not installed; nothing to stop.\n'
  exit 0
fi

"${compose[@]}" -f docker-compose.yml down --remove-orphans --timeout 10 || true
"${compose[@]}" -p skyengine-online -f docker-compose-online.yaml down --remove-orphans --timeout 10 || true
"${compose[@]}" -p skyengine-batch -f docker-compose.yaml down --remove-orphans --timeout 10 || true

printf 'SkyEngine stopped.\n'
