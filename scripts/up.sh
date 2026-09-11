#!/usr/bin/env bash
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root"

if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from .env.example; set host paths before rerunning."
  exit 1
fi

docker network inspect skyengine-net >/dev/null 2>&1 || docker network create skyengine-net >/dev/null
docker compose -f docker-compose.yml up -d --build
