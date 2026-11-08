#!/usr/bin/env bash
# Automated verification entry point.
#
# Builds a dedicated test virtualenv in .test-artifacts/venv (gitignored) containing:
#   - the exact dependency set locked by the Hermes checkout (uv export of its uv.lock), so the
#     tests import the real host code (plugin manager, tool registry, slash-command dispatch);
#   - pypdf at the same pin Hermes uses, plus pytest, reportlab (fixture PDFs) and httpx.
# Nothing is installed into the Hermes install or any Hermes profile.
#
# Usage: scripts/test.sh [pytest args]     HERMES_ROOT=/path/to/hermes-agent to override the checkout.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES_ROOT="${HERMES_ROOT:-$HOME/.hermes/hermes-agent}"
VENV="$ROOT/.test-artifacts/venv"
PY="$VENV/bin/python"
STAMP="$VENV/.sd-stamp"
WANT="$(cd "$HERMES_ROOT" && git rev-parse HEAD 2>/dev/null || echo unknown)"
mkdir -p "$ROOT/.test-artifacts"
if [ ! -x "$PY" ] || [ "$(cat "$STAMP" 2>/dev/null)" != "$WANT" ]; then
  echo "Building test venv against Hermes checkout $WANT ..." >&2
  uv venv --quiet --allow-existing --python ">=3.11" "$VENV"
  uv export --project "$HERMES_ROOT" --frozen --no-dev --no-hashes --no-emit-project --no-header --no-annotate \
    > "$ROOT/.test-artifacts/hermes-core-reqs.txt"
  uv pip install --quiet --no-deps --python "$PY" -r "$ROOT/.test-artifacts/hermes-core-reqs.txt"
  uv pip install --quiet --python "$PY" "pytest>=8,<10" "pypdf==6.21.4" "reportlab==5.1.0" "httpx>=0.27,<1"
  echo "$WANT" > "$STAMP"
fi
export SD_HERMES_ROOT="$HERMES_ROOT"
mkdir -p "$ROOT/.test-artifacts/tmp"
export TMPDIR="$ROOT/.test-artifacts/tmp"
cd "$ROOT"
exec "$PY" -m pytest "$@"
