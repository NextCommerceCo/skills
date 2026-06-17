#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<'EOF'
Next Commerce local skill helper

Usage:
  ./skills.sh
  ./skills.sh list
  ./skills.sh status [claude|codex|agents|all] [skill|all]
  ./skills.sh dry-run [claude|codex|agents|all] [skill|all]
  ./skills.sh install [claude|codex|agents|all] [skill|all]
  ./skills.sh install --target <skills-dir> [skill|all]

Examples:
  ./skills.sh
  ./skills.sh status
  ./skills.sh install codex
  ./skills.sh install codex next-ops-scan
  ./skills.sh dry-run --target /tmp/next-skills next-ops-scan

Targets:
  claude  -> ~/.claude/skills
  codex   -> ~/.codex/skills
  agents  -> ~/.agents/skills
  all     -> all of the above
EOF
}

platform_target() {
  case "$1" in
    claude) printf '%s\n' "$HOME/.claude/skills" ;;
    codex) printf '%s\n' "$HOME/.codex/skills" ;;
    agents) printf '%s\n' "$HOME/.agents/skills" ;;
    *)
      echo "Unknown target: $1" >&2
      return 2
      ;;
  esac
}

skill_ids() {
  find "$ROOT" -mindepth 2 -maxdepth 2 -name SKILL.md -print |
    sed "s#^$ROOT/##; s#/SKILL.md##" |
    sort
}

is_skill_id() {
  local needle="$1"
  local id
  while IFS= read -r id; do
    [[ "$id" == "$needle" ]] && return 0
  done < <(skill_ids)
  return 1
}

list_skills() {
  local id
  while IFS= read -r id; do
    printf '  %s\n' "$id"
  done < <(skill_ids)
}

resolve_skills() {
  local requested="${1:-all}"
  if [[ "$requested" == "all" ]]; then
    skill_ids
    return
  fi
  if ! is_skill_id "$requested"; then
    echo "Unknown skill: $requested" >&2
    echo "Available skills:" >&2
    list_skills >&2
    return 2
  fi
  printf '%s\n' "$requested"
}

copy_skill() {
  local skill="$1"
  local target_dir="$2"
  local dry_run="$3"
  local src="$ROOT/$skill"
  local dest="$target_dir/$skill"
  local status="unchanged"

  if [[ ! -d "$dest" ]]; then
    status="create"
  elif ! diff -qr "$src" "$dest" >/dev/null 2>&1; then
    status="update"
  fi

  printf '%-10s %s -> %s\n' "$status" "$skill" "$dest"

  if [[ "$dry_run" == "false" && "$status" != "unchanged" ]]; then
    mkdir -p "$target_dir"
    rm -rf -- "$dest"
    cp -R "$src" "$dest"
  fi
}

sync_target() {
  local target_dir="$1"
  local skill_filter="$2"
  local dry_run="$3"
  local skill
  local resolved

  printf '\nTarget: %s\n' "$target_dir"
  resolved="$(resolve_skills "$skill_filter")" || return 2
  while IFS= read -r skill; do
    [[ -n "$skill" ]] || continue
    copy_skill "$skill" "$target_dir" "$dry_run"
  done <<< "$resolved"
}

sync_platform() {
  local platform="$1"
  local skill_filter="$2"
  local dry_run="$3"

  if [[ "$platform" == "all" ]]; then
    sync_platform claude "$skill_filter" "$dry_run"
    sync_platform codex "$skill_filter" "$dry_run"
    sync_platform agents "$skill_filter" "$dry_run"
    return
  fi

  sync_target "$(platform_target "$platform")" "$skill_filter" "$dry_run"
}

guided() {
  if [[ ! -t 0 ]]; then
    sync_platform all all true
    return
  fi

  echo "Next Commerce skill installer"
  echo
  echo "Where should skills be installed?"
  echo "  1) Claude Code (~/.claude/skills)"
  echo "  2) Codex (~/.codex/skills)"
  echo "  3) Shared agent skills (~/.agents/skills)"
  echo "  4) All of the above"
  printf 'Choose [1-4, default 4]: '
  read -r platform_choice
  case "${platform_choice:-4}" in
    1) platform="claude" ;;
    2) platform="codex" ;;
    3) platform="agents" ;;
    4) platform="all" ;;
    *)
      echo "Invalid choice: $platform_choice" >&2
      exit 2
      ;;
  esac

  echo
  echo "Available skills:"
  list_skills
  echo
  printf 'Skill to install [default all]: '
  read -r skill_filter
  skill_filter="${skill_filter:-all}"

  echo
  echo "Preview:"
  sync_platform "$platform" "$skill_filter" true

  echo
  printf 'Install these changes? [y/N]: '
  read -r confirm
  case "$confirm" in
    y|Y|yes|YES)
      sync_platform "$platform" "$skill_filter" false
      echo
      echo "Done. Restart local agent sessions so refreshed skills are loaded."
      ;;
    *)
      echo "No changes written."
      ;;
  esac
}

action="${1:-guided}"
shift || true

case "$action" in
  list)
    list_skills
    ;;
  status)
    platform="${1:-all}"
    skill_filter="${2:-all}"
    sync_platform "$platform" "$skill_filter" true
    ;;
  dry-run)
    if [[ "${1:-}" == "--target" ]]; then
      target="${2:-}"
      skill_filter="${3:-all}"
      [[ -n "$target" ]] || { echo "Missing --target directory." >&2; exit 2; }
      sync_target "$target" "$skill_filter" true
    else
      platform="${1:-all}"
      skill_filter="${2:-all}"
      sync_platform "$platform" "$skill_filter" true
    fi
    ;;
  install)
    if [[ "${1:-}" == "--target" ]]; then
      target="${2:-}"
      skill_filter="${3:-all}"
      [[ -n "$target" ]] || { echo "Missing --target directory." >&2; exit 2; }
      sync_target "$target" "$skill_filter" false
    else
      platform="${1:-all}"
      skill_filter="${2:-all}"
      sync_platform "$platform" "$skill_filter" false
      echo
      echo "Done. Restart local agent sessions so refreshed skills are loaded."
    fi
    ;;
  claude|codex|agents|all)
    skill_filter="${1:-all}"
    sync_platform "$action" "$skill_filter" false
    echo
    echo "Done. Restart local agent sessions so refreshed skills are loaded."
    ;;
  guided)
    guided
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: $action" >&2
    usage >&2
    exit 2
    ;;
esac
