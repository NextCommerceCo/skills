#!/usr/bin/env python3
"""Diff built theme templates against the handoff copy manifest.

The copy manifest (``copy.json``) is the verbatim text inventory extracted from
the Figma text layers at handoff time. This lint reads the built templates and
fails on any visible copy string that is not in that inventory and not covered
by a recorded allowed deviation.

It exists because invented copy is cheap to write and expensive to find: it
surfaces in a review round, days after the section was built, and costs a full
fix round per occurrence. Run it in the builder and repair gates so the seat
sees the failure before it reports done.

    python3 copy-lint.py --package docs/handoff/example-figma \\
        --templates partials --templates templates \\
        --report /tmp/copy-lint.json

Exit codes: 0 clean, 1 copy drift found, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

SCHEMA = "next-theme-figma/copy/v1"
REPORT_SCHEMA = "next-theme-figma/copy-lint-report/v1"

DEFAULT_TEMPLATE_SUFFIXES = (".html", ".htm", ".dtl")
DEFAULT_MIN_LENGTH = 12

# Masked to spaces before text extraction so offsets stay aligned with the
# source file and every reported hit keeps a real line number.
MASKED_REGIONS = (
    re.compile(r"<!--.*?-->", re.DOTALL),
    re.compile(r"<script\b.*?</script\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"<style\b.*?</style\s*>", re.DOTALL | re.IGNORECASE),
    re.compile(r"\{#.*?#\}", re.DOTALL),
    # The block form hides prose from the renderer, so it is not built copy.
    re.compile(r"\{%\s*comment\b.*?\{%\s*endcomment\s*%\}", re.DOTALL | re.IGNORECASE),
    re.compile(r"\{%.*?%\}", re.DOTALL),
    re.compile(r"\{\{.*?\}\}", re.DOTALL),
)

DTL_DEFAULT_RE = re.compile(
    r"""\|\s*default:\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")"""
)
TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
COPY_ATTRIBUTE_RE = re.compile(
    r"""\b(?:alt|title|placeholder|aria-label)\s*=\s*(?:'([^']*)'|"([^"]*)")""",
    re.IGNORECASE,
)
LETTER_RE = re.compile(r"[A-Za-z]")
URL_LIKE_RE = re.compile(r"^(?:https?://|//|/|\./|\.\./|#|mailto:|tel:)")
ASSET_LIKE_RE = re.compile(r"\.(?:png|jpe?g|webp|svg|gif|css|js|woff2?)$", re.IGNORECASE)


def normalize(text: str) -> str:
    """Fold the differences that never matter for copy identity."""
    folded = unicodedata.normalize("NFKC", html.unescape(text))
    for source, target in (
        ("“", '"'), ("”", '"'),
        ("‘", "'"), ("’", "'"),
        ("—", "-"), ("–", "-"),
        (" ", " "),
    ):
        folded = folded.replace(source, target)
    return re.sub(r"\s+", " ", folded).strip()


def mask_regions(text: str) -> str:
    masked = text
    for pattern in MASKED_REGIONS:
        masked = pattern.sub(lambda match: " " * len(match.group(0)), masked)
    return masked


def line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def is_copy_candidate(value: str, min_length: int) -> bool:
    stripped = value.strip()
    if len(stripped) < min_length:
        return False
    if not LETTER_RE.search(stripped):
        return False
    if URL_LIKE_RE.match(stripped) or ASSET_LIKE_RE.search(stripped):
        return False
    if any(char in stripped for char in "{}<>"):
        return False
    return True


def extract_candidates(text: str, min_length: int) -> list[dict]:
    """Return every visible copy string in one template, with its offset."""
    found: list[dict] = []

    def add(value: str, offset: int, kind: str) -> None:
        if is_copy_candidate(value, min_length):
            found.append({"text": value.strip(), "offset": offset, "kind": kind})

    # DTL default literals live inside {{ ... }}, so they are read before the
    # expression regions are masked away.
    for match in DTL_DEFAULT_RE.finditer(text):
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        add(literal, match.start(), "dtl-default")

    masked = mask_regions(text)

    for match in COPY_ATTRIBUTE_RE.finditer(masked):
        value = match.group(1) if match.group(1) is not None else match.group(2)
        add(value, match.start(), "attribute")

    # Everything outside a tag is rendered text.
    cursor = 0
    for match in TAG_RE.finditer(masked):
        add(masked[cursor:match.start()], cursor, "text")
        cursor = match.end()
    add(masked[cursor:], cursor, "text")

    return found


def load_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != SCHEMA:
        raise ValueError(f'{path}: schema_version must be "{SCHEMA}"')
    if not isinstance(manifest.get("strings"), list):
        raise ValueError(f"{path}: strings must be an array")
    return manifest


def build_corpus(manifest: dict) -> str:
    return "\n".join(normalize(entry.get("text", "")) for entry in manifest["strings"])


def build_deviations(manifest: dict) -> list[dict]:
    deviations = []
    for entry in manifest.get("allowed_deviations", []) or []:
        record = {
            "deviation_id": entry.get("deviation_id", ""),
            "reason": entry.get("reason", ""),
            "text": normalize(entry["text"]) if entry.get("text") else None,
            "pattern": re.compile(entry["pattern"]) if entry.get("pattern") else None,
        }
        deviations.append(record)
    return deviations


def deviation_for(candidate: str, normalized: str, deviations: list[dict]) -> dict | None:
    for deviation in deviations:
        if deviation["text"] is not None and deviation["text"] == normalized:
            return deviation
        if deviation["pattern"] is not None and deviation["pattern"].search(candidate):
            return deviation
    return None


def template_files(paths: list[str], suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = Path(raw)
        if path.is_dir():
            files.extend(
                child for child in sorted(path.rglob("*"))
                if child.is_file() and child.suffix.lower() in suffixes
            )
        elif path.is_file():
            files.append(path)
        else:
            raise ValueError(f"{raw}: not a file or directory")
    return files


def lint(
    manifest: dict,
    files: list[Path],
    min_length: int,
    require_coverage: bool,
) -> dict:
    corpus = build_corpus(manifest)
    deviations = build_deviations(manifest)
    violations: list[dict] = []
    allowed: list[dict] = []
    checked = 0
    matched_texts: set[str] = set()

    for file in files:
        text = file.read_text(encoding="utf-8", errors="replace")
        for candidate in extract_candidates(text, min_length):
            checked += 1
            normalized = normalize(candidate["text"])
            if not normalized:
                continue
            record = {
                "file": str(file),
                "line": line_number(text, candidate["offset"]),
                "kind": candidate["kind"],
                "text": candidate["text"][:200],
            }
            if normalized in corpus:
                matched_texts.add(normalized)
                continue
            deviation = deviation_for(candidate["text"], normalized, deviations)
            if deviation is not None:
                allowed.append({
                    **record,
                    "deviation_id": deviation["deviation_id"],
                    "reason": deviation["reason"],
                })
                continue
            violations.append(record)

    uncovered: list[dict] = []
    if require_coverage:
        for entry in manifest["strings"]:
            normalized = normalize(entry.get("text", ""))
            if normalized and not any(normalized in matched for matched in matched_texts):
                uncovered.append({
                    "copy_id": entry.get("copy_id", ""),
                    "section_id": entry.get("section_id", ""),
                    "text": entry.get("text", "")[:200],
                })

    return {
        "schema_version": REPORT_SCHEMA,
        "files": len(files),
        "candidates_checked": checked,
        "violations": violations,
        "allowed_deviations_used": allowed,
        "uncovered_manifest_strings": uncovered,
        "status": "fail" if violations or uncovered else "pass",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--package", help="handoff package directory containing copy.json")
    source.add_argument("--manifest", help="path to copy.json directly")
    parser.add_argument(
        "--templates",
        action="append",
        default=[],
        required=True,
        help="template file or directory to lint; repeatable",
    )
    parser.add_argument("--min-length", type=int, default=DEFAULT_MIN_LENGTH)
    parser.add_argument(
        "--require-coverage",
        action="store_true",
        help="also fail when a manifest string appears in no template",
    )
    parser.add_argument("--report", help="write the JSON report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    manifest_path = (
        Path(args.package) / "copy.json" if args.package else Path(args.manifest)
    )
    try:
        manifest = load_manifest(manifest_path)
        files = template_files(args.templates, DEFAULT_TEMPLATE_SUFFIXES)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"copy-lint: {error}", file=sys.stderr)
        return 2

    if args.min_length < 1:
        print("copy-lint: --min-length must be at least 1", file=sys.stderr)
        return 2

    report = lint(manifest, files, args.min_length, args.require_coverage)

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for violation in report["violations"]:
        print(
            "NOT IN MANIFEST  {file}:{line}  [{kind}]  {text!r}".format(**violation)
        )
    for uncovered in report["uncovered_manifest_strings"]:
        print(
            "NOT BUILT        {copy_id} ({section_id})  {text!r}".format(**uncovered)
        )
    print(
        "[copy-lint] {status}: {candidates} strings checked in {files} file(s), "
        "{violations} not in manifest, {allowed} allowed deviation(s) used"
        "{coverage}".format(
            status=report["status"].upper(),
            candidates=report["candidates_checked"],
            files=report["files"],
            violations=len(report["violations"]),
            allowed=len(report["allowed_deviations_used"]),
            coverage=(
                ", {} manifest string(s) not built".format(
                    len(report["uncovered_manifest_strings"])
                )
                if report["uncovered_manifest_strings"]
                else ""
            ),
        )
    )
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
