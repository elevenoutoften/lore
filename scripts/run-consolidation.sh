#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${LORE_CONSOLIDATION_ENV_FILE:-/etc/axis/lore-consolidation.env}"
LOCK_FILE="${LORE_CONSOLIDATION_LOCK_FILE:-/tmp/lore-consolidation.lock}"

if [[ -f "$ENV_FILE" ]]; then
    # shellcheck disable=SC1090
    source "$ENV_FILE"
fi

LORE_URL="${LORE_URL:-http://127.0.0.1:8210}"
LORE_BEARER_TOKEN="${LORE_BEARER_TOKEN:-}"
CONSOLIDATION_DRY_RUN="${CONSOLIDATION_DRY_RUN:-true}"
CONSOLIDATION_BATCH_SIZE="${CONSOLIDATION_BATCH_SIZE:-20}"
CONSOLIDATION_MAX_AUTO_APPLY="${CONSOLIDATION_MAX_AUTO_APPLY:-3}"

json_escape() {
    python3 -c 'import json, sys; print(json.dumps(sys.argv[1]))' "$1"
}

log_json() {
    local level="$1"
    local event="$2"
    local message="$3"
    local extra="${4:-}"

    printf '{"ts":%s,"level":%s,"event":%s,"message":%s' \
        "$(json_escape "$(date -u +"%Y-%m-%dT%H:%M:%SZ")")" \
        "$(json_escape "$level")" \
        "$(json_escape "$event")" \
        "$(json_escape "$message")"
    if [[ -n "$extra" ]]; then
        printf ',%s' "$extra"
    fi
    printf '}\n'
}

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
    log_json "info" "consolidation_skipped" "another consolidation run is already in progress" \
        "\"lock_file\":$(json_escape "$LOCK_FILE")"
    exit 0
fi

export CONSOLIDATION_DRY_RUN CONSOLIDATION_BATCH_SIZE CONSOLIDATION_MAX_AUTO_APPLY
payload="$(python3 - <<'PY'
import json
import os

def parse_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}

print(json.dumps({
    "dry_run": parse_bool(os.environ["CONSOLIDATION_DRY_RUN"]),
    "batch_size": int(os.environ["CONSOLIDATION_BATCH_SIZE"]),
    "max_auto_apply": int(os.environ["CONSOLIDATION_MAX_AUTO_APPLY"]),
}))
PY
)"

headers_file="$(mktemp)"
body_file="$(mktemp)"
curl_error_file="$(mktemp)"
trap 'rm -f "$headers_file" "$body_file" "$curl_error_file"' EXIT

curl_args=(
    --silent
    --show-error
    --max-time 300
    --header 'Content-Type: application/json'
    --output "$body_file"
    --dump-header "$headers_file"
    --write-out '%{http_code}'
    --data "$payload"
)

if [[ -n "$LORE_BEARER_TOKEN" ]]; then
    curl_args+=(--header "Authorization: Bearer $LORE_BEARER_TOKEN")
fi

set +e
status_code="$(
    curl "${curl_args[@]}" \
        "$LORE_URL/api/consolidation/run" \
        2>"$curl_error_file"
)"
curl_exit=$?
set -e

if [[ $curl_exit -ne 0 ]]; then
    curl_error="$(tr '\n' ' ' <"$curl_error_file" | sed 's/[[:space:]]\+/ /g; s/^ //; s/ $//')"
    log_json "error" "consolidation_request_failed" "failed to contact lore consolidation endpoint" \
        "\"url\":$(json_escape "$LORE_URL/api/consolidation/run"),\"curl_exit\":$curl_exit,\"error\":$(json_escape "$curl_error")"
    exit 1
fi

if [[ "$status_code" == "401" || "$status_code" == "403" ]]; then
    response_body="$(cat "$body_file")"
    log_json "error" "consolidation_auth_failed" "lore consolidation endpoint rejected authentication" \
        "\"url\":$(json_escape "$LORE_URL/api/consolidation/run"),\"status_code\":$status_code,\"response\":$(json_escape "$response_body")"
    exit 1
fi

if [[ ! "$status_code" =~ ^2 ]]; then
    response_body="$(cat "$body_file")"
    log_json "error" "consolidation_http_failed" "lore consolidation endpoint returned a non-success status" \
        "\"url\":$(json_escape "$LORE_URL/api/consolidation/run"),\"status_code\":$status_code,\"response\":$(json_escape "$response_body")"
    exit 0
fi

summary="$(
    python3 - "$body_file" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

summary = {
    "batch_id": data.get("batch_id"),
    "captures_processed": int(data.get("captures_processed", 0)),
    "candidates_extracted": int(data.get("candidates_extracted", 0)),
    "plans_generated": int(data.get("plans_generated", 0)),
    "auto_applied": int(data.get("auto_applied", 0)),
    "review_required": int(data.get("review_required", 0)),
    "errors": data.get("errors", []),
    "dry_run": bool(data.get("dry_run", False)),
}
print(json.dumps(summary))
PY
)"

log_json "info" "consolidation_completed" "lore consolidation run finished" "\"result\":$summary"
exit 0
