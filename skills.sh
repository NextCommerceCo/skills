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
  ./skills.sh status --target <skills-dir> [skill|all]
  ./skills.sh dry-run [claude|codex|agents|all] [skill|all]  # deprecated alias: status
  ./skills.sh install [--force] [claude|codex|agents|all] [skill|all]
  ./skills.sh install [--force] --target <skills-dir> [skill|all]

Examples:
  ./skills.sh
  ./skills.sh status
  ./skills.sh install codex
  ./skills.sh install codex next-ops-scan
  ./skills.sh status --target /tmp/next-skills next-ops-scan
  ./skills.sh install --force --target /tmp/next-skills next-ops-scan

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
  python3 "$ROOT/scripts/skill_catalog.py" --root "$ROOT" list
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

compare_semver() {
  local left="$1"
  local right="$2"
  local left_major left_minor left_patch
  local right_major right_minor right_patch

  IFS=. read -r left_major left_minor left_patch <<< "$left"
  IFS=. read -r right_major right_minor right_patch <<< "$right"
  for component in major minor patch; do
    eval "left_value=\$left_${component}"
    eval "right_value=\$right_${component}"
    if (( 10#$left_value < 10#$right_value )); then
      printf '%s\n' "lt"
      return
    fi
    if (( 10#$left_value > 10#$right_value )); then
      printf '%s\n' "gt"
      return
    fi
  done
  printf '%s\n' "eq"
}

copy_skill() {
  local skill="$1"
  local target_dir="$2"
  local dry_run="$3"
  local force="$4"
  local src="$ROOT/$skill"
  local dest="$target_dir/$skill"
  local parent
  local tmp
  local parent_mode
  local diff_status
  local status="unchanged"
  local source_version
  local installed_version
  local version_relation

  source_version="$(awk '/^version:/ {gsub(/["\047]/, "", $2); print $2; exit}' "$src/SKILL.md")"
  installed_version="missing"

  parent="$(dirname "$dest")"
  if [[ -d "$parent" ]]; then
    parent_mode="$(stat -c '%a' "$parent" 2>/dev/null || stat -f '%Lp' "$parent")"
  else
    parent_mode="755"
  fi

  if [[ ! -d "$dest" ]]; then
    status="create"
  else
    if [[ -f "$dest/SKILL.md" ]]; then
      installed_version="$(awk '/^version:/ {gsub(/["\047]/, "", $2); print $2; exit}' "$dest/SKILL.md")"
      installed_version="${installed_version:-unknown}"
    else
      installed_version="unknown"
    fi
    if diff -qr "$src" "$dest" >/dev/null 2>&1; then
      diff_status=0
    else
      diff_status=$?
    fi
    if [[ "$diff_status" -eq 1 ]]; then
      if [[ "$source_version" =~ ^[0-9]+([.][0-9]+){2}$ && "$installed_version" =~ ^[0-9]+([.][0-9]+){2}$ ]]; then
        version_relation="$(compare_semver "$installed_version" "$source_version")"
        if [[ "$version_relation" == "lt" ]]; then
          status="stale"
        elif [[ "$version_relation" == "gt" ]]; then
          status="local-newer"
        else
          status="modified"
        fi
      else
        status="unknown-version"
      fi
    elif [[ "$diff_status" -gt 1 ]]; then
      echo "Failed to compare $src and $dest" >&2
      return 1
    fi
  fi

  if [[ "$status" == "unchanged" ]]; then
    printf '%-12s %s source=%s installed=%s -> %s\n' \
      "$status" "$skill" "${source_version:-unknown}" "$installed_version" "$dest"
    return 0
  fi

  printf '%-12s %s source=%s installed=%s -> %s\n' \
    "$status" "$skill" "${source_version:-unknown}" "$installed_version" "$dest"

  if [[ "$dry_run" == "false" ]]; then
    if [[ "$force" != "true" && "$status" =~ ^(modified|local-newer|unknown-version)$ ]]; then
      echo "Refusing to overwrite $status skill $dest; review it and rerun install with --force." >&2
      return 1
    fi
    if ! mkdir -p "$parent"; then
      echo "Failed to create target parent $parent" >&2
      return 1
    fi
    parent_mode="$(stat -c '%a' "$parent" 2>/dev/null || stat -f '%Lp' "$parent")"

    if ! tmp="$(mktemp -d "$parent/.${skill##*/}.tmp.XXXXXX")"; then
      echo "Failed to create staging directory in $parent" >&2
      return 1
    fi
    if ! cp -Rp "$src/." "$tmp/"; then
      rm -rf -- "$tmp"
      echo "Failed to stage $skill for $dest" >&2
      return 1
    fi
    if ! chmod -R u+rwX,go+rX "$tmp"; then
      rm -rf -- "$tmp"
      echo "Failed to normalize staged permissions for $tmp" >&2
      return 1
    fi
    if ! chmod "$parent_mode" "$tmp"; then
      rm -rf -- "$tmp"
      echo "Failed to set staged directory mode for $tmp" >&2
      return 1
    fi

    if [[ -e "$dest" ]]; then
      if ! command -v rsync >/dev/null 2>&1; then
        rm -rf -- "$tmp"
        echo "rsync is required to update existing skill directory $dest without removing it first" >&2
        return 1
      fi
      if ! rsync -a --delete "$tmp/" "$dest/"; then
        rm -rf -- "$tmp"
        echo "Failed to sync staged $skill into $dest" >&2
        return 1
      fi
      rm -rf -- "$tmp"
    elif ! mv "$tmp" "$dest"; then
      rm -rf -- "$tmp"
      echo "Failed to install $skill to $dest" >&2
      return 1
    fi
  fi
}

sync_target() {
  local target_dir="$1"
  local skill_filter="$2"
  local dry_run="$3"
  local force="$4"
  local skill
  local resolved
  local failed=0

  printf '\nTarget: %s\n' "$target_dir"
  resolved="$(resolve_skills "$skill_filter")" || return 2
  while IFS= read -r skill; do
    [[ -n "$skill" ]] || continue
    if ! copy_skill "$skill" "$target_dir" "$dry_run" "$force"; then
      failed=1
    fi
  done <<< "$resolved"
  return "$failed"
}

sync_platform() {
  local platform="$1"
  local skill_filter="$2"
  local dry_run="$3"
  local force="$4"
  local failed=0
  local target_dir
  local canonical
  local seen_targets=""
  local rc

  if [[ "$platform" == "all" ]]; then
    for platform in claude codex agents; do
      target_dir="$(platform_target "$platform")" || return 2
      canonical="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).expanduser().resolve(strict=False))' "$target_dir")"
      if printf '%s\n' "$seen_targets" | grep -Fqx "$canonical"; then
        printf '\nSkipping duplicate target: %s -> %s\n' "$target_dir" "$canonical"
        continue
      fi
      seen_targets="${seen_targets}${canonical}"$'\n'
      sync_target "$target_dir" "$skill_filter" "$dry_run" "$force" || {
        rc=$?
        [[ "$rc" -eq 2 ]] && return 2
        [[ "$failed" -lt "$rc" ]] && failed="$rc"
      }
    done
    return "$failed"
  fi

  target_dir="$(platform_target "$platform")" || return 2
  sync_target "$target_dir" "$skill_filter" "$dry_run" "$force"
}

guided() {
  if [[ ! -t 0 ]]; then
    sync_platform all all true false
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
  sync_platform "$platform" "$skill_filter" true false

  echo
  printf 'Install these changes? [y/N]: '
  read -r confirm
  case "$confirm" in
    y|Y|yes|YES)
      if ! sync_platform "$platform" "$skill_filter" false false; then
        echo
        echo "Install completed with errors." >&2
        return 1
      fi
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
    [[ $# -eq 0 ]] || { echo "Too many arguments for list." >&2; exit 2; }
    list_skills
    ;;
  status)
    if [[ "${1:-}" == "--target" ]]; then
      target="${2:-}"
      skill_filter="${3:-all}"
      [[ -n "$target" ]] || { echo "Missing --target directory." >&2; exit 2; }
      [[ $# -le 3 ]] || { echo "Too many arguments for status --target." >&2; exit 2; }
      sync_target "$target" "$skill_filter" true false
    else
      platform="${1:-all}"
      skill_filter="${2:-all}"
      [[ $# -le 2 ]] || { echo "Too many arguments for status." >&2; exit 2; }
      sync_platform "$platform" "$skill_filter" true false
    fi
    ;;
  dry-run)
    echo "warning: dry-run is deprecated; use status" >&2
    if [[ "${1:-}" == "--target" ]]; then
      target="${2:-}"
      skill_filter="${3:-all}"
      [[ -n "$target" ]] || { echo "Missing --target directory." >&2; exit 2; }
      [[ $# -le 3 ]] || { echo "Too many arguments for dry-run --target." >&2; exit 2; }
      sync_target "$target" "$skill_filter" true false
    else
      platform="${1:-all}"
      skill_filter="${2:-all}"
      [[ $# -le 2 ]] || { echo "Too many arguments for dry-run." >&2; exit 2; }
      sync_platform "$platform" "$skill_filter" true false
    fi
    ;;
  install)
    force="false"
    if [[ "${1:-}" == "--force" ]]; then
      force="true"
      shift
    fi
    if [[ "${1:-}" == "--target" ]]; then
      target="${2:-}"
      skill_filter="${3:-all}"
      [[ -n "$target" ]] || { echo "Missing --target directory." >&2; exit 2; }
      [[ $# -le 3 ]] || { echo "Too many arguments for install --target." >&2; exit 2; }
      if ! sync_target "$target" "$skill_filter" false "$force"; then
        echo
        echo "Install completed with errors." >&2
        exit 1
      fi
      echo
      echo "Done. Restart local agent sessions so refreshed skills are loaded."
    else
      platform="${1:-all}"
      skill_filter="${2:-all}"
      [[ $# -le 2 ]] || { echo "Too many arguments for install." >&2; exit 2; }
      if ! sync_platform "$platform" "$skill_filter" false "$force"; then
        echo
        echo "Install completed with errors." >&2
        exit 1
      fi
      echo
      echo "Done. Restart local agent sessions so refreshed skills are loaded."
    fi
    ;;
  claude|codex|agents|all)
    skill_filter="${1:-all}"
    [[ $# -le 1 ]] || { echo "Too many arguments for $action." >&2; exit 2; }
    if ! sync_platform "$action" "$skill_filter" false false; then
      echo
      echo "Install completed with errors." >&2
      exit 1
    fi
    echo
    echo "Done. Restart local agent sessions so refreshed skills are loaded."
    ;;
  guided)
    [[ $# -eq 0 ]] || { echo "Too many arguments for guided." >&2; exit 2; }
    guided
    ;;
  help|-h|--help)
    [[ $# -eq 0 ]] || { echo "Too many arguments for help." >&2; exit 2; }
    usage
    ;;
  *)
    echo "Unknown command: $action" >&2
    usage >&2
    exit 2
    ;;
esac
