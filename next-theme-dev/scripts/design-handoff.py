#!/usr/bin/env python3
"""Scaffold and validate a live-site design handoff package.

The shared handoff format belongs to next-theme-dev. In V1 it covers live-site
sources: a package that next-theme-design produced from a rendered website or
its HTML. Figma packages keep their own format and validator in
next-theme-figma (``figma-handoff.json`` and ``theme-figma.js``).

    python3 design-handoff.py new --out <dir> --project <slug> \\
        --source-url https://example.test/landing/ [options]
    python3 design-handoff.py validate <dir> [--report <file>] [--require-ready]

``validate`` reports two results. STRUCTURE says whether the package follows
the format and its provenance rules. READINESS says, per section, whether the
section can be built now; a structurally valid package can still have blocked
sections.

Exit codes: 0 valid (ready or not), 1 structurally invalid, 2 usage error or a
missing sibling skill, 3 valid but not ready when --require-ready is passed.

The format reference is ``references/design-handoff-format.md``.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import re
import struct
import sys
from pathlib import Path
from typing import Any

SKILL_DIR = Path(__file__).resolve().parents[1]
SIBLING_FIGMA_DIR = SKILL_DIR.parent / "next-theme-figma"
ROSTER_FILE = SIBLING_FIGMA_DIR / "references" / "spark-section-roster.json"

SCHEMA = {
    "handoff": "next-theme-dev/design-handoff/v1",
    "captures": "next-theme-dev/handoff-captures/v1",
    "routes": "next-theme-dev/handoff-routes/v1",
    "sections": "next-theme-dev/handoff-sections/v1",
    "assets": "next-theme-dev/handoff-assets/v1",
    "divergence": "next-theme-dev/handoff-divergence/v1",
    "coverage": "next-theme-dev/handoff-viewport-coverage/v1",
    "geometry": "next-theme-dev/handoff-geometry/v1",
    "copy": "next-theme-dev/handoff-copy/v1",
    "styles": "next-theme-dev/handoff-observed-styles/v1",
    "behaviors": "next-theme-dev/handoff-behaviors/v1",
}
REPORT_SCHEMA = "next-theme-dev/design-handoff-report/v1"

FILES = {
    "handoff": "design-handoff.json",
    "captures": "captures.json",
    "routes": "routes.json",
    "sections": "sections.json",
    "assets": "assets.json",
    "divergence": "platform-divergence-ledger.json",
    "coverage": "viewport-coverage.json",
    "geometry": "geometry.json",
    "copy": "copy.json",
    "styles": "observed-styles.json",
    "behaviors": "behaviors.json",
}
TEXT_FILES = ("validation-checklist.md", "notes.md")
FIGMA_ONLY_FILES = ("figma-handoff.json", "tokens.json")
FIGMA_ONLY_KEYS = frozenset({
    "figma", "figma_ref", "figma_node_id", "figma_names", "figma_nodes",
    "figma_frames", "figma_file_key", "figma_expectation", "figma_name",
    "frame_node_id", "node_id",
})

VIEWPORTS = {"desktop": 1440, "tablet": 768, "mobile": 390}
MODES = ("implementation-handoff", "intake-only")
REUSE = ("not-stated", "reuse-allowed", "reuse-not-allowed")
STATES = ("static", "motion", "interaction", "media-frame")
MOTION_MODES = ("paused", "partly-paused", "running", "none")
SCREENSHOT_KINDS = ("full-page", "viewport")
READINESS_STATUSES = ("ready", "timeout", "error", "none", "unsupported")
THEME_FAMILIES = {"spark": "web-components", "intro-bootstrap": "jquery-core-js", "custom": None}
RUNTIME_CONTRACTS = ("web-components", "jquery-core-js", "unknown")
CLASSIFICATIONS = (
    "semantic-rebuild", "reusable-media", "composed-asset", "background-asset",
    "live-commerce-component", "platform-app-hook", "screenshot-fallback",
)
ROSTER_STATUSES = ("shipped", "unshipped", "chrome", "unmapped")
ASSET_STATES = ("reference-only", "downloaded", "replacement-needed")
REFERENCE_TREATMENTS = ("omit", "rebuild-semantic", "replace-with-store-media")
MEDIA_TYPES = ("image", "video", "svg", "background-image", "icon", "audio", "embed")
DOWNLOAD_EXTENSIONS = {
    ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".webp": "webp", ".gif": "gif",
    ".svg": "svg", ".mp4": "mp4", ".webm": "webm",
}
DIVERGENCE_KINDS = ("commerce", "claim", "media", "layout", "platform", "other")
DIVERGENCE_DECISIONS = ("platform-wins", "source-wins-with-guardrails", "needs-approval", "blocked")
DIVERGENCE_STATUSES = ("open", "approved", "implemented", "blocked", "accepted-gap")
RESOLVED_DECISIONS = ("platform-wins", "source-wins-with-guardrails")
RESOLVED_STATUSES = ("approved", "implemented", "accepted-gap")
TARGET_EVIDENCE_ORIGINS = ("fresh-read-only", "prior-input", "none")
GAP_KINDS = (
    "missing-width", "extraction", "input", "commerce", "claim", "media",
    "behavior", "asset", "copy", "other",
)
COPY_DECISIONS = ("reuse", "replace", "omit", "unresolved")
COPY_ROLES = ("heading", "body", "label", "cta", "legal", "alt")
STYLE_CATEGORIES = (
    "color", "font", "spacing", "size", "radius", "shadow", "border", "layout",
    "motion", "other",
)
STYLE_FORBIDDEN_KEYS = ("variable", "variable_name", "token", "token_id", "css_var")
BEHAVIOR_KINDS = (
    "responsive-order", "sticky", "motion", "media", "cta-destination",
    "accessible-text", "interaction", "other",
)
BEHAVIOR_EVIDENCE = ("extracted", "observed")
GEOMETRY_ROLES = ("text", "image", "icon", "container", "control")
GEOMETRY_EDGES = ("left", "right", "top", "bottom")
GEOMETRY_AXES = ("vertical", "horizontal")
GEOMETRY_ASSERTIONS = ("position-x", "position-y", "width", "height")
GEOMETRY_ANCHORS = ("left", "center", "right")
BOX_MATCH_TOLERANCE_PX = 1.0
ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HASH_RE = re.compile(r"^[0-9a-f]{16,64}$")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class SiblingMissing(RuntimeError):
    """A sibling theme skill this command needs is not installed."""


def sibling_message(skill: str, path: Path, purpose: str) -> str:
    return (
        f"next-theme-dev: the sibling skill {skill} is not installed next to "
        f"next-theme-dev (expected {path}). It provides {purpose}. Install it "
        f"with `./skills.sh install <target> {skill}` from a NextCommerceCo/skills "
        f"checkout, or `npx skills add NextCommerceCo/skills -g --skill {skill}`. "
        "next-theme-figma, next-theme-design, and next-theme-dev are installed "
        "together; do not re-derive what the missing skill provides."
    )


def load_roster() -> dict[str, dict]:
    if not ROSTER_FILE.is_file():
        raise SiblingMissing(sibling_message(
            "next-theme-figma", SIBLING_FIGMA_DIR, "the Spark section roster "
            "(references/spark-section-roster.json)",
        ))
    roster = json.loads(ROSTER_FILE.read_text(encoding="utf-8"))
    entries = roster.get("entries") if isinstance(roster, dict) else None
    if not isinstance(entries, list):
        raise SiblingMissing(f"{ROSTER_FILE}: roster has no entries array")
    by_section: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        by_section.setdefault(entry.get("spark_section", ""), entry)
        for alternate in entry.get("alternates") or []:
            by_section.setdefault(alternate, entry)
    return by_section


# --------------------------------------------------------------------------
# Small helpers


class Result:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def read_json(path: Path, result: Result) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        result.error(f"{path.name}: invalid JSON ({error})")
        return None


def expect_schema(document: Any, key: str, result: Result) -> bool:
    label = FILES[key]
    if not isinstance(document, dict):
        result.error(f"{label}: must be a JSON object")
        return False
    if document.get("schema_version") != SCHEMA[key]:
        result.error(f'{label}: schema_version must be "{SCHEMA[key]}"')
    return True


def as_list(document: Any, key: str, label: str, result: Result) -> list:
    if not isinstance(document, dict):
        return []
    value = document.get(key)
    if not isinstance(value, list):
        result.error(f"{label}: {key} must be an array")
        return []
    return [item for item in value if isinstance(item, dict)] if value else []


def nonempty(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def parse_box(value: Any, label: str, result: Result) -> dict | None:
    if not isinstance(value, dict):
        result.error(f"{label}: missing box")
        return None
    parsed = {}
    for key in ("x", "y", "width", "height"):
        if not number(value.get(key)):
            result.error(f"{label}: box.{key} must be a finite number")
            return None
        parsed[key] = float(value[key])
    if parsed["width"] < 0 or parsed["height"] < 0:
        result.error(f"{label}: box width and height must not be negative")
        return None
    return parsed


def boxes_match(a: dict, b: dict) -> bool:
    return all(abs(a[key] - b[key]) <= BOX_MATCH_TOLERANCE_PX for key in ("x", "y", "width", "height"))


def package_path(package: Path, value: Any, label: str, result: Result) -> Path | None:
    """Resolve a package-relative file path, refusing escapes."""
    if not nonempty(value):
        result.error(f"{label}: path must be a non-empty string")
        return None
    if value.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", value) or "\\" in value:
        result.error(f"{label}: must be a relative path inside the package")
        return None
    root = package.resolve()
    target = (package / value).resolve()
    if target != root and root not in target.parents:
        result.error(f"{label}: resolves outside the package: {value}")
        return None
    if not target.is_file():
        result.error(f"{label}: file not found: {value}")
        return None
    return target


def png_size(path: Path) -> tuple[int, int] | None:
    data = path.read_bytes()[:33]
    if not data.startswith(PNG_SIGNATURE) or len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", data[16:24])


def media_check(path: Path) -> str | None:
    """Return an error message when a downloaded media file fails its checks."""
    kind = DOWNLOAD_EXTENSIONS.get(path.suffix.lower())
    if kind is None:
        return f"unsupported extension {path.suffix or '(none)'}"
    data = path.read_bytes()
    if not data:
        return "file is empty"
    head = data[:16]
    signatures = {
        "png": head.startswith(PNG_SIGNATURE),
        "jpeg": head.startswith(b"\xff\xd8"),
        "gif": head.startswith((b"GIF87a", b"GIF89a")),
        "webp": head.startswith(b"RIFF") and data[8:12] == b"WEBP",
        "mp4": data[4:8] == b"ftyp",
        "webm": head.startswith(b"\x1a\x45\xdf\xa3"),
        "svg": b"<svg" in data[:4096].lower(),
    }
    if not signatures[kind]:
        return f"content does not match its {kind} extension"
    if kind == "svg" and re.search(rb"<script\b|\son[a-z]+\s*=", data, re.IGNORECASE):
        return "SVG must not contain scripts or event handlers"
    return None


def find_figma_keys(value: Any, trail: str = "") -> list[str]:
    found = []
    if isinstance(value, dict):
        for key, child in value.items():
            where = f"{trail}.{key}" if trail else key
            if key in FIGMA_ONLY_KEYS:
                found.append(where)
            found.extend(find_figma_keys(child, where))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(find_figma_keys(child, f"{trail}[{index}]"))
    return found


def normalize_text(text: str) -> str:
    folded = text
    for source, target in (("“", '"'), ("”", '"'), ("‘", "'"), ("’", "'"),
                           ("—", "-"), ("–", "-"), (" ", " ")):
        folded = folded.replace(source, target)
    return re.sub(r"\s+", " ", folded).strip()


# --------------------------------------------------------------------------
# Validation


class Package:
    """The loaded package and the indexes the checks share."""

    def __init__(self, directory: Path, result: Result) -> None:
        self.dir = directory
        self.result = result
        self.docs: dict[str, Any] = {}
        for key, filename in FILES.items():
            self.docs[key] = read_json(directory / filename, result)
        notes = directory / "notes.md"
        self.notes = notes.read_text(encoding="utf-8") if notes.is_file() else ""
        self.routes: dict[str, dict] = {}
        self.sections: dict[str, dict] = {}
        self.captures: dict[str, dict] = {}
        self.raw_outputs: dict[str, dict] = {}
        self.gaps: dict[str, dict] = {}
        self.divergences: dict[str, dict] = {}
        self.copy: list[dict] = []
        self.assets: list[dict] = []


def validate(directory: Path) -> tuple[Result, dict]:
    result = Result()
    package = Package(directory, result)
    handoff = package.docs["handoff"]

    if handoff is None:
        result.error(f"missing {FILES['handoff']}: this is not a live-site package")
        return result, empty_readiness("missing entry file")

    for name in FIGMA_ONLY_FILES:
        if (directory / name).exists():
            result.error(
                f"{name} found: a live-site package must not carry Figma package files; "
                "Figma packages use next-theme-figma's validate-package"
            )
    for key, document in package.docs.items():
        if document is None:
            continue
        for where in find_figma_keys(document):
            result.error(f"{FILES[key]}: Figma-only field {where} in a live-site package")
        if isinstance(document, dict) and str(document.get("schema_version", "")).startswith("next-theme-figma/"):
            result.error(f"{FILES[key]}: next-theme-figma schema in a live-site package")

    mode = validate_handoff(package, handoff)
    if mode == "intake-only":
        if not (directory / "notes.md").is_file():
            result.error("missing notes.md: an intake-only result names its gaps in notes.md")
        return result, empty_readiness("intake-only package: capture has not produced a buildable package")

    for key, filename in FILES.items():
        if package.docs[key] is None and not (directory / filename).is_file():
            result.error(f"missing {filename}")
    for filename in TEXT_FILES:
        if not (directory / filename).is_file():
            result.error(f"missing {filename}")

    validate_routes(package)
    validate_sections(package, handoff)
    validate_captures(package)
    validate_gaps(package, handoff)
    validate_required_widths(package)
    validate_coverage(package)
    validate_divergence(package)
    validate_assets(package, handoff)
    validate_geometry(package)
    validate_copy(package)
    validate_styles(package)
    validate_behaviors(package)
    validate_cross_references(package)

    readiness = compute_readiness(package)
    return result, readiness


def validate_handoff(package: Package, handoff: Any) -> str:
    result = package.result
    if not expect_schema(handoff, "handoff", result):
        return ""
    mode = handoff.get("mode")
    if mode not in MODES:
        result.error(f"design-handoff.json: mode must be one of {', '.join(MODES)}")
    source = handoff.get("source")
    if not isinstance(source, dict):
        result.error("design-handoff.json: source must be an object")
        source = {}
    if source.get("kind") != "live-site":
        result.error('design-handoff.json: source.kind must be "live-site" (V1 covers live sites only)')
    urls = source.get("urls")
    supplied = source.get("supplied_html") or []
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        result.error("design-handoff.json: source.urls must be an array of URLs")
        urls = []
    if not urls and not supplied:
        result.error("design-handoff.json: record at least one source URL or supplied HTML file")
    for url in urls:
        if not re.match(r"^https?://", url):
            result.error(f"design-handoff.json: source URL {url!r} must start with http:// or https://")
    if not (isinstance(source.get("captured_on"), str) and DATE_RE.match(source["captured_on"])):
        result.error("design-handoff.json: source.captured_on must be the capture date as YYYY-MM-DD")
    rights = source.get("rights")
    if not isinstance(rights, dict):
        result.error("design-handoff.json: source.rights must record the owner and reuse statement")
        rights = {}
    if not nonempty(rights.get("owner")):
        result.error('design-handoff.json: source.rights.owner must be recorded (use "not stated" when the operator gave none)')
    if rights.get("reuse") not in REUSE:
        result.error(f"design-handoff.json: source.rights.reuse must be one of {', '.join(REUSE)}")
    if rights.get("reuse") in ("reuse-allowed", "reuse-not-allowed"):
        for field in ("statement", "stated_by"):
            if not nonempty(rights.get(field)):
                result.error(f"design-handoff.json: source.rights.{field} must record the operator's statement")

    target = handoff.get("target")
    if not isinstance(target, dict):
        result.error("design-handoff.json: target must be an object")
        target = {}
    family = target.get("theme_family")
    runtime = target.get("runtime_contract")
    if family not in THEME_FAMILIES:
        result.error(f"design-handoff.json: target.theme_family must be one of {', '.join(THEME_FAMILIES)}")
    if runtime not in RUNTIME_CONTRACTS:
        result.error(f"design-handoff.json: target.runtime_contract must be one of {', '.join(RUNTIME_CONTRACTS)}")
    expected = THEME_FAMILIES.get(family)
    if expected and runtime in RUNTIME_CONTRACTS and runtime != expected:
        result.error(f'design-handoff.json: target.theme_family "{family}" requires runtime_contract "{expected}"')
    if mode == "implementation-handoff":
        for field in ("store", "repo"):
            if not nonempty(target.get(field)):
                result.error(f"design-handoff.json: target.{field} must be recorded")
    gaps = handoff.get("gaps")
    if not isinstance(gaps, list):
        result.error("design-handoff.json: gaps must be an array (empty when there are none)")
    elif mode == "intake-only" and not gaps:
        result.error("design-handoff.json: an intake-only package must name the gaps that stop capture")
    return mode or ""


def validate_routes(package: Package) -> None:
    result = package.result
    routes = package.docs["routes"]
    if routes is None or not expect_schema(routes, "routes", result):
        return
    entries = as_list(routes, "routes", "routes.json", result)
    if not entries:
        result.error("routes.json: no routes recorded")
    for route in entries:
        route_id = route.get("route_id")
        if not nonempty(route_id) or not ID_RE.match(route_id):
            result.error("routes.json: route_id must be a lowercase id")
            continue
        if route_id in package.routes:
            result.error(f"routes.json: duplicate route_id {route_id}")
        package.routes[route_id] = route
        for field in ("source_url", "storefront_path", "theme_template"):
            if not nonempty(route.get(field)):
                result.error(f"routes.json: {route_id}: missing {field}")
        if not isinstance(route.get("section_order"), list) or not route["section_order"]:
            result.error(f"routes.json: {route_id}: section_order is empty")


def validate_sections(package: Package, handoff: dict) -> None:
    result = package.result
    sections = package.docs["sections"]
    if sections is None or not expect_schema(sections, "sections", result):
        return
    entries = as_list(sections, "sections", "sections.json", result)
    if not entries:
        result.error("sections.json: no sections recorded")
    spark = isinstance(handoff, dict) and (handoff.get("target") or {}).get("theme_family") == "spark"
    roster = load_roster() if spark else None
    for section in entries:
        section_id = section.get("section_id")
        if not nonempty(section_id) or not ID_RE.match(section_id):
            result.error("sections.json: section_id must be a lowercase id")
            continue
        label = f"sections.json: {section_id}"
        if section_id in package.sections:
            result.error(f"{label}: duplicate section_id (section ids are unique across the package)")
        package.sections[section_id] = section
        if section.get("route_id") not in package.routes:
            result.error(f"{label}: route_id {section.get('route_id')!r} is not in routes.json")
        if not isinstance(section.get("in_scope", True), bool):
            result.error(f"{label}: in_scope must be true or false")
        if section.get("in_scope") is False and not nonempty(section.get("exclusion_reason")):
            result.error(f"{label}: an excluded section needs exclusion_reason")
        if not nonempty(section.get("source_selector")):
            result.error(f"{label}: missing source_selector")
        classification = section.get("classification")
        if classification not in CLASSIFICATIONS:
            result.error(f"{label}: invalid classification {classification!r}")
        if classification == "screenshot-fallback" and section.get("screenshot_fallback_approved") is not True:
            result.error(f"{label}: screenshot-fallback requires screenshot_fallback_approved=true")
        if not nonempty(section.get("classification_rationale")):
            result.error(f"{label}: missing classification_rationale")
        target = section.get("implementation_target")
        if not isinstance(target, dict) or not nonempty(target.get("template")):
            result.error(f"{label}: missing implementation_target.template")
        refs = section.get("capture_refs")
        if not isinstance(refs, dict) or not any(nonempty(value) for value in refs.values()):
            result.error(f"{label}: capture_refs must name at least one capture of this section")
        status = section.get("roster_status")
        spark_section = section.get("spark_section") or ""
        if spark and not nonempty(status):
            result.error(
                f"{label}: missing roster_status; a Spark-targeted live-site package records "
                "roster_status on every section (use unmapped when nothing fits)"
            )
        if nonempty(status):
            if status not in ROSTER_STATUSES:
                result.error(f"{label}: invalid roster_status {status!r}")
            elif status != "unmapped" and not spark_section:
                result.error(f'{label}: roster_status "{status}" requires spark_section')
        if spark_section and roster is not None:
            entry = roster.get(spark_section)
            if entry is None:
                result.error(f"{label}: spark_section {spark_section!r} is not in the Spark section roster")
            elif nonempty(status) and status in ROSTER_STATUSES and status != entry.get("status"):
                result.error(
                    f'{label}: roster_status "{status}" contradicts the roster '
                    f"({spark_section} is {entry.get('status')})"
                )


def validate_captures(package: Package) -> None:
    result = package.result
    captures = package.docs["captures"]
    if captures is None or not expect_schema(captures, "captures", result):
        return
    for capture in as_list(captures, "captures", "captures.json", result):
        capture_id = capture.get("capture_id")
        if not nonempty(capture_id) or not ID_RE.match(capture_id):
            result.error("captures.json: capture_id must be a lowercase id")
            continue
        label = f"captures.json: {capture_id}"
        if capture_id in package.captures:
            result.error(f"{label}: duplicate capture_id")
        package.captures[capture_id] = capture
        if capture.get("route_id") not in package.routes:
            result.error(f"{label}: route_id {capture.get('route_id')!r} is not in routes.json")
        viewport = capture.get("viewport")
        if viewport not in VIEWPORTS:
            result.error(f"{label}: viewport must be one of {', '.join(VIEWPORTS)}")
        elif capture.get("viewport_width") != VIEWPORTS[viewport]:
            result.error(
                f"{label}: viewport_width {capture.get('viewport_width')} is not the "
                f"{viewport} width {VIEWPORTS[viewport]}"
            )
        if not (isinstance(capture.get("viewport_height"), int) and capture["viewport_height"] > 0):
            result.error(f"{label}: viewport_height must be a positive integer")
        dpr = capture.get("device_pixel_ratio")
        if not (number(dpr) and dpr > 0):
            result.error(f"{label}: device_pixel_ratio must be a positive number")
            dpr = None
        state = capture.get("state")
        if state not in STATES:
            result.error(f"{label}: state must be one of {', '.join(STATES)}")
        if state == "interaction" and not nonempty(capture.get("state_detail")):
            result.error(f"{label}: an interaction capture describes the interaction in state_detail")
        motion = capture.get("motion")
        if motion not in MOTION_MODES:
            result.error(f"{label}: motion must be one of {', '.join(MOTION_MODES)}")
        elif state in ("static", "interaction", "media-frame") and motion == "running":
            result.error(f"{label}: a {state} capture must be taken with motion paused, not running")
        if state == "media-frame":
            if not nonempty(capture.get("media_url")) or not number(capture.get("media_time_s")):
                result.error(f"{label}: a media-frame capture records media_url and media_time_s")
        for field in ("url", "captured_at", "browser"):
            if not nonempty(capture.get(field)):
                result.error(f"{label}: missing {field}")
        if not (isinstance(capture.get("html_sha256"), str) and HASH_RE.match(capture["html_sha256"])):
            result.error(f"{label}: html_sha256 must be 16 to 64 lowercase hex digits")
        readiness = capture.get("readiness")
        if not isinstance(readiness, dict):
            result.error(f"{label}: readiness must record fonts, images, and media_metadata")
        else:
            for key in ("fonts", "images", "media_metadata"):
                if readiness.get(key) not in READINESS_STATUSES:
                    result.error(f"{label}: readiness.{key} must be one of {', '.join(READINESS_STATUSES)}")
        boxes = capture.get("boxes", {})
        if not isinstance(boxes, dict):
            result.error(f"{label}: boxes must be an object keyed by target key")
        else:
            for key, value in boxes.items():
                parse_box(value, f"{label}: boxes[{key}]", result)
        screenshot = capture.get("screenshot")
        if screenshot is not None:
            if capture.get("screenshot_kind") not in SCREENSHOT_KINDS:
                result.error(f"{label}: screenshot_kind must be one of {', '.join(SCREENSHOT_KINDS)}")
            path = package_path(package.dir, screenshot, f"{label}: screenshot", result)
            if path is not None:
                size = png_size(path)
                if size is None:
                    result.error(f"{label}: screenshot {screenshot} is not a PNG")
                elif dpr is not None and number(capture.get("viewport_width")):
                    expected = round(capture["viewport_width"] * dpr)
                    if size[0] != expected:
                        result.error(
                            f"{label}: screenshot {screenshot} is {size[0]}px wide; "
                            f"viewport_width {capture['viewport_width']} x device_pixel_ratio "
                            f"{dpr} requires {expected}px (use an unscaled screenshot)"
                        )
        raw = capture.get("raw_output")
        if raw is not None:
            path = package_path(package.dir, raw, f"{label}: raw_output", result)
            if path is not None:
                try:
                    raw_doc = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as error:
                    result.error(f"{label}: raw_output is not valid JSON ({error})")
                    continue
                raw_capture = (raw_doc.get("capture") or {}) if isinstance(raw_doc, dict) else {}
                raw_page = (raw_doc.get("page") or {}) if isinstance(raw_doc, dict) else {}
                if raw_capture.get("capture_id") != capture_id:
                    result.error(f"{label}: raw_output belongs to capture {raw_capture.get('capture_id')!r}")
                if raw_page.get("viewport_width") != capture.get("viewport_width"):
                    result.error(f"{label}: raw_output viewport_width differs from the record")
                package.raw_outputs[capture_id] = raw_doc


def capture_for(package: Package, capture_id: Any, label: str, *, route: str | None = None,
                viewport: str | None = None, states: tuple[str, ...] | None = None,
                need_screenshot: bool = False) -> dict | None:
    """Resolve a capture reference and check it matches route, viewport, and state."""
    result = package.result
    if not nonempty(capture_id):
        result.error(f"{label}: missing capture_id")
        return None
    capture = package.captures.get(capture_id)
    if capture is None:
        result.error(f"{label}: capture_id {capture_id!r} is not in captures.json")
        return None
    if route is not None and capture.get("route_id") != route:
        result.error(
            f"{label}: capture {capture_id} is for route {capture.get('route_id')!r}, not {route!r}"
        )
    if viewport is not None and capture.get("viewport") != viewport:
        result.error(
            f"{label}: capture {capture_id} is a {capture.get('viewport')} capture, not {viewport}"
        )
    if states is not None and capture.get("state") not in states:
        result.error(
            f"{label}: capture {capture_id} is in state {capture.get('state')!r}; "
            f"expected {' or '.join(states)}"
        )
    if need_screenshot and not nonempty(capture.get("screenshot")):
        result.error(f"{label}: capture {capture_id} has no screenshot")
    return capture


def validate_gaps(package: Package, handoff: dict) -> None:
    result = package.result
    for gap in handoff.get("gaps") or []:
        if not isinstance(gap, dict):
            result.error("design-handoff.json: every gap must be an object")
            continue
        gap_id = gap.get("gap_id")
        if not nonempty(gap_id):
            result.error("design-handoff.json: gap missing gap_id")
            continue
        label = f"design-handoff.json: gap {gap_id}"
        if gap_id in package.gaps:
            result.error(f"{label}: duplicate gap_id")
        package.gaps[gap_id] = gap
        if gap.get("kind") not in GAP_KINDS:
            result.error(f"{label}: kind must be one of {', '.join(GAP_KINDS)}")
        if gap.get("route_id") not in package.routes:
            result.error(f"{label}: route_id {gap.get('route_id')!r} is not in routes.json")
        section_id = gap.get("section_id")
        if section_id is not None and section_id not in package.sections:
            result.error(f"{label}: section_id {section_id!r} is not in sections.json")
        viewport = gap.get("viewport")
        if viewport is not None and viewport not in VIEWPORTS:
            result.error(f"{label}: viewport must be one of {', '.join(VIEWPORTS)} or null")
        for field in ("reason", "next_action"):
            if not nonempty(gap.get(field)):
                result.error(f"{label}: missing {field}")
        if not isinstance(gap.get("blocks_build"), bool):
            result.error(f"{label}: blocks_build must be true or false")
        if gap.get("kind") == "missing-width":
            if viewport is None:
                result.error(f"{label}: a missing-width gap names its viewport")
            if gap.get("blocks_build") is False and not nonempty(gap.get("excluded_by")):
                result.error(
                    f"{label}: a missing width blocks its sections until it is captured "
                    "or explicitly excluded; record excluded_by to exclude it"
                )


def validate_required_widths(package: Package) -> None:
    result = package.result
    for route_id in package.routes:
        for viewport, width in VIEWPORTS.items():
            captured = any(
                capture.get("route_id") == route_id and capture.get("viewport") == viewport
                and nonempty(capture.get("screenshot"))
                for capture in package.captures.values()
            )
            recorded = any(
                gap.get("kind") == "missing-width" and gap.get("route_id") == route_id
                and gap.get("viewport") == viewport
                for gap in package.gaps.values()
            )
            if captured and recorded:
                result.warn(f"route {route_id}: {viewport} has captures and a missing-width gap; remove the gap")
            if not captured and not recorded:
                result.error(
                    f"route {route_id}: no {viewport} ({width}px) capture with a screenshot and no "
                    "missing-width gap recording why"
                )


def validate_coverage(package: Package) -> None:
    result = package.result
    coverage = package.docs["coverage"]
    if coverage is None or not expect_schema(coverage, "coverage", result):
        return
    viewports = coverage.get("viewports")
    if not isinstance(viewports, dict):
        result.error("viewport-coverage.json: viewports must be an object")
    else:
        for name, width in VIEWPORTS.items():
            entry = viewports.get(name)
            if not isinstance(entry, dict) or entry.get("expected_width") != width:
                result.error(f"viewport-coverage.json: viewports.{name}.expected_width must be {width}")
    seen = set()
    for entry in as_list(coverage, "coverage", "viewport-coverage.json", result):
        route_id = entry.get("route_id")
        label = f"viewport-coverage.json: {route_id}"
        if route_id not in package.routes:
            result.error(f"{label}: route_id is not in routes.json")
            continue
        seen.add(route_id)
        for viewport in VIEWPORTS:
            item = entry.get(viewport)
            where = f"{label}.{viewport}"
            if not isinstance(item, dict):
                result.error(f"{where}: must record capture_id and status")
                continue
            status = item.get("status")
            if status == "captured":
                capture = capture_for(package, item.get("capture_id"), where, route=route_id,
                                      viewport=viewport, need_screenshot=True)
                if capture is not None and item.get("source_ref") != capture.get("screenshot"):
                    result.error(f"{where}: source_ref must be the capture's screenshot {capture.get('screenshot')!r}")
            elif status == "gap":
                gap = package.gaps.get(item.get("gap_id"))
                if gap is None or gap.get("kind") != "missing-width" or gap.get("route_id") != route_id \
                        or gap.get("viewport") != viewport:
                    result.error(f"{where}: gap_id must name the missing-width gap for this route and viewport")
            else:
                result.error(f'{where}: status must be "captured" or "gap"')
            if nonempty(item.get("preview_ref")):
                package_path(package.dir, item["preview_ref"], f"{where}.preview_ref", result)
    for route_id in package.routes:
        if route_id not in seen:
            result.error(f"viewport-coverage.json: no coverage entry for route {route_id}")


def validate_divergence(package: Package) -> None:
    result = package.result
    ledger = package.docs["divergence"]
    if ledger is None or not expect_schema(ledger, "divergence", result):
        return
    for entry in as_list(ledger, "entries", "platform-divergence-ledger.json", result):
        divergence_id = entry.get("divergence_id")
        if not nonempty(divergence_id):
            result.error("platform-divergence-ledger.json: entry missing divergence_id")
            continue
        label = f"platform-divergence-ledger.json: {divergence_id}"
        if divergence_id in package.divergences:
            result.error(f"{label}: duplicate divergence_id")
        package.divergences[divergence_id] = entry
        if entry.get("kind") not in DIVERGENCE_KINDS:
            result.error(f"{label}: kind must be one of {', '.join(DIVERGENCE_KINDS)}")
        for field in ("surface", "source_expectation", "platform_behavior", "implementation_guardrail"):
            if not nonempty(entry.get(field)):
                result.error(f"{label}: missing {field}")
        decision = entry.get("decision")
        if decision == "figma-wins-with-guardrails":
            result.error(f"{label}: figma-wins-with-guardrails is a Figma decision; use source-wins-with-guardrails")
        elif decision not in DIVERGENCE_DECISIONS:
            result.error(f"{label}: decision must be one of {', '.join(DIVERGENCE_DECISIONS)}")
        status = entry.get("status")
        if status not in DIVERGENCE_STATUSES:
            result.error(f"{label}: status must be one of {', '.join(DIVERGENCE_STATUSES)}")
        if status in ("approved", "accepted-gap") and not nonempty(entry.get("approved_by")):
            result.error(f"{label}: an {status} entry records approved_by")
        routes = entry.get("route_ids")
        if not isinstance(routes, list) or not routes:
            result.error(f"{label}: route_ids must list the affected routes")
            routes = []
        for route_id in routes:
            if route_id not in package.routes:
                result.error(f"{label}: route_id {route_id!r} is not in routes.json")
        sections = entry.get("section_ids")
        if not isinstance(sections, list):
            result.error(f"{label}: section_ids must be an array")
            sections = []
        for section_id in sections:
            if section_id not in package.sections:
                result.error(f"{label}: section_id {section_id!r} is not in sections.json")
        capture_ids = entry.get("capture_ids")
        if not isinstance(capture_ids, list) or not capture_ids:
            result.error(f"{label}: capture_ids must cite the captures that show the source side")
        else:
            for capture_id in capture_ids:
                capture = capture_for(package, capture_id, label)
                if capture is not None and routes and capture.get("route_id") not in routes:
                    result.error(f"{label}: capture {capture_id} is for a route this entry does not list")
        evidence = entry.get("target_evidence")
        if evidence is not None:
            if not isinstance(evidence, dict) or evidence.get("origin") not in TARGET_EVIDENCE_ORIGINS:
                result.error(f"{label}: target_evidence.origin must be one of {', '.join(TARGET_EVIDENCE_ORIGINS)}")
            elif evidence.get("origin") != "none" and not nonempty(evidence.get("summary")):
                result.error(f"{label}: target_evidence.summary must say what the evidence shows")


def validate_assets(package: Package, handoff: dict) -> None:
    result = package.result
    assets = package.docs["assets"]
    if assets is None or not expect_schema(assets, "assets", result):
        return
    reuse = ((handoff.get("source") or {}).get("rights") or {}).get("reuse")
    seen = set()
    for asset in as_list(assets, "assets", "assets.json", result):
        asset_id = asset.get("asset_id")
        if not nonempty(asset_id):
            result.error("assets.json: asset missing asset_id")
            continue
        label = f"assets.json: {asset_id}"
        if asset_id in seen:
            result.error(f"{label}: duplicate asset_id")
        seen.add(asset_id)
        package.assets.append(asset)
        section = package.sections.get(asset.get("section_id"))
        if section is None:
            result.error(f"{label}: section_id {asset.get('section_id')!r} is not in sections.json")
        if asset.get("media_type") not in MEDIA_TYPES:
            result.error(f"{label}: media_type must be one of {', '.join(MEDIA_TYPES)}")
        if not nonempty(asset.get("role")):
            result.error(f"{label}: missing role")
        if not isinstance(asset.get("alt"), str):
            result.error(f"{label}: alt must be a string (empty for decorative media)")
        if not nonempty(asset.get("source_url")):
            result.error(f"{label}: missing source_url")
        capture_for(package, asset.get("capture_id"), label,
                    route=section.get("route_id") if section else None)
        state = asset.get("state")
        treatment = asset.get("treatment")
        local_path = asset.get("local_path")
        if state not in ASSET_STATES:
            result.error(f"{label}: state must be one of {', '.join(ASSET_STATES)}")
        elif state == "reference-only":
            if nonempty(local_path):
                result.error(f"{label}: a reference-only asset has no local file; remove local_path")
            if treatment not in (None, "") and treatment not in REFERENCE_TREATMENTS:
                result.error(f"{label}: treatment must be one of {', '.join(REFERENCE_TREATMENTS)} or empty")
        elif state == "downloaded":
            if reuse != "reuse-allowed":
                result.error(
                    f"{label}: source media was downloaded but source.rights.reuse is {reuse!r}; "
                    "without a reuse statement the run is reference-only"
                )
            if treatment != "use-downloaded":
                result.error(f'{label}: a downloaded asset has treatment "use-downloaded"')
            path = package_path(package.dir, local_path, f"{label}: local_path", result)
            if path is not None:
                problem = media_check(path)
                if problem:
                    result.error(f"{label}: local_path {local_path} fails the media checks: {problem}")
        elif state == "replacement-needed":
            if not nonempty(asset.get("target_asset")):
                result.error(f"{label}: a replacement-needed asset names the missing target_asset")
            if nonempty(local_path):
                result.error(f"{label}: a replacement-needed asset has no local file; remove local_path")


def validate_geometry(package: Package) -> None:
    result = package.result
    geometry = package.docs["geometry"]
    if geometry is None or not expect_schema(geometry, "geometry", result):
        return
    if geometry.get("source") != "dom-capture":
        result.error('geometry.json: source must be "dom-capture"')
    seen_routes = set()
    for route in as_list(geometry, "routes", "geometry.json", result):
        route_id = route.get("route_id")
        if route_id not in package.routes:
            result.error(f"geometry.json: route_id {route_id!r} is not in routes.json")
            continue
        if route_id in seen_routes:
            result.error(f"geometry.json: duplicate route {route_id}")
        seen_routes.add(route_id)
        viewports = route.get("viewports")
        if not isinstance(viewports, dict):
            result.error(f"geometry.json: {route_id}: viewports must be an object")
            continue
        for viewport, frame in viewports.items():
            frame_label = f"geometry.json: {route_id}.{viewport}"
            if viewport not in VIEWPORTS:
                result.error(f"{frame_label}: unknown viewport")
                continue
            if not isinstance(frame, dict):
                result.error(f"{frame_label}: must be an object")
                continue
            if frame.get("frame_width") != VIEWPORTS[viewport]:
                result.error(f"{frame_label}: frame_width must be {VIEWPORTS[viewport]}")
            if not number(frame.get("frame_height")):
                result.error(f"{frame_label}: frame_height must be a number")
            sections = frame.get("sections")
            if not isinstance(sections, list) or not sections:
                result.error(f"{frame_label}: sections must be a non-empty array")
                continue
            seen_sections = set()
            for section in sections:
                validate_geometry_section(package, route_id, viewport, section, seen_sections)


def geometry_provenance(package: Package, entry: dict, label: str, identifier: str, *,
                        route: str, viewport: str) -> dict | None:
    """Check an entry is extracted from a matching capture or inferred with a reason."""
    result = package.result
    basis = entry.get("basis", "extracted")
    if basis == "inferred":
        if not nonempty(entry.get("reason")):
            result.error(f"{label}: an inferred entry records its reason")
        if identifier not in package.notes:
            result.error(f"{label}: inferred entry {identifier} is not listed in notes.md")
        return None
    if basis != "extracted":
        result.error(f'{label}: basis must be "extracted" or "inferred"')
        return None
    if not nonempty(entry.get("capture_id")):
        result.error(
            f'{label}: needs a capture_id, or basis "inferred" with a reason listed in notes.md'
        )
        return None
    return capture_for(package, entry.get("capture_id"), label, route=route, viewport=viewport,
                       states=("static", "interaction"))


def validate_geometry_section(package: Package, route_id: str, viewport: str, section: Any,
                              seen: set) -> None:
    result = package.result
    if not isinstance(section, dict) or not nonempty(section.get("section_id")):
        result.error(f"geometry.json: {route_id}.{viewport}: section missing section_id")
        return
    section_id = section["section_id"]
    label = f"geometry.json: {route_id}.{viewport}.{section_id}"
    if section_id in seen:
        result.error(f"{label}: duplicate section")
    seen.add(section_id)
    known = package.sections.get(section_id)
    if known is None:
        result.error(f"{label}: section_id is not in sections.json")
    elif known.get("route_id") != route_id:
        result.error(f"{label}: section belongs to route {known.get('route_id')!r}")
    if not nonempty(section.get("selector")):
        result.error(f"{label}: missing selector (the hook the built theme carries)")
    box = parse_box(section.get("box"), label, result)
    capture = geometry_provenance(package, section, label, f"geometry:{route_id}/{viewport}/{section_id}",
                                  route=route_id, viewport=viewport)
    if capture is not None:
        if not nonempty(section.get("source_selector")):
            result.error(f"{label}: an extracted entry records the source page's source_selector")
        raw_box = (capture.get("boxes") or {}).get(section_id)
        if raw_box is None:
            result.error(f"{label}: capture {capture['capture_id']} has no box for {section_id}")
        elif box is not None and not boxes_match(box, raw_box):
            result.error(
                f"{label}: box does not match the page-relative box in capture "
                f"{capture['capture_id']} (section boxes are page-relative)"
            )
    elements = section.get("elements")
    if not isinstance(elements, list):
        result.error(f"{label}: elements must be an array")
        return
    ids = set()
    selectors: dict[str, str] = {}
    for element in elements:
        if not isinstance(element, dict) or not nonempty(element.get("element_id")):
            result.error(f"{label}: element missing element_id")
            continue
        element_id = element["element_id"]
        element_label = f"{label}::{element_id}"
        if element_id in ids:
            result.error(f"{element_label}: duplicate element_id")
        ids.add(element_id)
        selector = element.get("selector")
        if not nonempty(selector):
            result.error(f"{element_label}: missing selector")
        elif selector in selectors:
            result.error(f"{element_label}: selector {selector!r} is already used by {selectors[selector]}")
        else:
            selectors[selector] = element_id
        if element.get("role") is not None and element.get("role") not in GEOMETRY_ROLES:
            result.error(f"{element_label}: invalid role {element.get('role')!r}")
        if element.get("assert") is not None:
            if not isinstance(element["assert"], list) or not element["assert"] \
                    or any(name not in GEOMETRY_ASSERTIONS for name in element["assert"]):
                result.error(f"{element_label}: assert must be a non-empty subset of {', '.join(GEOMETRY_ASSERTIONS)}")
        if element.get("align_anchor") is not None and element["align_anchor"] not in GEOMETRY_ANCHORS:
            result.error(f"{element_label}: invalid align_anchor")
        if element.get("tolerance_px") is not None and not number(element["tolerance_px"]):
            result.error(f"{element_label}: tolerance_px must be a number")
        element_box = parse_box(element.get("box"), element_label, result)
        element_capture = geometry_provenance(
            package, element, element_label,
            f"geometry:{route_id}/{viewport}/{section_id}::{element_id}",
            route=route_id, viewport=viewport,
        )
        if element_capture is None:
            continue
        if not nonempty(element.get("source_selector")):
            result.error(f"{element_label}: an extracted entry records the source page's source_selector")
        if capture is None or element_capture["capture_id"] != capture["capture_id"]:
            result.error(
                f"{element_label}: an extracted element cites the same capture as its extracted section"
            )
            continue
        raw_section = (capture.get("boxes") or {}).get(section_id)
        raw_element = (capture.get("boxes") or {}).get(f"{section_id}::{element_id}")
        if raw_element is None or raw_section is None:
            result.error(f"{element_label}: capture {capture['capture_id']} has no box for {section_id}::{element_id}")
            continue
        expected = {
            "x": raw_element["x"] - raw_section["x"],
            "y": raw_element["y"] - raw_section["y"],
            "width": raw_element["width"],
            "height": raw_element["height"],
        }
        if element_box is not None and not boxes_match(element_box, expected):
            result.error(
                f"{element_label}: box must be relative to its section's top-left corner "
                f"(expected x={expected['x']:g}, y={expected['y']:g} from capture {capture['capture_id']})"
            )
    for group in section.get("alignment_groups") or []:
        members = group.get("element_ids") if isinstance(group, dict) else None
        if not isinstance(group, dict) or group.get("edge") not in GEOMETRY_EDGES \
                or not isinstance(members, list) or len(members) < 2 \
                or any(member not in ids for member in members):
            result.error(f"{label}: alignment group {group!r} needs an edge and two known element_ids")
    for gap in section.get("gaps") or []:
        if not isinstance(gap, dict) or gap.get("axis") not in GEOMETRY_AXES \
                or gap.get("from") not in ids or gap.get("to") not in ids or not number(gap.get("value")):
            result.error(f"{label}: gap {gap!r} needs axis, known from/to element_ids, and a value")


def raw_target(package: Package, capture_id: str, key: str) -> dict | None:
    raw = package.raw_outputs.get(capture_id)
    if not isinstance(raw, dict):
        return None
    target = (raw.get("targets") or {}).get(key)
    return target if isinstance(target, dict) else None


def validate_copy(package: Package) -> None:
    result = package.result
    copy = package.docs["copy"]
    if copy is None or not expect_schema(copy, "copy", result):
        return
    if copy.get("source") != "dom-capture":
        result.error('copy.json: source must be "dom-capture"')
    seen = set()
    for entry in as_list(copy, "strings", "copy.json", result):
        copy_id = entry.get("copy_id")
        if not nonempty(copy_id):
            result.error("copy.json: string missing copy_id")
            continue
        label = f"copy.json: {copy_id}"
        if copy_id in seen:
            result.error(f"{label}: duplicate copy_id")
        seen.add(copy_id)
        package.copy.append(entry)
        section = package.sections.get(entry.get("section_id"))
        if section is None:
            result.error(f"{label}: section_id {entry.get('section_id')!r} is not in sections.json")
        if entry.get("role") is not None and entry["role"] not in COPY_ROLES:
            result.error(f"{label}: invalid role {entry['role']!r}")
        source_text = entry.get("source_text")
        if not nonempty(source_text):
            result.error(f"{label}: source_text must hold the captured text")
            source_text = ""
        if entry.get("basis") == "inferred":
            result.error(f"{label}: source copy cannot be inferred; capture it or record a gap")
        capture = capture_for(
            package, entry.get("capture_id"), label,
            route=section.get("route_id") if section else None,
            viewport=entry.get("viewport") if entry.get("viewport") in VIEWPORTS else None,
            states=("static", "interaction", "media-frame"),
        )
        key = entry.get("capture_key")
        if capture is not None and nonempty(key) and source_text:
            target = raw_target(package, capture["capture_id"], key)
            if target is None:
                if capture["capture_id"] in package.raw_outputs:
                    result.error(f"{label}: capture {capture['capture_id']} has no target {key}")
            else:
                captured = normalize_text(" ".join(
                    [target.get("text") or "", target.get("accessible_name") or ""]
                    + [match.get("text") or "" for match in target.get("matches") or []]
                ))
                if normalize_text(source_text) not in captured:
                    result.error(f"{label}: source_text is not in the text capture {capture['capture_id']} recorded for {key}")
        decision = entry.get("decision")
        text = entry.get("text")
        if decision not in COPY_DECISIONS:
            result.error(f"{label}: decision must be one of {', '.join(COPY_DECISIONS)}")
        elif decision == "reuse":
            if text != source_text:
                result.error(f"{label}: a reuse decision permits exactly the source_text as text")
            if not nonempty(entry.get("reuse_basis")):
                result.error(f"{label}: a reuse decision records its reuse_basis")
            if entry.get("commerce_fact") is True and not nonempty(entry.get("next_evidence")):
                result.error(f"{label}: reusing a commerce fact needs next_evidence from NEXT")
        elif decision == "replace":
            if not nonempty(text):
                result.error(f"{label}: a replace decision holds the approved replacement in text")
            elif normalize_text(text) == normalize_text(source_text):
                result.error(f"{label}: replacement text equals the source text; use reuse")
            if not nonempty(entry.get("approved_by")) and not nonempty(entry.get("next_source")):
                result.error(f"{label}: a replacement records approved_by or its next_source")
        elif decision in ("omit", "unresolved"):
            if text != "":
                result.error(f'{label}: an {decision} decision permits no build text; text must be ""')
        divergence_id = entry.get("divergence_id")
        if nonempty(divergence_id) and divergence_id not in package.divergences:
            result.error(f"{label}: divergence_id {divergence_id!r} is not in the ledger")
    for deviation in copy.get("allowed_deviations") or []:
        label = f"copy.json: allowed deviation {deviation.get('deviation_id', '(unnamed)')}"
        has_text = nonempty(deviation.get("text"))
        has_pattern = nonempty(deviation.get("pattern"))
        if has_text == has_pattern:
            result.error(f"{label}: needs exactly one of text or pattern")
        if not nonempty(deviation.get("reason")) or not nonempty(deviation.get("approved_by")):
            result.error(f"{label}: needs a reason and approved_by")


def validate_styles(package: Package) -> None:
    result = package.result
    styles = package.docs["styles"]
    if styles is None or not expect_schema(styles, "styles", result):
        return
    if styles.get("source") != "dom-capture":
        result.error('observed-styles.json: source must be "dom-capture"')
    seen = set()
    for entry in as_list(styles, "styles", "observed-styles.json", result):
        style_id = entry.get("style_id")
        if not nonempty(style_id):
            result.error("observed-styles.json: entry missing style_id")
            continue
        label = f"observed-styles.json: {style_id}"
        if style_id in seen:
            result.error(f"{label}: duplicate style_id")
        seen.add(style_id)
        for key in STYLE_FORBIDDEN_KEYS:
            if key in entry:
                result.error(f"{label}: observed values are not variables; remove {key}")
        route_id = entry.get("route_id")
        viewport = entry.get("viewport")
        if route_id not in package.routes:
            result.error(f"{label}: route_id {route_id!r} is not in routes.json")
        if viewport not in VIEWPORTS:
            result.error(f"{label}: viewport must be one of {', '.join(VIEWPORTS)}")
        if entry.get("section_id") is not None and entry["section_id"] not in package.sections:
            result.error(f"{label}: section_id {entry['section_id']!r} is not in sections.json")
        if entry.get("category") not in STYLE_CATEGORIES:
            result.error(f"{label}: category must be one of {', '.join(STYLE_CATEGORIES)}")
        for field in ("property", "value"):
            if not nonempty(entry.get(field)):
                result.error(f"{label}: missing {field}")
        capture = geometry_provenance(package, entry, label, f"style:{style_id}",
                                      route=route_id, viewport=viewport)
        key = entry.get("capture_key")
        if capture is not None and nonempty(key):
            target = raw_target(package, capture["capture_id"], key)
            if target is not None:
                observed = (target.get("styles") or {}).get(entry.get("property"))
                if observed is not None and str(observed).strip() != str(entry.get("value")).strip():
                    result.error(
                        f"{label}: value {entry.get('value')!r} differs from the computed "
                        f"{entry.get('property')} {observed!r} in capture {capture['capture_id']}"
                    )


def validate_behaviors(package: Package) -> None:
    result = package.result
    behaviors = package.docs["behaviors"]
    if behaviors is None or not expect_schema(behaviors, "behaviors", result):
        return
    seen = set()
    for entry in as_list(behaviors, "behaviors", "behaviors.json", result):
        behavior_id = entry.get("behavior_id")
        if not nonempty(behavior_id):
            result.error("behaviors.json: behavior missing behavior_id")
            continue
        label = f"behaviors.json: {behavior_id}"
        if behavior_id in seen:
            result.error(f"{label}: duplicate behavior_id")
        seen.add(behavior_id)
        kind = entry.get("kind")
        if kind not in BEHAVIOR_KINDS:
            result.error(f"{label}: kind must be one of {', '.join(BEHAVIOR_KINDS)}")
        route_id = entry.get("route_id")
        if route_id not in package.routes:
            result.error(f"{label}: route_id {route_id!r} is not in routes.json")
        if entry.get("section_id") is not None and entry["section_id"] not in package.sections:
            result.error(f"{label}: section_id {entry['section_id']!r} is not in sections.json")
        viewport = entry.get("viewport")
        if viewport is not None and viewport not in VIEWPORTS:
            result.error(f"{label}: viewport must be one of {', '.join(VIEWPORTS)} or null")
        for field in ("trigger", "observed"):
            if not nonempty(entry.get(field)):
                result.error(f"{label}: missing {field}")
        evidence = entry.get("evidence")
        if evidence not in BEHAVIOR_EVIDENCE:
            result.error(f"{label}: evidence must be extracted or observed")
        if evidence == "observed" and not nonempty(entry.get("method")):
            result.error(f"{label}: an observed behavior records how it was observed in method")
        capture_ids = entry.get("capture_ids")
        states = []
        if not isinstance(capture_ids, list) or not capture_ids:
            result.error(f"{label}: capture_ids must cite at least one capture")
        else:
            for capture_id in capture_ids:
                capture = capture_for(package, capture_id, label, route=route_id, viewport=viewport)
                if capture is not None:
                    states.append(capture.get("state"))
        if kind == "motion" and evidence == "extracted" and "motion" not in states:
            result.error(f"{label}: extracted motion cites a capture taken while motion ran (state motion)")
        if entry.get("details") is not None and not isinstance(entry["details"], dict):
            result.error(f"{label}: details must be an object")
        for claim in entry.get("claims") or []:
            claim_label = f"{label}: claim {claim.get('claim_id', '(unnamed)') if isinstance(claim, dict) else claim}"
            if not isinstance(claim, dict):
                result.error(f"{claim_label}: must be an object")
                continue
            if not nonempty(claim.get("text")) or not nonempty(claim.get("media_url")) \
                    or not number(claim.get("time_s")):
                result.error(f"{claim_label}: records text, media_url, and the playback time time_s")
                continue
            capture = capture_for(package, claim.get("capture_id"), claim_label, route=route_id,
                                  states=("media-frame",), need_screenshot=True)
            if capture is not None:
                if capture.get("media_url") != claim["media_url"]:
                    result.error(f"{claim_label}: capture {capture['capture_id']} shows a different media_url")
                elif number(capture.get("media_time_s")) and abs(capture["media_time_s"] - claim["time_s"]) > 0.5:
                    result.error(
                        f"{claim_label}: capture {capture['capture_id']} is at {capture['media_time_s']}s, "
                        f"not the claim time {claim['time_s']}s"
                    )
            if nonempty(claim.get("divergence_id")) and claim["divergence_id"] not in package.divergences:
                result.error(f"{claim_label}: divergence_id {claim['divergence_id']!r} is not in the ledger")


def validate_cross_references(package: Package) -> None:
    result = package.result
    for route_id, route in package.routes.items():
        for section_id in route.get("section_order") or []:
            section = package.sections.get(section_id)
            if section is None:
                result.error(f"routes.json: {route_id}: section_order names unknown section {section_id!r}")
            elif section.get("route_id") != route_id:
                result.error(f"routes.json: {route_id}: section {section_id} belongs to another route")
    for section_id, section in package.sections.items():
        label = f"sections.json: {section_id}"
        for viewport, capture_id in (section.get("capture_refs") or {}).items():
            if viewport not in VIEWPORTS:
                result.error(f"{label}: capture_refs has unknown viewport {viewport!r}")
            elif nonempty(capture_id):
                capture_for(package, capture_id, f"{label}: capture_refs.{viewport}",
                            route=section.get("route_id"), viewport=viewport)
        for divergence_id in section.get("divergence_ids") or []:
            if divergence_id not in package.divergences:
                result.error(f"{label}: divergence_id {divergence_id!r} is not in the ledger")


# --------------------------------------------------------------------------
# Readiness


def empty_readiness(reason: str) -> dict:
    return {"ready": False, "reason": reason, "sections": [], "excluded_sections": []}


def unresolved(entry: dict) -> bool:
    return entry.get("decision") not in RESOLVED_DECISIONS or entry.get("status") not in RESOLVED_STATUSES


def compute_readiness(package: Package) -> dict:
    sections = []
    excluded = []
    for section_id, section in package.sections.items():
        route_id = section.get("route_id")
        if section.get("in_scope") is False:
            excluded.append({"section_id": section_id, "route_id": route_id,
                             "reason": section.get("exclusion_reason", "")})
            continue
        blockers: list[str] = []
        surface: list[str] = []
        for gap in package.gaps.values():
            if gap.get("route_id") != route_id or gap.get("blocks_build") is not True:
                continue
            if gap.get("section_id") in (None, section_id):
                what = f"missing {gap.get('viewport')} width" if gap.get("kind") == "missing-width" else gap.get("kind")
                blockers.append(f"gap {gap.get('gap_id')} ({what}): {gap.get('reason')}")
        for entry in package.copy:
            if entry.get("section_id") != section_id:
                continue
            if entry.get("decision") == "unresolved":
                blockers.append(f"copy {entry.get('copy_id')} is unresolved")
                surface.append(f"copy {entry.get('copy_id')}: unresolved")
            elif entry.get("decision") == "omit":
                surface.append(f"copy {entry.get('copy_id')}: omit")
        for asset in package.assets:
            if asset.get("section_id") != section_id:
                continue
            if asset.get("state") == "reference-only" and asset.get("treatment") in (None, ""):
                blockers.append(f"asset {asset.get('asset_id')} has no stated treatment")
                surface.append(f"asset {asset.get('asset_id')}: no treatment")
            elif asset.get("state") == "replacement-needed":
                blockers.append(f"asset {asset.get('asset_id')} needs a replacement: {asset.get('target_asset')}")
                surface.append(f"asset {asset.get('asset_id')}: replacement needed")
        for divergence_id, entry in package.divergences.items():
            if section_id in (entry.get("section_ids") or []) and unresolved(entry):
                blockers.append(
                    f"divergence {divergence_id} is unresolved ({entry.get('decision')}/{entry.get('status')})"
                )
                surface.append(f"divergence {divergence_id}: unresolved")
        sections.append({
            "section_id": section_id,
            "route_id": route_id,
            "ready": not blockers,
            "blockers": blockers,
            "surface": surface,
        })
    ready = bool(sections) and all(entry["ready"] for entry in sections)
    return {"ready": ready, "reason": "" if ready else "blocked sections", "sections": sections,
            "excluded_sections": excluded}


# --------------------------------------------------------------------------
# Scaffolder


def route_id_for(path: str, index: int) -> str:
    if path == "/":
        return "home"
    cleaned = re.sub(r"[^a-z0-9]+", "-", path.strip("/").lower()).strip("-")
    return cleaned or f"route-{index + 1}"


CHECKLIST = """# {project} design handoff checklist

- [ ] Intake records source URLs, capture date, target store/theme/repo, and the rights statement.
- [ ] Every route has captures with screenshots at 1440, 768, and 390, or a missing-width gap.
- [ ] Static captures were taken with motion paused; motion has its own capture record.
- [ ] Sections are ordered, classified, and (for Spark) carry roster_status.
- [ ] Geometry: section boxes page-relative, element boxes section-relative, each from a capture.
- [ ] Copy: every string has source_text from a capture and a decision; replacements are approved.
- [ ] Assets: each is reference-only, downloaded (reuse declared), or replacement-needed, with a treatment.
- [ ] Observed styles cite captures and are not labeled as variables.
- [ ] Behaviors: responsive order, sticky, motion, media, and CTA destinations cite captures.
- [ ] Commerce facts (prices, offers, shipping, coupons, claims) are ledger entries or gaps, not copy.
- [ ] Inferred geometry and style entries are listed in notes.md.
- [ ] `design-handoff.py validate` reports STRUCTURE: VALID; blocked sections are listed in notes.md.
"""

NOTES = """# {project} design handoff notes

## Summary

## Evidence origin

## Inferred entries

List every inferred geometry or style entry by its identifier, for example
`geometry:home/desktop/hero::badge` or `style:hero-heading-color`, with the
reason.

## Blocked sections and next actions

## Limits of automated capture

## Handoff to next-theme-dev
"""


def command_new(args: argparse.Namespace) -> int:
    out = Path(args.out)
    targets = [out / filename for filename in list(FILES.values()) + list(TEXT_FILES)]
    existing = [path.name for path in targets if path.exists()]
    if existing and not args.force:
        print(f"refusing to overwrite existing package files ({', '.join(existing)}); pass --force",
              file=sys.stderr)
        return 2
    if args.reuse in ("reuse-allowed", "reuse-not-allowed") and not args.reuse_statement:
        print("--reuse-statement is required when --reuse is stated", file=sys.stderr)
        return 2
    family = args.theme_family
    runtime = args.runtime_contract or (THEME_FAMILIES.get(family) or "unknown")
    paths = [path.strip() for path in (args.routes or "/").split(",") if path.strip()]
    routes = [route_id_for(path, index) for index, path in enumerate(paths)]
    now = _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    (out / "captures" / "raw").mkdir(parents=True, exist_ok=True)

    def write(key: str, body: dict) -> None:
        (out / FILES[key]).write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")

    write("handoff", {
        "schema_version": SCHEMA["handoff"],
        "generated_at": now,
        "generator": "next-theme-dev design-handoff.py new",
        "project": args.project,
        "mode": args.mode,
        "source": {
            "kind": "live-site",
            "urls": args.source_url or [],
            "supplied_html": [],
            "captured_on": args.captured_on or now[:10],
            "rights": {
                "owner": args.owner or "not stated",
                "reuse": args.reuse,
                "statement": args.reuse_statement or "",
                "stated_by": args.stated_by or "",
            },
        },
        "target": {
            "store": args.store or "",
            "repo": args.repo or "",
            "preview_url": args.preview_url or "",
            "theme_id": args.theme_id or "",
            "theme_family": family,
            "runtime_contract": runtime,
        },
        "gaps": [],
        "unresolved_questions": [],
    })
    write("captures", {"schema_version": SCHEMA["captures"], "captures": []})
    write("routes", {"schema_version": SCHEMA["routes"], "routes": [
        {
            "route_id": route_id,
            "source_url": (args.source_url or [""])[min(index, len(args.source_url or [""]) - 1)],
            "storefront_path": path,
            "theme_template": "",
            "section_order": [],
            "status": "draft",
            "notes": "",
        }
        for index, (route_id, path) in enumerate(zip(routes, paths))
    ]})
    write("sections", {"schema_version": SCHEMA["sections"], "sections": []})
    write("assets", {"schema_version": SCHEMA["assets"], "assets": []})
    write("divergence", {"schema_version": SCHEMA["divergence"], "entries": []})
    write("coverage", {
        "schema_version": SCHEMA["coverage"],
        "viewports": {name: {"expected_width": width} for name, width in VIEWPORTS.items()},
        "coverage": [
            {"route_id": route_id, **{name: {"capture_id": "", "source_ref": "", "preview_ref": "", "status": ""}
                                     for name in VIEWPORTS}}
            for route_id in routes
        ],
    })
    write("geometry", {"schema_version": SCHEMA["geometry"], "project": args.project, "source": "dom-capture",
                       "extracted_at": now, "routes": []})
    write("copy", {"schema_version": SCHEMA["copy"], "project": args.project, "source": "dom-capture",
                   "extracted_at": now, "strings": [], "allowed_deviations": []})
    write("styles", {"schema_version": SCHEMA["styles"], "project": args.project, "source": "dom-capture",
                     "extracted_at": now, "styles": []})
    write("behaviors", {"schema_version": SCHEMA["behaviors"], "project": args.project, "behaviors": []})
    (out / "validation-checklist.md").write_text(CHECKLIST.format(project=args.project), encoding="utf-8")
    (out / "notes.md").write_text(NOTES.format(project=args.project), encoding="utf-8")
    print(f"[next-theme-dev] live-site package created: {out}")
    return 0


# --------------------------------------------------------------------------
# Output


def print_result(result: Result, readiness: dict) -> None:
    for warning in result.warnings:
        print(f"Warning: {warning}")
    for error in result.errors:
        print(f"Error: {error}")
    if result.errors:
        print(f"[next-theme-dev] design-handoff STRUCTURE: INVALID ({len(result.errors)} error(s), "
              f"{len(result.warnings)} warning(s))")
        return
    print(f"[next-theme-dev] design-handoff STRUCTURE: VALID (live-site, {len(result.warnings)} warning(s))")
    sections = readiness["sections"]
    blocked = [entry for entry in sections if not entry["ready"]]
    if readiness["ready"]:
        print(f"[next-theme-dev] design-handoff READINESS: READY ({len(sections)} section(s) ready to build)")
    elif not sections:
        print(f"[next-theme-dev] design-handoff READINESS: NOT READY ({readiness['reason']})")
    else:
        print(f"[next-theme-dev] design-handoff READINESS: NOT READY ({len(blocked)} of "
              f"{len(sections)} intended section(s) blocked)")
    for entry in sections:
        state = "READY  " if entry["ready"] else "BLOCKED"
        print(f"  {state} {entry['route_id']}/{entry['section_id']}")
        for blocker in entry["blockers"]:
            print(f"    blocker: {blocker}")
        # Blockers are already surfaced; list only the non-blocking items the
        # operator should still see before the section is built.
        for item in entry["surface"]:
            if not item.endswith(": unresolved") and not item.endswith(": no treatment") \
                    and not item.endswith(": replacement needed"):
                print(f"    surface to operator: {item}")
    for entry in readiness["excluded_sections"]:
        print(f"  EXCLUDED {entry['route_id']}/{entry['section_id']}: {entry['reason']}")


def command_validate(args: argparse.Namespace) -> int:
    directory = Path(args.package)
    if not directory.is_dir():
        print(f"design-handoff: {directory} is not a directory", file=sys.stderr)
        return 2
    try:
        result, readiness = validate(directory)
    except SiblingMissing as error:
        print(str(error), file=sys.stderr)
        return 2
    print_result(result, readiness)
    if args.report:
        report = {
            "schema_version": REPORT_SCHEMA,
            "package": str(directory),
            "structure": {
                "valid": not result.errors,
                "errors": result.errors,
                "warnings": result.warnings,
            },
            "readiness": readiness if not result.errors else empty_readiness("structurally invalid"),
        }
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if result.errors:
        return 1
    if args.require_ready and not readiness["ready"]:
        return 3
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)

    new = commands.add_parser("new", help="write a blank live-site package")
    new.add_argument("--out", required=True)
    new.add_argument("--project", required=True)
    new.add_argument("--source-url", action="append")
    new.add_argument("--routes", help='comma-separated storefront paths, default "/"')
    new.add_argument("--captured-on", help="capture date, YYYY-MM-DD (default today)")
    new.add_argument("--store")
    new.add_argument("--repo")
    new.add_argument("--preview-url")
    new.add_argument("--theme-id")
    new.add_argument("--theme-family", choices=tuple(THEME_FAMILIES), default="spark")
    new.add_argument("--runtime-contract", choices=RUNTIME_CONTRACTS)
    new.add_argument("--owner", help="source owner, as the operator states it")
    new.add_argument("--reuse", choices=REUSE, default="not-stated")
    new.add_argument("--reuse-statement")
    new.add_argument("--stated-by")
    new.add_argument("--mode", choices=MODES, default="implementation-handoff")
    new.add_argument("--force", action="store_true")

    check = commands.add_parser("validate", help="validate a live-site package")
    check.add_argument("package")
    check.add_argument("--report", help="write the JSON report here")
    check.add_argument("--require-ready", action="store_true",
                       help="exit 3 when the package is valid but not every intended section is ready")

    args = parser.parse_args(argv)
    if args.command == "new":
        return command_new(args)
    return command_validate(args)


if __name__ == "__main__":
    sys.exit(main())
