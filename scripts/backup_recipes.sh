#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ENV_FILE="$SCRIPT_DIR/recipes_backup.env"

log() {
  printf '[backup] %s\n' "$*"
}

fail() {
  printf '[backup] ERROR: %s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

configure_redis_cli() {
  if command -v redis-cli >/dev/null 2>&1; then
    REDIS_CLI=(redis-cli)
    return
  fi

  local redis_container
  redis_container="${REDIS_CONTAINER_NAME:-shared-redis}"
  if docker ps --format '{{.Names}}' | grep -Fxq "$redis_container"; then
    REDIS_CLI=(docker exec -i "$redis_container" redis-cli)
    return
  fi

  fail "Missing redis-cli and Redis container '$redis_container' is not running"
}

configure_minio_cli() {
  if command -v mc >/dev/null 2>&1; then
    MC_CMD=(mc)
    MC_STAGE_ROOT="$STAGING_DIR"
    return
  fi

  local minio_url_for_container
  minio_url_for_container="${MINIO_URL/127.0.0.1/host.docker.internal}"
  minio_url_for_container="${minio_url_for_container/localhost/host.docker.internal}"
  MC_CONFIG_DIR="$(mktemp -d "$STAGING_ROOT/mc-config.XXXXXX")"
  MC_CMD=(
    docker run --rm
    -v "$STAGING_DIR:/backup"
    -v "$MC_CONFIG_DIR:/mc-config"
    minio/mc
    --config-dir /mc-config
  )
  MC_MINIO_URL="$minio_url_for_container"
  MC_STAGE_ROOT="/backup"
}

redis_cmd() {
  "${REDIS_CLI[@]}" "$@"
}

mc_cmd() {
  "${MC_CMD[@]}" "$@"
}

qdrant_curl() {
  if [[ -n "$QDRANT_API_KEY" ]]; then
    curl -fsS -H "api-key: $QDRANT_API_KEY" "$@"
  else
    curl -fsS "$@"
  fi
}

usage() {
  cat <<EOF
Usage: $(basename "$0") [env-file]

Runs an app-scoped weekly backup for the recipe application:
- builds and pushes tagged images to GHCR
- exports the MinIO bucket used by the app
- exports Redis keys under the configured prefix
- exports Qdrant snapshots for the configured collections
- prunes backup folders to the configured retention count
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

ENV_FILE="${1:-$DEFAULT_ENV_FILE}"
[[ -f "$ENV_FILE" ]] || fail "Env file not found: $ENV_FILE"

# shellcheck disable=SC1090
source "$ENV_FILE"

require_cmd docker
require_cmd git
require_cmd python3
require_cmd curl
require_cmd shasum

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Library/CloudStorage/OneDrive-Personal/Backups/recipes/weekly}"
RETENTION_WEEKS="${RETENTION_WEEKS:-4}"
BACKUP_DATE="${BACKUP_DATE:-$(date +%F)}"
BACKUP_DIR="${BACKUP_ROOT%/}/$BACKUP_DATE"
ALLOW_REPLACE_EXISTING_BACKUP="${ALLOW_REPLACE_EXISTING_BACKUP:-0}"
STAGING_ROOT="${STAGING_ROOT:-/tmp/recipes-backup-staging}"

GHCR_REPOSITORY="${GHCR_REPOSITORY:-ghcr.io/lajh87/recipes}"
GHCR_WEEKLY_TAG="${GHCR_WEEKLY_TAG:-weekly-$BACKUP_DATE}"
RECIPES_IMAGE="${GHCR_REPOSITORY}:${GHCR_WEEKLY_TAG}"
BUILD_IMAGE="${BUILD_IMAGE:-1}"
PUSH_IMAGE="${PUSH_IMAGE:-1}"

RECIPES_COMPOSE_FILE="${RECIPES_COMPOSE_FILE:-$REPO_ROOT/docker-compose.yml}"
STOP_APP_CONTAINERS="${STOP_APP_CONTAINERS:-1}"

MINIO_ALIAS="${MINIO_ALIAS:-recipes-local}"
MINIO_URL="${MINIO_URL:-http://127.0.0.1:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-recipe-library-ebooks}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"

REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/5}"
REDIS_PREFIX="${REDIS_PREFIX:-recipes:}"

QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
QDRANT_COLLECTIONS="${QDRANT_COLLECTIONS:-recipes-cookbooks recipes-recipe-chunks}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"

EXPORT_APP_ENV="${EXPORT_APP_ENV:-0}"
APP_ENV_FILE_PATH="${APP_ENV_FILE_PATH:-$REPO_ROOT/.env}"
GPG_RECIPIENT="${GPG_RECIPIENT:-}"
REDIS_CONTAINER_NAME="${REDIS_CONTAINER_NAME:-shared-redis}"
MC_MINIO_URL="${MINIO_URL}"
MC_STAGE_ROOT=""

declare -a STOPPED_SERVICES=()
declare -a REDIS_CLI=()
declare -a MC_CMD=()
STAGING_DIR=""
MC_CONFIG_DIR=""

cleanup() {
  if [[ "${#STOPPED_SERVICES[@]}" -gt 0 ]]; then
    log "Restarting recipe containers: ${STOPPED_SERVICES[*]}"
    docker compose -f "$RECIPES_COMPOSE_FILE" up -d "${STOPPED_SERVICES[@]}"
  fi
  if [[ -n "$STAGING_DIR" && -d "$STAGING_DIR" ]]; then
    rm -rf -- "$STAGING_DIR"
  fi
  if [[ -n "$MC_CONFIG_DIR" && -d "$MC_CONFIG_DIR" ]]; then
    rm -rf -- "$MC_CONFIG_DIR"
  fi
}

collect_backup_dirs() {
  local dir
  backup_dirs=()
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    backup_dirs+=("$dir")
  done < <(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -print | sort)
}

trap cleanup EXIT

mkdir -p "$BACKUP_ROOT"
mkdir -p "$STAGING_ROOT"
if [[ -e "$BACKUP_DIR" ]]; then
  if [[ "$ALLOW_REPLACE_EXISTING_BACKUP" == "1" ]]; then
    log "Removing existing backup directory: $BACKUP_DIR"
    rm -rf -- "$BACKUP_DIR"
  elif [[ ! -f "$BACKUP_DIR/manifest.json" ]]; then
    log "Removing incomplete backup directory from a previous failed run: $BACKUP_DIR"
    rm -rf -- "$BACKUP_DIR"
  else
    fail "Backup directory already exists: $BACKUP_DIR"
  fi
fi

STAGING_DIR="$(mktemp -d "$STAGING_ROOT/${BACKUP_DATE}.staging.XXXXXX")"
mkdir -p "$STAGING_DIR"/image "$STAGING_DIR"/minio "$STAGING_DIR"/redis "$STAGING_DIR"/qdrant "$STAGING_DIR"/config

configure_redis_cli
configure_minio_cli

GIT_SHA="$(git -C "$REPO_ROOT" rev-parse HEAD)"
RECIPES_SHA_IMAGE="${GHCR_REPOSITORY}:sha-${GIT_SHA}"

printf '%s\n' "$RECIPES_IMAGE" > "$STAGING_DIR/image/ghcr-tag.txt"
printf '%s\n' "$RECIPES_SHA_IMAGE" > "$STAGING_DIR/image/ghcr-sha-tag.txt"
printf '%s\n' "$GIT_SHA" > "$STAGING_DIR/image/git-sha.txt"

if [[ "$BUILD_IMAGE" == "1" ]]; then
  log "Building GHCR image tags"
  docker build -t "$RECIPES_IMAGE" -t "$RECIPES_SHA_IMAGE" "$REPO_ROOT"
fi

if [[ "$PUSH_IMAGE" == "1" ]]; then
  log "Pushing GHCR image tags"
  docker push "$RECIPES_IMAGE"
  docker push "$RECIPES_SHA_IMAGE"
fi

if [[ "$EXPORT_APP_ENV" == "1" ]]; then
  require_cmd gpg
  [[ -n "$GPG_RECIPIENT" ]] || fail "GPG_RECIPIENT must be set when EXPORT_APP_ENV=1"
  [[ -f "$APP_ENV_FILE_PATH" ]] || fail "APP_ENV_FILE_PATH not found: $APP_ENV_FILE_PATH"
  log "Encrypting application env file"
  gpg --batch --yes --output "$STAGING_DIR/config/recipes.env.gpg" --encrypt --recipient "$GPG_RECIPIENT" "$APP_ENV_FILE_PATH"
fi

if [[ "$STOP_APP_CONTAINERS" == "1" ]]; then
  while IFS= read -r service; do
    case "$service" in
      app|worker)
        STOPPED_SERVICES+=("$service")
        ;;
    esac
  done < <(docker compose -f "$RECIPES_COMPOSE_FILE" ps --status running --services 2>/dev/null || true)

  if [[ "${#STOPPED_SERVICES[@]}" -gt 0 ]]; then
    log "Stopping recipe containers: ${STOPPED_SERVICES[*]}"
    docker compose -f "$RECIPES_COMPOSE_FILE" stop "${STOPPED_SERVICES[@]}"
  fi
fi

log "Exporting MinIO bucket: $MINIO_BUCKET"
mc_cmd alias set "$MINIO_ALIAS" "$MC_MINIO_URL" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc_cmd stat --json "$MINIO_ALIAS/$MINIO_BUCKET" > "$STAGING_DIR/minio/bucket-stat.json"
mc_cmd find "$MINIO_ALIAS/$MINIO_BUCKET" --json > "$STAGING_DIR/minio/object-list.jsonl"
mc_cmd cp --recursive --preserve "$MINIO_ALIAS/$MINIO_BUCKET/" "$MC_STAGE_ROOT/minio/$MINIO_BUCKET/"

log "Exporting Redis keys for prefix: ${REDIS_PREFIX}*"
: > "$STAGING_DIR/redis/index.tsv"
redis_index=0
while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  ttl="$(redis_cmd -u "$REDIS_URL" pttl "$key")"
  if [[ "$ttl" == "-2" ]]; then
    continue
  fi
  if [[ "$ttl" == "-1" ]]; then
    ttl=0
  fi

  redis_index=$((redis_index + 1))
  dump_id="$(printf '%06d' "$redis_index")"
  printf '%s\t%s\t%s\n' "$dump_id" "$ttl" "$key" >> "$STAGING_DIR/redis/index.tsv"
  redis_cmd -u "$REDIS_URL" --raw dump "$key" | python3 -c 'import base64,sys; sys.stdout.write(base64.b64encode(sys.stdin.buffer.read()).decode())' > "$STAGING_DIR/redis/${dump_id}.dump.b64"
done < <(redis_cmd -u "$REDIS_URL" --scan --pattern "${REDIS_PREFIX}*")

read -r -a qdrant_collections <<< "$QDRANT_COLLECTIONS"
for collection in "${qdrant_collections[@]}"; do
  [[ -n "$collection" ]] || continue
  log "Exporting Qdrant snapshot for collection: $collection"
  snapshot_metadata="$(
    qdrant_curl -X POST "$QDRANT_URL/collections/$collection/snapshots?wait=true"
  )"
  printf '%s\n' "$snapshot_metadata" > "$STAGING_DIR/qdrant/${collection}.snapshot.json"
  snapshot_name="$(
    python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["name"])' <<<"$snapshot_metadata"
  )"
  qdrant_curl "$QDRANT_URL/collections/$collection/snapshots/$snapshot_name" -o "$STAGING_DIR/qdrant/${collection}.snapshot"
  shasum -a 256 "$STAGING_DIR/qdrant/${collection}.snapshot" > "$STAGING_DIR/qdrant/${collection}.snapshot.sha256"
done

log "Writing manifest"
python3 - "$STAGING_DIR" "$BACKUP_DATE" "$RECIPES_IMAGE" "$RECIPES_SHA_IMAGE" "$GIT_SHA" "$MINIO_BUCKET" "$REDIS_URL" "$REDIS_PREFIX" "$QDRANT_COLLECTIONS" <<'PY'
import json
import pathlib
import sys

backup_dir = pathlib.Path(sys.argv[1])
payload = {
    "backup_date": sys.argv[2],
    "image": {
        "weekly_tag": sys.argv[3],
        "sha_tag": sys.argv[4],
        "git_sha": sys.argv[5],
    },
    "minio_bucket": sys.argv[6],
    "redis": {
        "url": sys.argv[7],
        "prefix": sys.argv[8],
    },
    "qdrant_collections": [item for item in sys.argv[9].split() if item],
}
(backup_dir / "manifest.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
PY

mv "$STAGING_DIR" "$BACKUP_DIR"
STAGING_DIR=""

log "Pruning backup folders to retain the latest $RETENTION_WEEKS"
collect_backup_dirs
if (( ${#backup_dirs[@]} > RETENTION_WEEKS )); then
  prune_count=$(( ${#backup_dirs[@]} - RETENTION_WEEKS ))
  for ((idx = 0; idx < prune_count; idx++)); do
    log "Removing old backup: ${backup_dirs[$idx]}"
    rm -rf -- "${backup_dirs[$idx]}"
  done
fi

log "Backup complete: $BACKUP_DIR"
