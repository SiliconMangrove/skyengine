#!/usr/bin/env bash
set -Eeuo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$project_root"

lock_dir="$project_root/.skyengine-start.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  if [[ -f "$lock_dir/pid" ]] && ! kill -0 "$(cat "$lock_dir/pid" 2>/dev/null)" 2>/dev/null; then
    rm -rf "$lock_dir"
    mkdir "$lock_dir"
  else
    printf 'start.sh: another SkyEngine startup is already running.\n' >&2
    exit 1
  fi
fi
printf '%s\n' "$$" > "$lock_dir/pid"
trap 'rm -rf "$lock_dir"' EXIT

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
env_size="$(wc -c < .env)"
if (( env_size > 1048576 )); then
  printf 'start.sh: .env is unexpectedly large (%s bytes). Repair it from .env.example before starting.\n' "$env_size" >&2
  exit 1
fi

get_env_value() {
  local key="$1"
  local value
  value="$(sed -n "s/^${key}=//p" .env | tail -n 1)"
  printf '%s' "${value:-}"
}

gpu_mode="$(get_env_value SKYENGINE_GPU_MODE)"
gpu_mode="${gpu_mode:-auto}"
case "$gpu_mode" in
  auto|cuda|cpu) ;;
  *)
    printf 'start.sh: SKYENGINE_GPU_MODE must be auto, cuda, or cpu.\n' >&2
    exit 1
    ;;
esac

platform_compose=(-f docker-compose.yml)
"${compose[@]}" -f docker-compose.yml build backend frontend
gpu_count=0
if [[ "$gpu_mode" != "cpu" ]]; then
  probe_output="$(docker run --rm --gpus all \
    --entrypoint /app/.venv/bin/python \
    skyengine-backend:latest \
    -c 'import torch; n=torch.cuda.device_count() if torch.cuda.is_available() else 0; [(torch.cuda.set_device(i), torch.ones(1).cuda().add_(1).cpu()) for i in range(n)]; print(n)' \
    2>/dev/null || true)"
  if [[ "$probe_output" =~ ^[1-9][0-9]*$ ]]; then
    gpu_count="$probe_output"
    platform_compose+=(-f docker-compose.gpu.yml)
  elif [[ "$gpu_mode" == "cuda" ]]; then
    printf 'start.sh: CUDA mode was requested, but Docker and backend PyTorch cannot access an NVIDIA GPU.\n' >&2
    exit 1
  else
    printf 'start.sh: no Docker-accessible CUDA device detected; backend will use CPU.\n'
  fi
fi

set_env_value() {
  local key="$1"
  local value="$2"
  local escaped temp_file
  if (( $(wc -c < .env) > 1048576 )); then
    printf 'start.sh: refusing to rewrite an oversized .env file.\n' >&2
    exit 1
  fi
  escaped="$(printf '%s' "$value" | sed 's/[&|\\]/\\&/g')"
  temp_file=".env.tmp.$$"
  cp .env "$temp_file"
  if grep -qE "^${key}=" .env; then
    sed -i "s|^${key}=.*$|${key}=${escaped}|" "$temp_file"
  else
    printf '%s=%s\n' "$key" "$value" >> "$temp_file"
  fi
  mv -f "$temp_file" .env
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
    "${compose[@]}" "${platform_compose[@]}" rm -sf frontend >/dev/null 2>&1 || true
    docker volume rm skyengine-frontend-node-modules >/dev/null 2>&1 || true
  fi
fi

container_ids="$("${compose[@]}" "${platform_compose[@]}" ps -q 2>/dev/null || true)"
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
  "${compose[@]}" "${platform_compose[@]}" up -d
ENGINE_PORT="$engine_port" \
  "${compose[@]}" -p skyengine-online -f docker-compose-online.yaml up -d engine

"${compose[@]}" "${platform_compose[@]}" ps
printf 'Backend compute: %s' "$(if (( gpu_count > 0 )); then printf 'CUDA'; else printf 'CPU'; fi)"
if (( gpu_count > 0 )); then
  printf ' (%s device(s))' "$gpu_count"
fi
printf '\n'

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
  "${compose[@]}" "${platform_compose[@]}" logs --no-color --tail 80 frontend >&2 || true
  "${compose[@]}" "${platform_compose[@]}" logs --no-color --tail 80 backend >&2 || true
  exit 1
}

wait_for_http 'backend' "http://127.0.0.1:${backend_port}/health"
wait_for_http 'frontend' "http://127.0.0.1:${frontend_port}/"
wait_for_http 'online engine' "http://127.0.0.1:${engine_port}/health"

printf '\nSkyEngine started. Frontend: http://localhost:%s\n' "$(get_env_value FRONTEND_PORT)"
printf 'Backend API: http://localhost:%s\n' "$(get_env_value BACKEND_PORT)"
printf 'Online engine: http://localhost:%s\n' "$(get_env_value ENGINE_PORT)"
