#!/usr/bin/env python3
"""Validate and render the public skill catalog.

``skills.json`` is the canonical distribution seam. The installer, CI, and the
generated README table all consume this module so a skill cannot be installable
while remaining absent from the public catalog (or vice versa).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
SKILL_ID_RE = re.compile(r"^next-[a-z0-9]+(?:-[a-z0-9]+)*$")
FRONTMATTER_FIELD_RE = re.compile(
    r"^(?P<field>name|version):\s*['\"]?(?P<value>[^'\"\s]+)", re.MULTILINE
)
README_TABLE_START = "<!-- BEGIN GENERATED SKILLS TABLE -->"
README_TABLE_END = "<!-- END GENERATED SKILLS TABLE -->"


class CatalogError(ValueError):
    """Raised when a catalog or generated surface violates the contract."""


def load_manifest_text(text: str, label: str) -> dict:
    try:
        manifest = json.loads(text)
    except json.JSONDecodeError as error:
        raise CatalogError(f"{label}: invalid JSON: {error}") from error
    if not isinstance(manifest, dict) or not isinstance(manifest.get("skills"), list):
        raise CatalogError(f"{label}: expected an object with a skills array")
    return manifest


def _nonempty_string(entry: dict, field: str, label: str) -> str:
    value = entry.get(field)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{label}: {field} must be a non-empty string")
    return value


def _string_list(entry: dict, field: str, label: str) -> list[str]:
    value = entry.get(field)
    if not isinstance(value, list) or not value:
        raise CatalogError(f"{label}: {field} must be a non-empty array")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise CatalogError(f"{label}: {field} entries must be non-empty strings")
    return value


def catalog_entries(manifest: dict, label: str = "skills.json.skills") -> list[dict]:
    entries: list[dict] = []
    seen: set[str] = set()
    for index, entry in enumerate(manifest["skills"]):
        item_label = f"{label}[{index}]"
        if not isinstance(entry, dict):
            raise CatalogError(f"{item_label}: expected an object")

        skill_id = _nonempty_string(entry, "id", item_label)
        if not SKILL_ID_RE.fullmatch(skill_id):
            raise CatalogError(f"{item_label}: invalid skill id {skill_id!r}")
        if skill_id in seen:
            raise CatalogError(f"{label}: duplicate skill id {skill_id!r}")
        seen.add(skill_id)

        _nonempty_string(entry, "name", item_label)
        version = _nonempty_string(entry, "version", item_label)
        if not SEMVER_RE.fullmatch(version):
            raise CatalogError(f"{item_label} ({skill_id}): invalid semver {version!r}")
        expected_path = f"{skill_id}/SKILL.md"
        path = _nonempty_string(entry, "path", item_label)
        if path != expected_path:
            raise CatalogError(
                f"{item_label} ({skill_id}): path must be {expected_path!r}, got {path!r}"
            )
        _nonempty_string(entry, "domain", item_label)
        _nonempty_string(entry, "description", item_label)
        _string_list(entry, "triggers", item_label)
        _string_list(entry, "prerequisites", item_label)
        if not isinstance(entry.get("safety"), dict):
            raise CatalogError(f"{item_label} ({skill_id}): safety must be an object")
        entries.append(entry)
    return entries


def _frontmatter_fields(skill_path: Path) -> dict[str, str]:
    text = skill_path.read_text(encoding="utf-8")
    return {
        match.group("field"): match.group("value")
        for match in FRONTMATTER_FIELD_RE.finditer(text)
    }


def validate_catalog(root: Path, *, check_readme: bool = False) -> list[str]:
    errors: list[str] = []
    manifest_path = root / "skills.json"
    try:
        manifest = load_manifest_text(
            manifest_path.read_text(encoding="utf-8"), str(manifest_path)
        )
        entries = catalog_entries(manifest)
    except (OSError, CatalogError) as error:
        return [str(error)]

    manifest_ids = {entry["id"] for entry in entries}
    package_ids = {
        path.parent.name
        for path in root.glob("next-*/SKILL.md")
        if path.parent.parent == root
    }
    for skill_id in sorted(package_ids - manifest_ids):
        errors.append(f"{skill_id}: package has SKILL.md but is missing from skills.json")
    for skill_id in sorted(manifest_ids - package_ids):
        errors.append(f"{skill_id}: listed in skills.json but package SKILL.md is missing")

    for entry in entries:
        skill_id = entry["id"]
        package_dir = root / skill_id
        skill_path = root / entry["path"]
        readme_path = package_dir / "README.md"
        if not readme_path.is_file():
            errors.append(f"{skill_id}: README.md missing at {readme_path}")
        if not skill_path.is_file():
            continue
        fields = _frontmatter_fields(skill_path)
        for field in ("name", "version"):
            if field not in fields:
                errors.append(f"{skill_path}: {field} missing from frontmatter")
        if fields.get("name") not in (None, skill_id):
            errors.append(
                f"{skill_path}: frontmatter name {fields['name']!r} does not match {skill_id!r}"
            )
        if fields.get("version") not in (None, entry["version"]):
            errors.append(
                f"{skill_path}: frontmatter version {fields['version']!r} does not "
                f"match skills.json {entry['version']!r}"
            )

    if check_readme:
        try:
            readme = (root / "README.md").read_text(encoding="utf-8")
            expected = render_readme_table(entries)
            actual = generated_readme_table(readme)
            if actual != expected:
                errors.append(
                    "README.md generated skills table is stale; run "
                    "python3 scripts/skill_catalog.py readme --write"
                )
        except (OSError, CatalogError) as error:
            errors.append(str(error))

    return errors


def _table_cell(value: str) -> str:
    return " ".join(value.split()).replace("|", "\\|")


def render_readme_table(entries: list[dict]) -> str:
    lines = [
        README_TABLE_START,
        "| Skill | Domain | What It Does |",
        "|-------|--------|--------------|",
    ]
    for entry in entries:
        lines.append(
            "| "
            f"[**{_table_cell(entry['name'])}**]({entry['id']}/) | "
            f"{_table_cell(entry['domain'].replace('-', ' ').title())} | "
            f"{_table_cell(entry['description'])} |"
        )
    lines.append(README_TABLE_END)
    return "\n".join(lines)


def generated_readme_table(readme: str) -> str:
    start = readme.find(README_TABLE_START)
    end = readme.find(README_TABLE_END)
    if start < 0 or end < 0 or end < start:
        raise CatalogError("README.md: generated skills table markers are missing or invalid")
    return readme[start : end + len(README_TABLE_END)]


def write_readme_table(root: Path, entries: list[dict]) -> None:
    readme_path = root / "README.md"
    readme = readme_path.read_text(encoding="utf-8")
    current = generated_readme_table(readme)
    updated = readme.replace(current, render_readme_table(entries), 1)
    readme_path.write_text(updated, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--check-readme", action="store_true")
    subparsers.add_parser("list")
    readme_parser = subparsers.add_parser("readme")
    readme_parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()

    try:
        manifest = load_manifest_text(
            (root / "skills.json").read_text(encoding="utf-8"), "skills.json"
        )
        entries = catalog_entries(manifest)
    except (OSError, CatalogError) as error:
        print(error, file=sys.stderr)
        return 1

    if args.command == "list":
        errors = validate_catalog(root)
        if errors:
            for error in errors:
                print(error, file=sys.stderr)
            return 1
        for entry in entries:
            print(entry["id"])
        return 0

    if args.command == "readme" and args.write:
        try:
            write_readme_table(root, entries)
        except (OSError, CatalogError) as error:
            print(error, file=sys.stderr)
            return 1
        return 0

    errors = validate_catalog(root, check_readme=args.command == "validate" and args.check_readme)
    if errors:
        print("Skill catalog validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1
    print("Skill catalog is valid.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
