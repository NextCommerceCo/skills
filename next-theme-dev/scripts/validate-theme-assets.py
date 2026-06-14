#!/usr/bin/env python3
"""Validate a Next/Spark theme asset export manifest.

The script checks facts that are easy to miss after Figma export:
relative paths, deterministic names, dimensions, file size, transparency,
asset_url mapping, and explicit clean-export confirmations.
"""

from __future__ import annotations

import argparse
import json
import re
import struct
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Tuple
from xml.etree import ElementTree


MEDIA_EXTENSIONS = {
    ".gif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".svg",
    ".webp",
}

SAFE_COMPONENT_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
NUMBER_RE = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)")


class ImageInfo:
    def __init__(
        self,
        width: Optional[int],
        height: Optional[int],
        alpha: Optional[bool],
        fmt: str,
    ) -> None:
        self.width = width
        self.height = height
        self.alpha = alpha
        self.fmt = fmt


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate a Figma-to-Spark asset export manifest."
    )
    parser.add_argument(
        "--theme",
        required=True,
        help="Path to the theme root containing assets/, partials/, templates/, etc.",
    )
    parser.add_argument(
        "--manifest",
        required=True,
        help="Manifest path. Relative paths are resolved from --theme. Use '-' for stdin.",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Treat warnings as failures.",
    )
    return parser.parse_args()


def load_manifest(theme_root: Path, manifest_arg: str) -> Dict[str, Any]:
    if manifest_arg == "-":
        return json.load(sys.stdin)

    manifest_path = Path(manifest_arg)
    if not manifest_path.is_absolute():
        manifest_path = theme_root / manifest_path
    with manifest_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def manifest_entries(manifest: Any) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if isinstance(manifest, list):
        return manifest, {}
    if not isinstance(manifest, dict):
        raise TypeError("Manifest must be an object with assets[] or a list of assets.")
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise TypeError("Manifest object must contain an assets array.")
    defaults = {key: value for key, value in manifest.items() if key != "assets"}
    return assets, defaults


def read_image_info(path: Path) -> ImageInfo:
    suffix = path.suffix.lower()
    data = path.read_bytes()
    if suffix == ".png":
        return read_png_info(data)
    if suffix in {".jpg", ".jpeg"}:
        return read_jpeg_info(data)
    if suffix == ".gif":
        return read_gif_info(data)
    if suffix == ".svg":
        return read_svg_info(path)
    if suffix == ".webp":
        return read_webp_info(data)
    raise ValueError(f"Unsupported image extension for validation: {suffix}")


def read_png_info(data: bytes) -> ImageInfo:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 33:
        raise ValueError("Invalid PNG signature.")
    width, height = struct.unpack(">II", data[16:24])
    color_type = data[25]
    has_alpha = color_type in {4, 6}

    pos = 8
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_type = data[pos + 4 : pos + 8]
        if chunk_type == b"tRNS":
            has_alpha = True
        pos += 12 + length
    return ImageInfo(width, height, has_alpha, "png")


def read_jpeg_info(data: bytes) -> ImageInfo:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG signature.")

    pos = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while pos + 4 < len(data):
        while pos < len(data) and data[pos] != 0xFF:
            pos += 1
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in {0xD8, 0xD9}:
            continue
        if pos + 2 > len(data):
            break
        segment_length = struct.unpack(">H", data[pos : pos + 2])[0]
        if segment_length < 2 or pos + segment_length > len(data):
            break
        if marker in sof_markers:
            height, width = struct.unpack(">HH", data[pos + 3 : pos + 7])
            return ImageInfo(width, height, False, "jpeg")
        pos += segment_length
    raise ValueError("Could not find JPEG dimensions.")


def read_gif_info(data: bytes) -> ImageInfo:
    if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
        raise ValueError("Invalid GIF signature.")
    width, height = struct.unpack("<HH", data[6:10])
    has_alpha = False
    pos = 13
    while pos + 8 < len(data):
        if data[pos] == 0x21 and data[pos + 1] == 0xF9 and data[pos + 2] == 0x04:
            packed = data[pos + 3]
            if packed & 0x01:
                has_alpha = True
            pos += 8
        else:
            pos += 1
    return ImageInfo(width, height, has_alpha, "gif")


def read_svg_info(path: Path) -> ImageInfo:
    tree = ElementTree.parse(path)
    root = tree.getroot()
    width = parse_svg_number(root.attrib.get("width"))
    height = parse_svg_number(root.attrib.get("height"))
    view_box = root.attrib.get("viewBox") or root.attrib.get("viewbox")
    if (width is None or height is None) and view_box:
        parts = [part for part in re.split(r"[\s,]+", view_box.strip()) if part]
        if len(parts) == 4:
            width = width or int(round(float(parts[2])))
            height = height or int(round(float(parts[3])))
    return ImageInfo(width, height, True, "svg")


def parse_svg_number(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    match = NUMBER_RE.match(value)
    if not match:
        return None
    return int(round(float(match.group(1))))


def read_webp_info(data: bytes) -> ImageInfo:
    if not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise ValueError("Invalid WebP signature.")
    pos = 12
    while pos + 8 <= len(data):
        chunk = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        body = data[pos + 8 : pos + 8 + size]
        if chunk == b"VP8X" and len(body) >= 10:
            flags = body[0]
            width = 1 + int.from_bytes(body[4:7], "little")
            height = 1 + int.from_bytes(body[7:10], "little")
            return ImageInfo(width, height, bool(flags & 0x10), "webp")
        if chunk == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            raw_width, raw_height = struct.unpack("<HH", body[6:10])
            return ImageInfo(raw_width & 0x3FFF, raw_height & 0x3FFF, False, "webp")
        if chunk == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            b1, b2, b3, b4 = body[1], body[2], body[3], body[4]
            width = 1 + (((b2 & 0x3F) << 8) | b1)
            height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
            return ImageInfo(width, height, None, "webp")
        pos += 8 + size + (size % 2)
    raise ValueError("Could not find WebP dimensions.")


def validate_asset(
    entry: Dict[str, Any],
    defaults: Dict[str, Any],
    theme_root: Path,
    index: int,
    errors: List[str],
    warnings: List[str],
) -> None:
    label = f"assets[{index}]"
    rel_path_value = entry.get("path")
    if not rel_path_value:
        errors.append(f"{label}: missing required path.")
        return
    if not isinstance(rel_path_value, str):
        errors.append(f"{label}: path must be a string.")
        return
    if "\\" in rel_path_value:
        errors.append(f"{label}: path must use forward slashes: {rel_path_value}")
        return

    rel_path = PurePosixPath(rel_path_value)
    if rel_path.is_absolute() or ".." in rel_path.parts:
        errors.append(f"{label}: path must be relative and stay inside the theme: {rel_path_value}")
        return
    if not rel_path.parts or rel_path.parts[0] != "assets":
        errors.append(f"{label}: path must start with assets/: {rel_path_value}")
        return

    for component in rel_path.parts:
        if not SAFE_COMPONENT_RE.match(component):
            errors.append(
                f"{label}: path component must be lowercase and URL-safe: {component}"
            )
        if "_" in component:
            warnings.append(
                f"{label}: prefer kebab-case over underscores in asset paths: {component}"
            )

    full_path = theme_root / Path(*rel_path.parts)
    if not full_path.exists():
        errors.append(f"{label}: file does not exist: {full_path}")
        return
    if not full_path.is_file():
        errors.append(f"{label}: path is not a file: {full_path}")
        return

    suffix = full_path.suffix.lower()
    if suffix not in MEDIA_EXTENSIONS:
        errors.append(f"{label}: unsupported media extension for asset check: {suffix}")
        return
    if suffix != full_path.suffix:
        errors.append(f"{label}: extension must be lowercase: {full_path.suffix}")

    expected_asset_url_path = "/".join(rel_path.parts[1:])
    asset_url_path = entry.get("asset_url_path")
    if asset_url_path and asset_url_path != expected_asset_url_path:
        errors.append(
            f"{label}: asset_url_path should be {expected_asset_url_path}, got {asset_url_path}"
        )
    if asset_url_path and str(asset_url_path).startswith("assets/"):
        errors.append(f"{label}: asset_url_path must not include the assets/ prefix.")

    max_bytes = entry.get("max_bytes")
    if max_bytes is not None:
        actual_bytes = full_path.stat().st_size
        if actual_bytes > int(max_bytes):
            errors.append(
                f"{label}: {actual_bytes} bytes exceeds max_bytes {max_bytes}: {rel_path_value}"
            )

    try:
        info = read_image_info(full_path)
    except Exception as exc:
        errors.append(f"{label}: cannot inspect image: {exc}")
        return

    check_dimension(entry, "expected_width", info.width, label, errors)
    check_dimension(entry, "expected_height", info.height, label, errors)

    if entry.get("requires_alpha"):
        if info.alpha is False:
            errors.append(f"{label}: requires alpha but {info.fmt} has no alpha channel.")
        elif info.alpha is None:
            errors.append(f"{label}: requires alpha but alpha could not be verified.")

    if (entry.get("forbid_badges") or entry.get("forbid_baked_text")) and not entry.get(
        "clean_export_verified"
    ):
        errors.append(
            f"{label}: set clean_export_verified=true after checking for baked badges/text."
        )

    role = str(entry.get("role", "")).lower()
    decorative = bool(entry.get("decorative"))
    alt = entry.get("alt")
    if ("logo" in role or "press" in role) and not decorative and not alt:
        warnings.append(f"{label}: press/logo assets should include useful alt text.")

    figma_file_key = entry.get("figma_file_key") or defaults.get("figma_file_key")
    figma_node_id = entry.get("figma_node_id")
    if not figma_file_key:
        warnings.append(f"{label}: missing figma_file_key.")
    if not figma_node_id and entry.get("source") != "manual":
        warnings.append(f"{label}: missing figma_node_id.")


def check_dimension(
    entry: Dict[str, Any],
    key: str,
    actual: Optional[int],
    label: str,
    errors: List[str],
) -> None:
    expected = entry.get(key)
    if expected is None:
        return
    if actual is None:
        errors.append(f"{label}: {key} is {expected}, but actual dimension is unknown.")
        return
    if int(expected) != actual:
        errors.append(f"{label}: {key} is {expected}, actual is {actual}.")


def main() -> int:
    args = parse_args()
    theme_root = Path(args.theme).resolve()
    if not theme_root.exists():
        print(f"ERROR: theme path does not exist: {theme_root}", file=sys.stderr)
        return 2
    if not (theme_root / "assets").exists():
        print(f"ERROR: theme path has no assets/ directory: {theme_root}", file=sys.stderr)
        return 2

    try:
        manifest = load_manifest(theme_root, args.manifest)
        entries, defaults = manifest_entries(manifest)
    except Exception as exc:
        print(f"ERROR: cannot load manifest: {exc}", file=sys.stderr)
        return 2

    errors: List[str] = []
    warnings: List[str] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"assets[{index}]: entry must be an object.")
            continue
        validate_asset(entry, defaults, theme_root, index, errors, warnings)

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    if errors or (args.strict and warnings):
        print(
            f"FAILED: {len(errors)} error(s), {len(warnings)} warning(s), {len(entries)} asset(s).",
            file=sys.stderr,
        )
        return 1

    print(f"OK: {len(entries)} asset(s) passed with {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
