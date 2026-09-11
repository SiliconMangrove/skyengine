#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$project_root"

die() {
  printf 'install.sh: %s\n' "$1" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || die "Docker is required. Install Docker Engine and rerun this script."
if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  die "Docker Compose is required. Install the Compose plugin or docker-compose."
fi
docker info >/dev/null 2>&1 || die "Docker daemon is not available. Start Docker and rerun this script."

[[ -f docker-compose.yml ]] || die "Run this script from the skyengine project directory."
[[ -f .env.example ]] || die "Missing .env.example."

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

set_env_path() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(escape_sed_replacement "$value")"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*$|${key}=${escaped}|" .env
  else
    printf '\n%s=%s\n' "$key" "$value" >> .env
  fi
}

set_env_path SKYENGINE_COMPOSE_PATH "$project_root/docker-compose-online.yaml"
set_env_path SKYENGINE_BATCH_COMPOSE_PATH "$project_root/docker-compose.yaml"
set_env_path SKYENGINE_PROJECT_DIR "$project_root"
set_env_path SKYENGINE_BATCH_DATASET_HOST_DIR "$project_root/dataset"

mkdir -p sky_logs

"${compose[@]}" -f docker-compose.yml config --quiet
"${compose[@]}" -f docker-compose-online.yaml config --quiet
"${compose[@]}" -f docker-compose.yaml config --quiet

printf '%s\n' '[1/2] Building platform images...'
"${compose[@]}" -f docker-compose.yml build
printf '%s\n' '[2/2] Building Python engine images...'
"${compose[@]}" -p skyengine-online -f docker-compose-online.yaml build engine
"${compose[@]}" -p skyengine-batch -f docker-compose.yaml build engine

printf '\nInstallation complete.\n'
printf 'Configuration: %s/.env\n' "$project_root"
printf 'Run ./start.sh to start SkyEngine.\n'
