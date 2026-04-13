#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DEFAULT_ENV_FILE="$SCRIPT_DIR/recipes_backup.env"

log() {
  printf '[restore] %s\n' "$*"
}

fail() {
  printf '[restore] ERROR: %s\n' "$*" >&2
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
    MC_IMPORT_ROOT="$backup_dir/minio"
    return
  fi

  local minio_url_for_container
  minio_url_for_container="${MINIO_URL/127.0.0.1/host.docker.internal}"
  minio_url_for_container="${minio_url_for_container/localhost/host.docker.internal}"
  RESTORE_CACHE_DIR="$(mktemp -d "$RESTORE_STAGING_ROOT/minio-import.XXXXXX")"
  MC_CONFIG_DIR="$(mktemp -d "$RESTORE_STAGING_ROOT/mc-config.XXXXXX")"
  mkdir -p "$RESTORE_CACHE_DIR"
  cp -R "$backup_dir/minio/." "$RESTORE_CACHE_DIR/"
  MC_CMD=(
    docker run --rm
    -v "$RESTORE_CACHE_DIR:/backup-minio:ro"
    -v "$MC_CONFIG_DIR:/mc-config"
    minio/mc
    --config-dir /mc-config
  )
  MC_MINIO_URL="$minio_url_for_container"
  MC_IMPORT_ROOT="/backup-minio"
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
Usage: $(basename "$0") [env-file] [backup-dir]

Restores the recipe application data from a backup folder created by backup_recipes.sh.
If backup-dir is omitted, the most recent dated folder under BACKUP_ROOT is used.
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
require_cmd python3
require_cmd curl

BACKUP_ROOT="${BACKUP_ROOT:-$HOME/Library/CloudStorage/OneDrive-Personal/Backups/recipes/weekly}"
RESTORE_STAGING_ROOT="${RESTORE_STAGING_ROOT:-/tmp/recipes-restore-staging}"
RECIPES_COMPOSE_FILE="${RECIPES_COMPOSE_FILE:-$REPO_ROOT/docker-compose.yml}"
STOP_APP_CONTAINERS="${STOP_APP_CONTAINERS:-1}"

MINIO_ALIAS="${MINIO_ALIAS:-recipes-local}"
MINIO_URL="${MINIO_URL:-http://127.0.0.1:9000}"
MINIO_BUCKET="${MINIO_BUCKET:-recipe-library-ebooks}"
MINIO_ROOT_USER="${MINIO_ROOT_USER:-minioadmin}"
MINIO_ROOT_PASSWORD="${MINIO_ROOT_PASSWORD:-minioadmin}"

REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/5}"
REDIS_PREFIX="${REDIS_PREFIX:-recipes:}"
PURGE_REDIS_PREFIX_ON_RESTORE="${PURGE_REDIS_PREFIX_ON_RESTORE:-1}"

QDRANT_URL="${QDRANT_URL:-http://127.0.0.1:6333}"
QDRANT_COLLECTIONS="${QDRANT_COLLECTIONS:-recipes-cookbooks recipes-recipe-chunks}"
QDRANT_API_KEY="${QDRANT_API_KEY:-}"
REDIS_CONTAINER_NAME="${REDIS_CONTAINER_NAME:-shared-redis}"
MC_MINIO_URL="${MINIO_URL}"
MC_IMPORT_ROOT=""

declare -a STOPPED_SERVICES=()
declare -a REDIS_CLI=()
declare -a MC_CMD=()
RESTORE_CACHE_DIR=""
MC_CONFIG_DIR=""

cleanup() {
  if [[ "${#STOPPED_SERVICES[@]}" -gt 0 ]]; then
    log "Restarting recipe containers: ${STOPPED_SERVICES[*]}"
    docker compose -f "$RECIPES_COMPOSE_FILE" up -d "${STOPPED_SERVICES[@]}"
  fi
  if [[ -n "$RESTORE_CACHE_DIR" && -d "$RESTORE_CACHE_DIR" ]]; then
    rm -rf -- "$RESTORE_CACHE_DIR"
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

backup_dir="${2:-}"
if [[ -z "$backup_dir" ]]; then
  collect_backup_dirs
  [[ "${#backup_dirs[@]}" -gt 0 ]] || fail "No backup directories found under $BACKUP_ROOT"
  backup_dir="${backup_dirs[${#backup_dirs[@]}-1]}"
fi

[[ -d "$backup_dir" ]] || fail "Backup directory not found: $backup_dir"
[[ -f "$backup_dir/manifest.json" ]] || fail "Backup manifest not found: $backup_dir/manifest.json"
mkdir -p "$RESTORE_STAGING_ROOT"

configure_redis_cli
configure_minio_cli

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

log "Restoring MinIO bucket: $MINIO_BUCKET"
mc_cmd alias set "$MINIO_ALIAS" "$MC_MINIO_URL" "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" >/dev/null
mc_cmd mb --ignore-existing "$MINIO_ALIAS/$MINIO_BUCKET" >/dev/null
mc_cmd cp --recursive --preserve "$MC_IMPORT_ROOT/$MINIO_BUCKET/" "$MINIO_ALIAS/$MINIO_BUCKET/"

if [[ "$PURGE_REDIS_PREFIX_ON_RESTORE" == "1" ]]; then
  log "Purging existing Redis keys for prefix: ${REDIS_PREFIX}*"
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    redis_cmd -u "$REDIS_URL" unlink "$key" >/dev/null
  done < <(redis_cmd -u "$REDIS_URL" --scan --pattern "${REDIS_PREFIX}*")
fi

log "Restoring Redis keys from backup"
if [[ -f "$backup_dir/redis/index.tsv" ]]; then
  while IFS=$'\t' read -r dump_id ttl key; do
    [[ -n "${dump_id:-}" ]] || continue
    dump_file="$backup_dir/redis/${dump_id}.dump.b64"
    [[ -f "$dump_file" ]] || fail "Missing Redis dump file: $dump_file"
    python3 - "$dump_file" <<'PY' | redis_cmd -u "$REDIS_URL" -x restore "$key" "$ttl" replace >/dev/null
import base64
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
sys.stdout.buffer.write(base64.b64decode(path.read_text(encoding="utf-8")))
PY
  done < "$backup_dir/redis/index.tsv"
fi

read -r -a qdrant_collections <<< "$QDRANT_COLLECTIONS"
for collection in "${qdrant_collections[@]}"; do
  [[ -n "$collection" ]] || continue
  snapshot_path="$backup_dir/qdrant/${collection}.snapshot"
  [[ -f "$snapshot_path" ]] || fail "Missing Qdrant snapshot: $snapshot_path"

  checksum=""
  if [[ -f "$backup_dir/qdrant/${collection}.snapshot.json" ]]; then
    checksum="$(
      python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["result"].get("checksum", ""))' "$backup_dir/qdrant/${collection}.snapshot.json"
    )"
  fi

  log "Restoring Qdrant collection: $collection"
  if [[ -n "$checksum" ]]; then
    qdrant_curl -X POST "$QDRANT_URL/collections/$collection/snapshots/upload?wait=true&priority=snapshot&checksum=$checksum" -F "snapshot=@$snapshot_path" >/dev/null
  else
    qdrant_curl -X POST "$QDRANT_URL/collections/$collection/snapshots/upload?wait=true&priority=snapshot" -F "snapshot=@$snapshot_path" >/dev/null
  fi
done

log "Restore complete from: $backup_dir"
