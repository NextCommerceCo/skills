#!/usr/bin/env python3
"""Validate public skill manifest parity and require bumps for changed packages."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FRONTMATTER_VERSION_RE = re.compile(r"^version:\s*['\"]?([^'\"\s]+)", re.MULTILINE)


def semver(value: str) -> tuple[int, int, int]:
    match = SEMVER_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid semver: {value!r}")
    return tuple(int(part) for part in match.groups())


def run_git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(root), *args], text=True, stderr=subprocess.STDOUT
    )


def load_manifest_text(text: str, label: str) -> dict:
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f"{label}: invalid JSON: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("skills"), list):
        raise ValueError(f"{label}: expected an object with a skills array")
    return manifest


def version_map(manifest: dict, label: str) -> dict[str, str]:
    versions: dict[str, str] = {}
    for index, entry in enumerate(manifest["skills"]):
        if not isinstance(entry, dict):
            raise ValueError(f"{label}[{index}]: expected an object")
        name = entry.get("id")
        version = entry.get("version")
        if not isinstance(name, str) or not name:
            raise ValueError(f"{label}[{index}]: missing id")
        if not isinstance(version, str):
            raise ValueError(f"{label}[{index}] ({name}): missing version")
        semver(version)
        if name in versions:
            raise ValueError(f"{label}: duplicate skill id {name!r}")
        versions[name] = version
    return versions


def changed_skill_ids(paths: list[str], known_ids: set[str]) -> set[str]:
    changed: set[str] = set()
    for path in paths:
        top_level = path.split("/", 1)[0]
        if top_level in known_ids:
            changed.add(top_level)
    return changed


def validate(root: Path, base: str | None = None) -> list[str]:
    errors: list[str] = []
    manifest_path = root / "skills.json"
    try:
        manifest = load_manifest_text(manifest_path.read_text(), str(manifest_path))
        current_versions = version_map(manifest, "skills.json.skills")
    except (OSError, ValueError) as error:
        return [str(error)]

    for entry in manifest["skills"]:
        skill_id = entry["id"]
        skill_path = root / entry.get("path", "")
        if not skill_path.is_file():
            errors.append(f"{skill_id}: SKILL.md missing at {skill_path}")
            continue
        match = FRONTMATTER_VERSION_RE.search(skill_path.read_text())
        if not match:
            errors.append(f"{skill_path}: version missing from frontmatter")
        elif match.group(1) != entry["version"]:
            errors.append(
                f"{skill_path}: frontmatter version {match.group(1)!r} does not "
                f"match skills.json {entry['version']!r}"
            )

    if not base:
        return errors

    try:
        old_manifest = load_manifest_text(
            run_git(root, "show", f"{base}:skills.json"), f"{base}:skills.json"
        )
        old_versions = version_map(old_manifest, f"{base}:skills.json.skills")
        changed_paths = run_git(root, "diff", "--name-only", f"{base}...HEAD").splitlines()
    except (subprocess.CalledProcessError, ValueError) as error:
        errors.append(f"base comparison failed for {base!r}: {error}")
        return errors

    for skill_id in sorted(changed_skill_ids(changed_paths, set(current_versions) | set(old_versions))):
        if skill_id not in old_versions or skill_id not in current_versions:
            continue
        old_version = old_versions[skill_id]
        current_version = current_versions[skill_id]
        if semver(current_version) <= semver(old_version):
            errors.append(
                f"{skill_id}: package changed but version did not advance "
                f"({old_version} -> {current_version})"
            )

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", help="base commit/ref used to detect changed packages")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()

    errors = validate(args.root.resolve(), args.base)
    if errors:
        print("Skill version validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Skill versions are valid" + (f" against {args.base}" if args.base else "") + ".")
    return 0


if __name__ == "__main__":
    sys.exit(main())
