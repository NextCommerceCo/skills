#!/usr/bin/env bash
# next-campaigns-create: provision a Campaigns App campaign over the NEXT Admin API.
#
# Launcher for scripts/campaign_admin.py. It prints the skill version, checks for
# Python 3.9+, and forwards every other argument to the engine unchanged. The
# engine owns argument parsing and credential loading, so the store a command
# targets and the token it uses always come from the same --store value.
set -euo pipefail

src="${BASH_SOURCE[0]}"
while [ -L "$src" ]; do
  link_dir="$(cd -P "$(dirname "$src")" && pwd)"
  src="$(readlink "$src")"
  case "$src" in
    /*) ;;
    *) src="$link_dir/$src" ;;
  esac
done
SKILL_DIR="$(cd -P "$(dirname "$src")" && pwd)"
ENGINE="$SKILL_DIR/scripts/campaign_admin.py"
PY="${NEXT_CAMPAIGNS_CREATE_PYTHON:-python3}"

usage() {
  cat <<'EOF'
next-campaigns-create: provision a Campaigns App campaign over the NEXT Admin API

Usage:
  next-campaigns-create.sh --version
  next-campaigns-create.sh --help
  next-campaigns-create.sh discover  --store <subdomain> [--out <dir>]
  next-campaigns-create.sh metadata  --store <subdomain> [--apply]
  next-campaigns-create.sh recommend --discovery <dir>/discovery.json --hero <product_id>
                                    --ctc low|high --anchor-price <decimal>
                                    --shipping <code>:<price>[:<key>] [options]
  next-campaigns-create.sh plan      --plan <dir>/campaign-plan.json [--check-store]
  next-campaigns-create.sh apply     --plan <dir>/campaign-plan.json --yes --plan-sha256 <plan-sha256>
                                    [--resume <dir>/run-manifest.json] [--out <dir>]
  next-campaigns-create.sh verify    --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json
  next-campaigns-create.sh teardown  --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --yes

Admin API token, first match wins:
  1. {STORE}_NEXT_ADMIN_API_TOKEN in the environment
     (mystore -> MYSTORE_NEXT_ADMIN_API_TOKEN, my-store -> MY_STORE_NEXT_ADMIN_API_TOKEN)
  2. the line {STORE}_NEXT_ADMIN_API_TOKEN=<token> in ./.env (read as text, never executed)
  3. NEXT_ADMIN_API_TOKEN in the environment
  Never pass the token on the command line.

Run files:
  discover writes ./next-campaigns-create-runs/<store>/discovery.json. recommend, apply
  and verify write next to the file they read; --out overrides. Inside a git repository
  the run directory must be gitignored: add "next-campaigns-create-runs/" to .gitignore.

Environment:
  NEXT_CAMPAIGNS_CREATE_PYTHON   Python 3.9+ interpreter to use (default: python3)

Exit codes:
  0 success. 1 refused or failed. 2 usage error, the apply gate, or no usable Python.
  Every exit 2 means nothing was sent to the store.

Without bash (Windows): run python3 <skill-dir>/scripts/campaign_admin.py with the same
arguments and the token set in the environment.
EOF
}

python_ok() {
  command -v "$PY" >/dev/null 2>&1 &&
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

require_python() {
  if ! command -v "$PY" >/dev/null 2>&1; then
    echo "next-campaigns-create: '$PY' not found; install Python 3.9 or newer, or set NEXT_CAMPAIGNS_CREATE_PYTHON" >&2
    exit 2
  fi
  if ! python_ok; then
    echo "next-campaigns-create: '$PY' is not Python 3.9 or newer; set NEXT_CAMPAIGNS_CREATE_PYTHON to one that is" >&2
    exit 2
  fi
  if [ ! -f "$ENGINE" ]; then
    echo "next-campaigns-create: engine not found at $ENGINE; reinstall the skill" >&2
    exit 2
  fi
}

case "${1:-}" in
  "")
    usage >&2
    exit 2
    ;;
  -h|--help|help)
    usage
    if python_ok && [ -f "$ENGINE" ]; then
      echo
      "$PY" "$ENGINE" --help || true
    fi
    exit 0
    ;;
  -V|--version)
    if [ ! -f "$SKILL_DIR/SKILL.md" ]; then
      echo "next-campaigns-create: SKILL.md not found in $SKILL_DIR" >&2
      exit 1
    fi
    version="$(awk '/^version:/ {gsub(/["\047]/, "", $2); print $2; exit}' "$SKILL_DIR/SKILL.md")"
    echo "next-campaigns-create ${version:-unknown}"
    exit 0
    ;;
esac

require_python
exec "$PY" "$ENGINE" "$@"
