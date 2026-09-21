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

dfjspt_root="$(dirname "$project_root")/skyengine-DFJSPT"
[[ -d "$dfjspt_root" ]] || die "Missing sibling algorithm repository: $dfjspt_root"
[[ -f "$dfjspt_root/dfjsp_t_rl/__init__.py" ]] || die "Invalid DFJSP-T algorithm repository: expected $dfjspt_root/dfjsp_t_rl/__init__.py"

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

escape_sed_replacement() {
  printf '%s' "$1" | sed 's/[&|\\]/\\&/g'
}

set_env_value() {
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

set_env_value SKYENGINE_COMPOSE_PATH "$project_root/docker-compose-online.yaml"
set_env_value SKYENGINE_BATCH_COMPOSE_PATH "$project_root/docker-compose.yaml"
set_env_value SKYENGINE_PROJECT_HOST_DIR "$project_root"
set_env_value SKYENGINE_BATCH_DATASET_HOST_DIR "$project_root/dataset"

mkdir -p sky_logs

# Refresh whole-corpus digests after checkout; CRLF and LF produce different hashes.
# BEGIN dataset template digests
frontend_view="application/frontend/src/views/AlgorithmPlatformView.vue"
for corpus_split in train validation; do
  corpus_uri="dataset/dfjsp_t_${corpus_split}/${corpus_split}.jsonl"
  corpus_digest="$(sha256sum "$corpus_uri")"
  corpus_digest="${corpus_digest%% *}"
  sed -i "/^[[:space:]]*uri: 'dataset\/dfjsp_t_${corpus_split}\/${corpus_split}\.jsonl',[[:space:]]*\$/ { n; s/sha256:[[:xdigit:]]\{64\}/sha256:${corpus_digest}/; }" "$frontend_view"
  printf 'Dataset template: %s sha256:%s\n' "$corpus_uri" "$corpus_digest"
done
# END dataset template digests

"${compose[@]}" -f docker-compose.yml config --quiet
"${compose[@]}" -f docker-compose.yml -f docker-compose.gpu.yml config --quiet
"${compose[@]}" -f docker-compose-online.yaml config --quiet
"${compose[@]}" -f docker-compose.yaml config --quiet

printf '%s\n' '[1/2] Building platform images...'
"${compose[@]}" -f docker-compose.yml build

gpu_mode="$(sed -n 's/^SKYENGINE_GPU_MODE=//p' .env | tail -n 1)"
gpu_mode="${gpu_mode:-auto}"
case "$gpu_mode" in
  auto|cuda|cpu) ;;
  *) die "SKYENGINE_GPU_MODE must be auto, cuda, or cpu." ;;
esac

gpu_count=0
if [[ "$gpu_mode" != "cpu" ]]; then
  probe_output="$(docker run --rm --gpus all \
    --entrypoint /app/.venv/bin/python \
    skyengine-backend:latest \
    -c 'import torch; n=torch.cuda.device_count() if torch.cuda.is_available() else 0; [(torch.cuda.set_device(i), torch.ones(1).cuda().add_(1).cpu()) for i in range(n)]; print(n)' \
    2>/dev/null || true)"
  if [[ "$probe_output" =~ ^[1-9][0-9]*$ ]]; then
    gpu_count="$probe_output"
  elif [[ "$gpu_mode" == "cuda" ]]; then
    die "SKYENGINE_GPU_MODE=cuda, but Docker and the backend PyTorch runtime cannot access an NVIDIA GPU."
  fi
fi
set_env_value SKYENGINE_GPU_COUNT "$gpu_count"

if (( gpu_count > 0 )); then
  printf 'GPU runtime: CUDA (%s device(s) available to the backend).\n' "$gpu_count"
else
  printf 'GPU runtime: CPU (no Docker-accessible CUDA device detected).\n'
fi

printf '%s\n' '[2/2] Building Python engine images...'
"${compose[@]}" -p skyengine-online -f docker-compose-online.yaml build engine
"${compose[@]}" -p skyengine-batch -f docker-compose.yaml build engine

printf '\nInstallation complete.\n'
printf 'Configuration: %s/.env\n' "$project_root"
printf 'Run ./start.sh to start SkyEngine.\n'
