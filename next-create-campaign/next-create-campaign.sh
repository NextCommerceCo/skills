#!/usr/bin/env bash
# next-create-campaign: provision a Campaigns App campaign over the NEXT Admin API.
#
# Launcher for scripts/campaign_admin.py. It prints the skill version, checks for
# Python 3.9+, and forwards every other argument to the engine unchanged. The
# engine owns argument parsing and credential loading, so the store a command
# targets and the token it uses always come from the same --store value.
#
# check-update runs scripts/update_check.py instead, never the engine. It is
# advisory: it always exits 0, even when Python or the checker is missing.
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
UPDATER="$SKILL_DIR/scripts/update_check.py"
CATALOG_PAGE="https://github.com/NextCommerceCo/skills/blob/main/skills.json"
PY="${NEXT_CREATE_CAMPAIGN_PYTHON:-python3}"

usage() {
  cat <<'EOF'
next-create-campaign: provision a Campaigns App campaign over the NEXT Admin API

Usage:
  next-create-campaign.sh --version
  next-create-campaign.sh --help
  next-create-campaign.sh check-update [--no-cache] [--json]
  next-create-campaign.sh discover  --store <subdomain> [--out <dir>]
  next-create-campaign.sh metadata  --store <subdomain> [--apply]
  next-create-campaign.sh recommend --discovery <dir>/discovery.json --hero <product_id>
                                    --ctc low|high --anchor-price <decimal>
                                    --shipping <code>:<price>[:<key>] [options]
  next-create-campaign.sh plan      --plan <dir>/campaign-plan.json [--check-store]
  next-create-campaign.sh apply     --plan <dir>/campaign-plan.json --yes --plan-sha256 <plan-sha256>
                                    [--resume <dir>/run-manifest.json] [--out <dir>]
  next-create-campaign.sh verify    --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json
  next-create-campaign.sh teardown  --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --yes

Admin API token, first match wins:
  1. {STORE}_NEXT_ADMIN_API_TOKEN in the environment
     (mystore -> MYSTORE_NEXT_ADMIN_API_TOKEN, my-store -> MY_STORE_NEXT_ADMIN_API_TOKEN)
  2. the line {STORE}_NEXT_ADMIN_API_TOKEN=<token> in ./.env (read as text, never executed)
  3. NEXT_ADMIN_API_TOKEN in the environment
  Never pass the token on the command line.

Run files:
  discover writes ./next-create-campaign-runs/<store>/discovery.json. recommend, apply
  and verify write next to the file they read; --out overrides. Inside a git repository
  the run directory must be gitignored: add "next-create-campaign-runs/" to .gitignore.

Update check:
  check-update prints the installed version, then whether a newer version is published
  and how to update this copy. One read-only request to GitHub, cached for 24 hours.
  It never installs anything and always exits 0.

Environment:
  NEXT_CREATE_CAMPAIGN_PYTHON   Python 3.9+ interpreter to use (default: python3)
  NEXT_SKILLS_NO_UPDATE_CHECK   set to 1 to skip the check-update request
  NEXT_SKILLS_CHECK_TIMEOUT     seconds before check-update gives up (default: 10)

Exit codes:
  0 success. 1 refused or failed. 2 usage error, the apply gate, or no usable Python.
  Every exit 2 means nothing was sent to the store. check-update always exits 0.

Without bash (Windows): run python3 <skill-dir>/scripts/campaign_admin.py with the same
arguments and the token set in the environment.
EOF
}

skill_version() {
  awk '/^version:/ {gsub(/["\047]/, "", $2); print $2; exit}' "$SKILL_DIR/SKILL.md"
}

python_ok() {
  command -v "$PY" >/dev/null 2>&1 &&
    "$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1
}

require_python() {
  if ! command -v "$PY" >/dev/null 2>&1; then
    echo "next-create-campaign: '$PY' not found; install Python 3.9 or newer, or set NEXT_CREATE_CAMPAIGN_PYTHON" >&2
    exit 2
  fi
  if ! python_ok; then
    echo "next-create-campaign: '$PY' is not Python 3.9 or newer; set NEXT_CREATE_CAMPAIGN_PYTHON to one that is" >&2
    exit 2
  fi
  if [ ! -f "$ENGINE" ]; then
    echo "next-create-campaign: engine not found at $ENGINE; reinstall the skill" >&2
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
      echo "next-create-campaign: SKILL.md not found in $SKILL_DIR" >&2
      exit 1
    fi
    version="$(skill_version)"
    echo "next-create-campaign ${version:-unknown}"
    exit 0
    ;;
  check-update)
    shift
    json=false
    for arg in "$@"; do
      [ "$arg" = "--json" ] && json=true
    done
    if [ "$json" = false ]; then
      version=""
      [ -f "$SKILL_DIR/SKILL.md" ] && version="$(skill_version 2>/dev/null || true)"
      echo "next-create-campaign ${version:-unknown}"
    fi
    limit="${NEXT_SKILLS_CHECK_TIMEOUT:-10}"
    case "$limit" in ''|*[!0-9]*) limit=10 ;; esac
    if python_ok && [ -f "$UPDATER" ]; then
      # A watchdog bounds the whole check, including a hung interpreter start or a
      # shim that forks. set -m gives the child its own process group, so the
      # watchdog stops every process that could hold the output pipe open.
      set -m
      "$PY" "$UPDATER" "$@" &
      child=$!
      ( sleep "$limit" && kill -TERM -- "-$child" ) >/dev/null 2>&1 &
      watchdog=$!
      set +m
      rc=0
      wait "$child" || rc=$?
      kill -TERM -- "-$watchdog" >/dev/null 2>&1 || true
      if [ "$rc" -eq 0 ]; then
        exit 0
      fi
    fi
    if [ "$json" = true ]; then
      printf '{"status": "could-not-check", "lines": ["Could not check for updates.", "  Latest is listed at %s"], "catalog": "%s"}\n' "$CATALOG_PAGE" "$CATALOG_PAGE"
    else
      echo "Could not check for updates."
      echo "  Latest is listed at $CATALOG_PAGE"
    fi
    exit 0
    ;;
esac

require_python
exec "$PY" "$ENGINE" "$@"
