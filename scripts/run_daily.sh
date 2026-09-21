#!/usr/bin/env bash
# Local daily-sampler runner: activates the project venv, runs
# `python -m trip_hunter.daily_sampler`, and appends timestamped output to
# logs/daily_sample.log. Intended for a local cron/launchd entry - see
# .github/workflows/daily_sample.yml for the hosted equivalent.
#
# Usage:
#   scripts/run_daily.sh [extra args passed through to daily_sampler, e.g. --dry-run]

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${PROJECT_ROOT}"

LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/daily_sample.log"
mkdir -p "${LOG_DIR}"

if [[ -f "${PROJECT_ROOT}/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "${PROJECT_ROOT}/.venv/bin/activate"
else
  echo "Error: .venv not found at ${PROJECT_ROOT}/.venv - create it first (python3 -m venv .venv && .venv/bin/pip install -e .)." >&2
  exit 1
fi

timestamp() {
  date '+%Y-%m-%dT%H:%M:%S%z'
}

{
  echo "[$(timestamp)] daily_sample run started"
  if python -m trip_hunter.daily_sampler "$@" 2>&1 | while IFS= read -r line; do
    echo "[$(timestamp)] ${line}"
  done; then
    echo "[$(timestamp)] daily_sample run finished (exit 0)"
  else
    status=$?
    echo "[$(timestamp)] daily_sample run FAILED (exit ${status})"
    exit "${status}"
  fi
} >> "${LOG_FILE}" 2>&1
