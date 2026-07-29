#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/ops/compose/pilot.compose.yml"
RUN_ROOT="$ROOT/ops/run"
ENV_FILE="${PILOT_ENV_FILE:-$RUN_ROOT/pilot.env}"
CURRENT_RUN="$RUN_ROOT/current-run"

fail() {
  printf '%s\n' "pilot_start_failed" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail
command -v openssl >/dev/null 2>&1 || fail
[[ -f "$ENV_FILE" ]] || fail

declare -A seen=()
allowed='POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL CHAMPION_DATABASE_URL CHAMPION_PUBLIC_BASE_URL CHAMPION_TRUSTED_ADMIN_ORIGIN CHAMPION_TASK_SIGNING_KEY_PATH CHAMPION_SECRET_VAULT_KEY_PATH CHAMPION_ALLOCATION_SEED_PATH CHAMPION_ALLOCATION_SEED_VERSION CHAMPION_TOKEN_PEPPER TZ PILOT_SERVER_PORT PILOT_POSTGRES_PORT'
while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
  [[ -z "${key:-}" || "$key" == \#* ]] && continue
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || fail
  [[ " $allowed " == *" $key "* ]] || fail
  [[ -z "${seen[$key]:-}" ]] || fail
  seen[$key]=1
  export "$key=$value"
done < "$ENV_FILE"

required='POSTGRES_DB POSTGRES_USER POSTGRES_PASSWORD DATABASE_URL CHAMPION_DATABASE_URL CHAMPION_PUBLIC_BASE_URL CHAMPION_TRUSTED_ADMIN_ORIGIN CHAMPION_ALLOCATION_SEED_VERSION CHAMPION_TOKEN_PEPPER TZ'
for key in $required; do
  value="${!key:-}"
  [[ -n "$value" ]] || fail
  [[ "$value" != *SET_A_* && "$value" != *YOUR_* ]] || fail
done
[[ ${#POSTGRES_PASSWORD} -ge 16 ]] || fail
[[ ${#CHAMPION_TOKEN_PEPPER} -ge 32 ]] || fail

mkdir -p "$RUN_ROOT"
chmod 700 "$RUN_ROOT"
if [[ -n "${PILOT_RUN_ID:-}" ]]; then
  run_id="$PILOT_RUN_ID"
elif [[ -f "$CURRENT_RUN" ]]; then
  run_id="$(<"$CURRENT_RUN")"
else
  run_id="$(date -u +%Y%m%d%H%M%S)-$(openssl rand -hex 4)"
fi
[[ "$run_id" =~ ^[a-z0-9][a-z0-9-]{7,63}$ ]] || fail

run_dir="$RUN_ROOT/pilot-$run_id"
mkdir -p "$run_dir"
chmod 700 "$run_dir"
umask 077
printf '%s\n' "$run_id" > "$CURRENT_RUN"
chmod 600 "$CURRENT_RUN"

if [[ ! -f "$run_dir/task-signing.pem" ]]; then
  openssl genpkey -algorithm ED25519 -out "$run_dir/task-signing.pem.tmp" >/dev/null 2>&1
  mv "$run_dir/task-signing.pem.tmp" "$run_dir/task-signing.pem"
fi
for secret in vault.key allocation-seed.key; do
  if [[ ! -f "$run_dir/$secret" ]]; then
    openssl rand -out "$run_dir/$secret.tmp" 32
    mv "$run_dir/$secret.tmp" "$run_dir/$secret"
  fi
done
chmod 600 "$run_dir/task-signing.pem" "$run_dir/vault.key" "$run_dir/allocation-seed.key"

export PILOT_RUN_ID="$run_id"
export PILOT_RUN_DIR="$run_dir"
export PILOT_PROJECT_NAME="champion-pilot-$run_id"
export PILOT_SERVER_PORT="${PILOT_SERVER_PORT:-58000}"
export PILOT_POSTGRES_PORT="${PILOT_POSTGRES_PORT:-55440}"

docker compose \
  --project-name "$PILOT_PROJECT_NAME" \
  --env-file "$ENV_FILE" \
  -f "$COMPOSE_FILE" \
  up --build --wait

printf '%s\n' "pilot_started:$run_id"
printf '%s\n' "health:http://127.0.0.1:$PILOT_SERVER_PORT/healthz"
