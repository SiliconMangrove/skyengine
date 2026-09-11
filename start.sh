#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$project_root"

command -v docker >/dev/null 2>&1 || {
  printf 'start.sh: Docker is required. Run install.sh after installing Docker.\n' >&2
  exit 1
}
if docker compose version >/dev/null 2>&1; then
  compose=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  compose=(docker-compose)
else
  printf 'start.sh: Docker Compose is required.\n' >&2
  exit 1
fi
docker info >/dev/null 2>&1 || {
  printf 'start.sh: Docker daemon is not available.\n' >&2
  exit 1
}
[[ -f .env ]] || {
  printf 'start.sh: .env is missing. Run ./install.sh first.\n' >&2
  exit 1
}

get_env_value() {
  local key="$1"
  local value
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1)"
  printf '%s' "${value:-}"
}

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped
  escaped="$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*$|${key}=${escaped}|" .env
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

port_in_use() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -H -ltn "( sport = :${port} )" 2>/dev/null | grep -q .
  elif command -v netstat >/dev/null 2>&1; then
    netstat -ltn 2>/dev/null | awk '{print $4}' | grep -Eq "(^|:)${port}$"
  else
    (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null
  fi
}

find_free_port() {
  local port="${1:-}"
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || {
    printf 'start.sh: invalid port value: %s\n' "$port" >&2
    exit 1
  }
  while port_in_use "$port" || [[ " $reserved_ports " == *" $port "* ]]; do
    ((port < 65535)) || {
      printf 'start.sh: no free port available after %s\n' "$1" >&2
      exit 1
    }
    port=$((port + 1))
  done
  reserved_ports+="$port "
  selected_port="$port"
}

reserve_port() {
  local port="$1"
  [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || {
    printf 'start.sh: invalid port value: %s\n' "$port" >&2
    exit 1
  }
  reserved_ports+="$port "
}

# Reuse the configured ports when the platform is already running. Otherwise,
# select free host ports before Compose creates any containers.
platform_running=0
reserved_ports=""

if ! docker network inspect skyengine-net >/dev/null 2>&1; then
  docker network create skyengine-net >/dev/null
fi

# A previous image may have populated the dependency volume with files copied
# from a non-Unix filesystem. Recreate it when the Vite command shim is not executable.
if docker container inspect skyengine-frontend >/dev/null 2>&1; then
  if ! docker exec skyengine-frontend sh -c 'test -x /app/node_modules/.bin/vite' >/dev/null 2>&1; then
    "${compose[@]}" -f docker-compose.yml rm -sf frontend >/dev/null 2>&1 || true
    docker volume rm skyengine-frontend-node-modules >/dev/null 2>&1 || true
  fi
fi

container_ids="$("${compose[@]}" -f docker-compose.yml ps -q 2>/dev/null || true)"
for container_id in $container_ids; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null)" == "true" ]]; then
    platform_running=1
    break
  fi
done

online_running=0
online_container_ids="$("${compose[@]}" -p skyengine-online -f docker-compose-online.yaml ps -q engine 2>/dev/null || true)"
for container_id in $online_container_ids; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null)" == "true" ]]; then
    online_running=1
    break
  fi
done

batch_running=0
batch_container_ids="$("${compose[@]}" -p skyengine-batch -f docker-compose.yaml ps -q engine 2>/dev/null || true)"
for container_id in $batch_container_ids; do
  if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id" 2>/dev/null)" == "true" ]]; then
    batch_running=1
    break
  fi
done

backend_port="$(get_env_value BACKEND_PORT)"
frontend_port="$(get_env_value FRONTEND_PORT)"
engine_port="$(get_env_value ENGINE_PORT)"
batch_engine_port="$(get_env_value BATCH_ENGINE_PORT)"
backend_port="${backend_port:-8233}"
frontend_port="${frontend_port:-5180}"
engine_port="${engine_port:-8080}"
batch_engine_port="${batch_engine_port:-8081}"

if [[ "$platform_running" -eq 0 ]]; then
  find_free_port "${backend_port:-8233}"
  backend_port="$selected_port"
  find_free_port "${frontend_port:-5180}"
  frontend_port="$selected_port"
  find_free_port "${engine_port:-8080}"
  engine_port="$selected_port"
  find_free_port "${batch_engine_port:-8081}"
  batch_engine_port="$selected_port"
else
  reserve_port "$backend_port"
  reserve_port "$frontend_port"
  if [[ "$online_running" -eq 0 ]]; then
    find_free_port "$engine_port"
    engine_port="$selected_port"
  else
    reserve_port "$engine_port"
  fi
  if [[ "$batch_running" -eq 0 ]]; then
    find_free_port "$batch_engine_port"
    batch_engine_port="$selected_port"
  else
    reserve_port "$batch_engine_port"
  fi
fi
set_env_value BACKEND_PORT "$backend_port"
set_env_value FRONTEND_PORT "$frontend_port"
set_env_value ENGINE_PORT "$engine_port"
set_env_value BATCH_ENGINE_PORT "$batch_engine_port"

BACKEND_PORT="$backend_port" FRONTEND_PORT="$frontend_port" \
  "${compose[@]}" -f docker-compose.yml up -d --build
ENGINE_PORT="$engine_port" \
  "${compose[@]}" -p skyengine-online -f docker-compose-online.yaml up -d engine

"${compose[@]}" -f docker-compose.yml ps

wait_for_http() {
  local name="$1"
  local url="$2"
  local attempt

  if ! command -v curl >/dev/null 2>&1; then
    printf 'start.sh: curl is not installed; skipping readiness check for %s.\n' "$name" >&2
    return 0
  fi

  for attempt in $(seq 1 60); do
    if curl --silent --show-error --fail --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done

  printf 'start.sh: %s did not become ready: %s\n' "$name" "$url" >&2
  "${compose[@]}" -f docker-compose.yml logs --no-color --tail 80 frontend >&2 || true
  "${compose[@]}" -f docker-compose.yml logs --no-color --tail 80 backend >&2 || true
  exit 1
}

wait_for_http 'backend' "http://127.0.0.1:${backend_port}/health"
wait_for_http 'frontend' "http://127.0.0.1:${frontend_port}/"
wait_for_http 'online engine' "http://127.0.0.1:${engine_port}/health"

printf '\nSkyEngine started. Frontend: http://localhost:%s\n' "$(get_env_value FRONTEND_PORT)"
printf 'Backend API: http://localhost:%s\n' "$(get_env_value BACKEND_PORT)"
printf 'Online engine: http://localhost:%s\n' "$(get_env_value ENGINE_PORT)"
