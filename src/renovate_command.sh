#!/bin/sh
set -eu

export NODE_OPTIONS="${NODE_OPTIONS:+$NODE_OPTIONS }--import=file:$RENOVATE_AUTOMATION_PATH/src/renovate_bootstrap.mjs"

attempt=1
while :; do
  status=0
  renovate || status=$?
  # The pinned engine reserves 6 for TemporaryErrors, including repository-changed.
  # Keep all other failures, and fail if the temporary error exhausts the budget.
  if [ "$status" -ne 6 ] || [ "$attempt" -ge 3 ]; then
    exit "$status"
  fi
  delay=$((attempt * 15))
  printf 'Renovate temporary error on attempt %s/3; retrying in %s seconds.\n' "$attempt" "$delay" >&2
  sleep "$delay"
  attempt=$((attempt + 1))
done
