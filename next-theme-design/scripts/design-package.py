#!/usr/bin/env python3
"""Turn capture-script output into records of a live-site handoff package.

next-theme-dev owns the package format, its scaffolder, and its validator.
This helper does the mechanical authoring steps that are easy to get wrong by
hand: it files each raw capture as a capture record, derives geometry with
page-relative section boxes and section-relative element boxes, drafts copy
and observed-style entries from the captured values, and fills viewport
coverage from the captures. Every decision (copy decisions, asset treatments,
divergences, gaps) stays with the package author.

    python3 design-package.py check-siblings
    python3 design-package.py record   --package <dir> --raw <raw.json> \\
        [--screenshot <png>] [--screenshot-kind full-page|viewport] [--tool <text>]
    python3 design-package.py geometry --package <dir> --capture <capture_id>
    python3 design-package.py draft-copy   --package <dir> --capture <capture_id> [--keys k1,k2]
    python3 design-package.py draft-styles --package <dir> --capture <capture_id> \\
        --keys k1,k2 [--properties font-family,color]
    python3 design-package.py coverage --package <dir>

Exit codes: 0 done, 1 refused (the input breaks a format rule), 2 usage error
or a missing sibling skill.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
SIBLINGS = {
    "next-theme-dev": (
        ("scripts/design-handoff.py", "scripts/assert-geometry.mjs"),
        "the package format, its scaffolder and validator, and the geometry assertion",
    ),
    "next-theme-figma": (
        ("references/spark-section-roster.json", "scripts/copy-lint.py"),
        "the Spark section roster and the copy lint",
    ),
}
RAW_SCHEMA = "next-theme-design/capture-output/v1"
VIEWPORTS = {"desktop": 1440, "tablet": 768, "mobile": 390}
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
DEFAULT_STYLE_PROPERTIES = ("font-family", "font-size", "font-weight", "line-height", "color", "background-color")
STYLE_CATEGORY = {
    "font-family": "font", "font-size": "font", "font-weight": "font", "font-style": "font",
    "line-height": "font", "letter-spacing": "font", "text-transform": "font",
    "color": "color", "background-color": "color", "border-top-color": "color",
    "border-radius": "radius", "box-shadow": "shadow", "border-top-width": "border",
    "border-top-style": "border", "gap": "spacing", "row-gap": "spacing", "column-gap": "spacing",
    "width": "size", "height": "size", "max-width": "size",
}
TEXT_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "span", "li", "small", "strong", "em", "label", "blockquote"}
CONTROL_TAGS = {"a", "button", "input", "select", "summary"}
IMAGE_TAGS = {"img", "video", "picture", "svg", "canvas", "iframe"}


class Refused(RuntimeError):
    """The input would break a rule of the package format."""


def check_siblings() -> None:
    missing = []
    for skill, (files, purpose) in SIBLINGS.items():
        directory = SKILL_DIR.parent / skill
        if not all((directory / name).is_file() for name in files):
            missing.append((skill, directory, purpose))
    if missing:
        lines = []
        for skill, directory, purpose in missing:
            lines.append(
                f"next-theme-design: the sibling skill {skill} is not installed next to "
                f"next-theme-design (expected {directory}). It provides {purpose}. Install it "
                f"with `./skills.sh install <target> {skill}` from a NextCommerceCo/skills "
                f"checkout, or `npx skills add NextCommerceCo/skills -g --skill {skill}`."
            )
        lines.append(
            "next-theme-figma, next-theme-design, and next-theme-dev are installed together. "
            "Stop here; do not re-derive what the missing skill provides."
        )
        raise SystemExit("\n".join(lines))


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def save(path: Path, body: dict) -> None:
    path.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def package_file(package: Path, name: str) -> Path:
    path = package / name
    if not path.is_file():
        raise Refused(f"{path} not found; create the package with next-theme-dev's design-handoff.py new")
    return path


def png_width(path: Path) -> int:
    data = path.read_bytes()[:24]
    if not data.startswith(PNG_SIGNATURE) or data[12:16] != b"IHDR":
        raise Refused(f"{path} is not a PNG screenshot")
    return struct.unpack(">I", data[16:20])[0]


def relative(package: Path, path: Path) -> str:
    return path.resolve().relative_to(package.resolve()).as_posix()


def place(package: Path, source: Path, destination: str) -> str:
    """Copy a file into the package unless it is already there."""
    target = package / destination
    if source.resolve() != target.resolve():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    return destination


def readiness_status(entry: dict) -> str:
    status = (entry or {}).get("status", "none")
    return status if status in ("ready", "timeout", "error", "none", "unsupported") else "error"


# --------------------------------------------------------------------------


def command_record(args: argparse.Namespace) -> None:
    package = Path(args.package)
    raw_path = Path(args.raw)
    raw = load(raw_path)
    if raw.get("schema_version") != RAW_SCHEMA:
        raise Refused(f"{raw_path}: not capture-script output ({RAW_SCHEMA})")
    capture = raw.get("capture") or {}
    page = raw.get("page") or {}
    capture_id = capture.get("capture_id") or ""
    if not re.match(r"^[a-z0-9][a-z0-9._-]*$", capture_id):
        raise Refused(f"{raw_path}: capture.capture_id {capture_id!r} must be a lowercase id")
    viewport = capture.get("viewport")
    if viewport not in VIEWPORTS:
        raise Refused(f"{raw_path}: capture.viewport must be one of {', '.join(VIEWPORTS)}")
    width = page.get("viewport_width")
    if width != VIEWPORTS[viewport]:
        raise Refused(
            f"{raw_path}: the page was {width}px wide; a {viewport} capture is taken at "
            f"{VIEWPORTS[viewport]}px. Resize the viewport and capture again."
        )
    dpr = page.get("device_pixel_ratio") or 1
    screenshot = None
    if args.screenshot:
        source = Path(args.screenshot)
        actual = png_width(source)
        expected = round(width * dpr)
        if actual != expected:
            raise Refused(
                f"{source} is {actual}px wide; viewport {width} x device pixel ratio {dpr} "
                f"requires {expected}px. Save an unscaled screenshot."
            )
        screenshot = place(package, source, f"captures/{capture_id}.png")
    raw_rel = place(package, raw_path, f"captures/raw/{capture_id}.json")
    readiness = raw.get("readiness") or {}
    media_frame = (raw.get("media_frame") or [None])[0]
    record = {
        "capture_id": capture_id,
        "route_id": capture.get("route_id", ""),
        "viewport": viewport,
        "state": capture.get("state", ""),
        "state_detail": capture.get("state_detail", ""),
        "url": page.get("url", ""),
        "viewport_width": width,
        "viewport_height": page.get("viewport_height"),
        "device_pixel_ratio": dpr,
        "scroll_x": page.get("scroll_x", 0),
        "scroll_y": page.get("scroll_y", 0),
        "document_width": page.get("document_width"),
        "document_height": page.get("document_height"),
        "browser": page.get("browser", ""),
        "tool": args.tool or "",
        "captured_at": page.get("captured_at", ""),
        "html_sha256": page.get("html_sha256", ""),
        "readiness": {key: readiness_status(readiness.get(key)) for key in ("fonts", "images", "media_metadata")},
        "motion": (raw.get("motion") or {}).get("mode", "none"),
        "media_url": media_frame.get("media_url", "") if media_frame else "",
        "media_time_s": media_frame.get("time_s") if media_frame else None,
        "screenshot": screenshot,
        "screenshot_kind": args.screenshot_kind if screenshot else None,
        "raw_output": raw_rel,
        "boxes": raw.get("boxes") or {},
        "gaps": raw.get("gaps") or [],
    }
    captures_path = package_file(package, "captures.json")
    captures = load(captures_path)
    entries = [entry for entry in captures.get("captures", []) if entry.get("capture_id") != capture_id]
    entries.append(record)
    captures["captures"] = entries
    save(captures_path, captures)
    print(f"[next-theme-design] recorded {capture_id} ({viewport}, {record['state']}, "
          f"screenshot {'yes' if screenshot else 'no'}, {len(record['boxes'])} box(es))")


def captured(package: Path, capture_id: str) -> tuple[dict, dict]:
    captures = load(package_file(package, "captures.json"))
    record = next((entry for entry in captures.get("captures", []) if entry.get("capture_id") == capture_id), None)
    if record is None:
        raise Refused(f"capture {capture_id} is not in captures.json; record it first")
    raw_path = package / (record.get("raw_output") or "")
    raw = load(raw_path) if record.get("raw_output") and raw_path.is_file() else {}
    return record, raw


def element_role(tag: str) -> str:
    if tag in TEXT_TAGS:
        return "text"
    if tag in CONTROL_TAGS:
        return "control"
    if tag in IMAGE_TAGS:
        return "image"
    return "container"


def command_geometry(args: argparse.Namespace) -> None:
    package = Path(args.package)
    record, raw = captured(package, args.capture)
    if record.get("state") not in ("static", "interaction"):
        raise Refused(f"capture {args.capture} is a {record.get('state')} capture; geometry comes from static captures")
    sections_doc = load(package_file(package, "sections.json"))
    route_id = record["route_id"]
    viewport = record["viewport"]
    boxes = record.get("boxes") or {}
    targets = raw.get("targets") or {}
    geometry_path = package_file(package, "geometry.json")
    geometry = load(geometry_path)
    route = next((entry for entry in geometry.setdefault("routes", []) if entry.get("route_id") == route_id), None)
    if route is None:
        route = {"route_id": route_id, "viewports": {}}
        geometry["routes"].append(route)
    previous = route.setdefault("viewports", {}).get(viewport) or {}
    previous_sections = {entry.get("section_id"): entry for entry in previous.get("sections", [])}
    wanted = [entry for entry in sections_doc.get("sections", []) if entry.get("route_id") == route_id]
    frame_sections = []
    skipped = []
    for section in wanted:
        section_id = section["section_id"]
        if section_id not in boxes:
            skipped.append(section_id)
            continue
        old = previous_sections.get(section_id, {})
        old_elements = {entry.get("element_id"): entry for entry in old.get("elements", [])}
        section_box = boxes[section_id]
        elements = []
        prefix = f"{section_id}::"
        for key, element_box in boxes.items():
            if not key.startswith(prefix):
                continue
            element_id = key[len(prefix):]
            previous_element = old_elements.get(element_id, {})
            if previous_element.get("basis") == "inferred":
                elements.append(previous_element)
                continue
            tag = (targets.get(key) or {}).get("tag", "")
            element = {
                "element_id": element_id,
                "capture_id": record["capture_id"],
                "selector": previous_element.get("selector") or f'[data-geo="{section_id}-{element_id}"]',
                "source_selector": (targets.get(key) or {}).get("selector", ""),
                "role": previous_element.get("role") or element_role(tag),
                "box": {
                    "x": round(element_box["x"] - section_box["x"], 2),
                    "y": round(element_box["y"] - section_box["y"], 2),
                    "width": element_box["width"],
                    "height": element_box["height"],
                },
            }
            for keep in ("assert", "align_anchor", "tolerance_px"):
                if keep in previous_element:
                    element[keep] = previous_element[keep]
            elements.append(element)
        for element_id, previous_element in old_elements.items():
            if previous_element.get("basis") == "inferred" and all(e["element_id"] != element_id for e in elements):
                elements.append(previous_element)
        entry = {
            "section_id": section_id,
            "capture_id": record["capture_id"],
            "selector": old.get("selector") or f'[data-geo-section="{section_id}"]',
            "source_selector": (targets.get(section_id) or {}).get("selector", section.get("source_selector", "")),
            "box": dict(section_box),
            "elements": elements,
            "alignment_groups": old.get("alignment_groups", []),
            "gaps": old.get("gaps", []),
        }
        frame_sections.append(entry)
    route["viewports"][viewport] = {
        "capture_id": record["capture_id"],
        "frame_width": record["viewport_width"],
        "frame_height": record.get("document_height") or 0,
        "sections": frame_sections,
    }
    save(geometry_path, geometry)
    print(f"[next-theme-design] geometry {route_id}/{viewport} from {record['capture_id']}: "
          f"{len(frame_sections)} section(s)"
          + (f"; no box for {', '.join(skipped)} (capture it or record a gap)" if skipped else ""))


def copy_id_for(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")


def command_draft_copy(args: argparse.Namespace) -> None:
    package = Path(args.package)
    record, raw = captured(package, args.capture)
    if not raw:
        raise Refused(f"capture {args.capture} has no raw_output to draft copy from")
    wanted = set(filter(None, (args.keys or "").split(",")))
    copy_path = package_file(package, "copy.json")
    copy = load(copy_path)
    existing = {entry.get("copy_id") for entry in copy.get("strings", [])}
    added = 0
    for key, target in (raw.get("targets") or {}).items():
        if wanted and key not in wanted:
            continue
        if not wanted and "::" not in key:
            continue
        texts = [target.get("text") or ""] if not target.get("matches") else [
            match.get("text") or "" for match in target["matches"]
        ]
        for index, text in enumerate(texts):
            text = text.strip() or (target.get("accessible_name") or "").strip()
            if not text:
                continue
            copy_id = copy_id_for(key) + (f"_{index + 1}" if len(texts) > 1 else "")
            if copy_id in existing:
                continue
            copy.setdefault("strings", []).append({
                "copy_id": copy_id,
                "section_id": key.split("::", 1)[0],
                "capture_id": record["capture_id"],
                "capture_key": key,
                "viewport": record["viewport"],
                "role": "",
                "source_text": text,
                "decision": "unresolved",
                "text": "",
            })
            existing.add(copy_id)
            added += 1
    save(copy_path, copy)
    print(f"[next-theme-design] drafted {added} copy string(s) from {record['capture_id']}; "
          "set role and a decision for each")


def command_draft_styles(args: argparse.Namespace) -> None:
    package = Path(args.package)
    record, raw = captured(package, args.capture)
    if not raw:
        raise Refused(f"capture {args.capture} has no raw_output to draft styles from")
    keys = [key for key in (args.keys or "").split(",") if key]
    properties = [prop for prop in (args.properties or ",".join(DEFAULT_STYLE_PROPERTIES)).split(",") if prop]
    styles_path = package_file(package, "observed-styles.json")
    styles = load(styles_path)
    existing = {entry.get("style_id") for entry in styles.get("styles", [])}
    added = 0
    for key in keys:
        target = (raw.get("targets") or {}).get(key)
        if not target or not target.get("styles"):
            raise Refused(f"capture {record['capture_id']} has no styles for target {key}")
        for prop in properties:
            value = target["styles"].get(prop)
            if value is None:
                continue
            style_id = f"{copy_id_for(key)}-{record['viewport']}-{prop}".replace("_", "-")
            if style_id in existing:
                continue
            styles.setdefault("styles", []).append({
                "style_id": style_id,
                "route_id": record["route_id"],
                "viewport": record["viewport"],
                "section_id": key.split("::", 1)[0],
                "capture_id": record["capture_id"],
                "capture_key": key,
                "source_selector": target.get("selector", ""),
                "property": prop,
                "value": value,
                "category": STYLE_CATEGORY.get(prop, "other"),
            })
            existing.add(style_id)
            added += 1
    save(styles_path, styles)
    print(f"[next-theme-design] drafted {added} observed style(s) from {record['capture_id']}")


def command_coverage(args: argparse.Namespace) -> None:
    package = Path(args.package)
    captures = load(package_file(package, "captures.json")).get("captures", [])
    handoff = load(package_file(package, "design-handoff.json"))
    routes = [entry["route_id"] for entry in load(package_file(package, "routes.json")).get("routes", [])]
    coverage_path = package_file(package, "viewport-coverage.json")
    coverage = load(coverage_path)
    previous = {entry.get("route_id"): entry for entry in coverage.get("coverage", [])}
    rows = []
    unresolved = []
    for route_id in routes:
        row = {"route_id": route_id}
        for viewport in VIEWPORTS:
            old = (previous.get(route_id) or {}).get(viewport) or {}
            candidates = [
                entry for entry in captures
                if entry.get("route_id") == route_id and entry.get("viewport") == viewport
                and entry.get("screenshot") and entry.get("state") == "static"
            ]
            candidates.sort(key=lambda entry: entry.get("screenshot_kind") != "full-page")
            gap = next((entry for entry in handoff.get("gaps", [])
                        if entry.get("kind") == "missing-width" and entry.get("route_id") == route_id
                        and entry.get("viewport") == viewport), None)
            if candidates:
                chosen = candidates[0]
                row[viewport] = {"capture_id": chosen["capture_id"], "source_ref": chosen["screenshot"],
                                 "preview_ref": old.get("preview_ref", ""), "status": "captured"}
            elif gap:
                row[viewport] = {"capture_id": "", "source_ref": "", "preview_ref": old.get("preview_ref", ""),
                                 "status": "gap", "gap_id": gap["gap_id"]}
            else:
                row[viewport] = {"capture_id": "", "source_ref": "", "preview_ref": "", "status": ""}
                unresolved.append(f"{route_id}/{viewport}")
        rows.append(row)
    coverage["coverage"] = rows
    save(coverage_path, coverage)
    print("[next-theme-design] coverage written"
          + (f"; no capture or gap for {', '.join(unresolved)}" if unresolved else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check-siblings", help="confirm next-theme-dev and next-theme-figma are installed")
    record = commands.add_parser("record", help="file a raw capture as a capture record")
    record.add_argument("--package", required=True)
    record.add_argument("--raw", required=True)
    record.add_argument("--screenshot")
    record.add_argument("--screenshot-kind", choices=("full-page", "viewport"), default="full-page")
    record.add_argument("--tool", help="the browser tool that ran the capture, for example playwright")
    geometry = commands.add_parser("geometry", help="derive geometry entries from a static capture")
    geometry.add_argument("--package", required=True)
    geometry.add_argument("--capture", required=True)
    draft_copy = commands.add_parser("draft-copy", help="draft copy entries from captured text")
    draft_copy.add_argument("--package", required=True)
    draft_copy.add_argument("--capture", required=True)
    draft_copy.add_argument("--keys", help="comma-separated target keys; default every element target")
    draft_styles = commands.add_parser("draft-styles", help="draft observed-style entries from computed styles")
    draft_styles.add_argument("--package", required=True)
    draft_styles.add_argument("--capture", required=True)
    draft_styles.add_argument("--keys", required=True)
    draft_styles.add_argument("--properties")
    coverage = commands.add_parser("coverage", help="fill viewport-coverage.json from the captures")
    coverage.add_argument("--package", required=True)
    args = parser.parse_args(argv)

    try:
        check_siblings()
    except SystemExit as error:
        print(error, file=sys.stderr)
        return 2
    if args.command == "check-siblings":
        print("[next-theme-design] next-theme-dev and next-theme-figma are installed next to this skill")
        return 0
    handlers = {
        "record": command_record,
        "geometry": command_geometry,
        "draft-copy": command_draft_copy,
        "draft-styles": command_draft_styles,
        "coverage": command_coverage,
    }
    try:
        handlers[args.command](args)
    except Refused as error:
        print(f"design-package: {error}", file=sys.stderr)
        return 1
    except (OSError, json.JSONDecodeError) as error:
        print(f"design-package: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
