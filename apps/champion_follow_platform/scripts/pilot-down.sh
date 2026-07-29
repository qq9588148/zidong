#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/ops/compose/pilot.compose.yml"
RUN_ROOT="$ROOT/ops/run"
ENV_FILE="${PILOT_ENV_FILE:-$RUN_ROOT/pilot.env}"
CURRENT_RUN="$RUN_ROOT/current-run"
destroy=false

if [[ "${1:-}" == "--destroy-test-data" ]]; then
  destroy=true
  shift
fi
[[ $# -eq 0 ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }
[[ -f "$ENV_FILE" && -f "$CURRENT_RUN" ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }

while IFS='=' read -r key value || [[ -n "${key:-}" ]]; do
  [[ -z "${key:-}" || "$key" == \#* ]] && continue
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }
  export "$key=$value"
done < "$ENV_FILE"

run_id="$(<"$CURRENT_RUN")"
[[ "$run_id" =~ ^[a-z0-9][a-z0-9-]{7,63}$ ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }
run_dir="$RUN_ROOT/pilot-$run_id"
[[ -d "$run_dir" && "$run_dir" == "$RUN_ROOT"/pilot-* ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }

export PILOT_RUN_ID="$run_id"
export PILOT_RUN_DIR="$run_dir"
export PILOT_PROJECT_NAME="champion-pilot-$run_id"
export PILOT_SERVER_PORT="${PILOT_SERVER_PORT:-58000}"
export PILOT_POSTGRES_PORT="${PILOT_POSTGRES_PORT:-55440}"

compose=(docker compose --project-name "$PILOT_PROJECT_NAME" --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
if [[ "$destroy" == true ]]; then
  mapfile -t volumes < <(docker volume ls --quiet \
    --filter label=com.champion-follow.pilot=true \
    --filter "label=com.champion-follow.run-id=$run_id")
  [[ ${#volumes[@]} -ge 1 ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }
  for volume in "${volumes[@]}"; do
    [[ "$volume" == "$PILOT_PROJECT_NAME"_* ]] || { printf '%s\n' "pilot_stop_failed" >&2; exit 1; }
  done
  "${compose[@]}" down --volumes --remove-orphans
  rm -rf -- "$run_dir"
  rm -f -- "$CURRENT_RUN"
  printf '%s\n' "pilot_stopped_and_destroyed:$run_id"
else
  "${compose[@]}" down --remove-orphans
  printf '%s\n' "pilot_stopped_preserved:$run_id"
fi
