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
import zlib
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
EXTERNAL_REF_RE = re.compile(
    r"(?:^|url\(\s*['\"]?|@import\s+(?:url\(\s*)?['\"]?)(?:https?:|//|data:|javascript:)",
    re.IGNORECASE,
)


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
        info = read_png_info(data)
    elif suffix in {".jpg", ".jpeg"}:
        info = read_jpeg_info(data)
    elif suffix == ".gif":
        info = read_gif_info(data)
    elif suffix == ".ico":
        info = read_ico_info(data)
    elif suffix == ".svg":
        info = read_svg_info(path)
    elif suffix == ".webp":
        info = read_webp_info(data)
    else:
        raise ValueError(f"Unsupported image extension for validation: {suffix}")
    verify_raster_decode(path, info.fmt)
    return info


def verify_raster_decode(path: Path, fmt: str) -> None:
    if fmt == "svg":
        return
    try:
        from PIL import Image, UnidentifiedImageError
    except Exception as exc:
        raise ValueError(
            "Pillow is required for raster decode verification; install it with "
            "python3 -m pip install Pillow."
        ) from exc
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except UnidentifiedImageError as exc:
        raise ValueError("image decoder could not identify file.") from exc
    except Exception as exc:
        raise ValueError(f"image decoder rejected file: {exc}") from exc


def read_png_info(data: bytes) -> ImageInfo:
    if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) < 33:
        raise ValueError("Invalid PNG signature.")

    pos = 8
    seen_ihdr = False
    seen_iend = False
    width = height = 0
    has_alpha = False
    while pos + 12 <= len(data):
        length = struct.unpack(">I", data[pos : pos + 4])[0]
        chunk_end = pos + 12 + length
        if chunk_end > len(data):
            raise ValueError("PNG chunk length exceeds file size.")
        chunk_type = data[pos + 4 : pos + 8]
        chunk_data = data[pos + 8 : pos + 8 + length]
        expected_crc = struct.unpack(">I", data[pos + 8 + length : pos + 12 + length])[0]
        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise ValueError(f"PNG {chunk_type.decode('ascii', 'replace')} CRC mismatch.")
        if not seen_ihdr and chunk_type != b"IHDR":
            raise ValueError("PNG first chunk is not IHDR.")
        if chunk_type == b"IHDR":
            if length != 13:
                raise ValueError("PNG IHDR chunk has invalid length.")
            width, height = struct.unpack(">II", chunk_data[:8])
            color_type = chunk_data[9]
            has_alpha = color_type in {4, 6}
            seen_ihdr = True
        if chunk_type == b"tRNS":
            has_alpha = True
        if chunk_type == b"IEND":
            if length != 0:
                raise ValueError("PNG IEND chunk has invalid length.")
            seen_iend = True
            break
        pos = chunk_end
    if not seen_ihdr:
        raise ValueError("PNG missing IHDR chunk.")
    if not seen_iend:
        raise ValueError("PNG missing IEND chunk.")
    return ImageInfo(width, height, has_alpha, "png")


def read_jpeg_info(data: bytes) -> ImageInfo:
    if not data.startswith(b"\xff\xd8"):
        raise ValueError("Invalid JPEG signature.")
    if not data.rstrip().endswith(b"\xff\xd9"):
        raise ValueError("JPEG missing EOI marker.")

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
        if marker == 0xDA:
            break
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
    if not data.rstrip().endswith(b";"):
        raise ValueError("GIF missing trailer.")
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


def read_ico_info(data: bytes) -> ImageInfo:
    if len(data) < 22:
        raise ValueError("ICO file is too small.")
    reserved, image_type, image_count = struct.unpack("<HHH", data[:6])
    if reserved != 0 or image_type not in {1, 2} or image_count < 1:
        raise ValueError("Invalid ICO header.")
    width = data[6] or 256
    height = data[7] or 256
    bit_count = struct.unpack("<H", data[12:14])[0]
    size = struct.unpack("<I", data[14:18])[0]
    offset = struct.unpack("<I", data[18:22])[0]
    if offset + size > len(data):
        raise ValueError("ICO image data exceeds file size.")
    image_data = data[offset : offset + size]
    if image_data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ImageInfo(width, height, read_png_info(image_data).alpha, "ico")
    alpha = bit_count >= 32 if bit_count else None
    return ImageInfo(width, height, alpha, "ico")


def read_svg_info(path: Path) -> ImageInfo:
    tree = ElementTree.parse(path)
    root = tree.getroot()
    validate_svg_safe(root)
    width = parse_svg_number(root.attrib.get("width"))
    height = parse_svg_number(root.attrib.get("height"))
    view_box = root.attrib.get("viewBox")
    if (width is None or height is None) and view_box:
        parts = [part for part in re.split(r"[\s,]+", view_box.strip()) if part]
        if len(parts) == 4:
            width = width or int(round(float(parts[2])))
            height = height or int(round(float(parts[3])))
    return ImageInfo(width, height, None, "svg")


def validate_svg_safe(root: ElementTree.Element) -> None:
    if local_name(root.tag) != "svg":
        raise ValueError("SVG root element must be <svg>.")
    for element in root.iter():
        element_name = local_name(element.tag)
        if element_name == "script":
            raise ValueError("SVG must not include script elements.")
        if element_name == "style" and EXTERNAL_REF_RE.search("".join(element.itertext())):
            raise ValueError("SVG style elements must not reference external or inline resources.")
        for raw_name, raw_value in element.attrib.items():
            name = local_name(raw_name)
            value = raw_value.strip()
            if name.startswith("on"):
                raise ValueError(f"SVG must not include event handler attributes: {name}.")
            if name in {"href", "src"} and EXTERNAL_REF_RE.search(value):
                raise ValueError(f"SVG must not reference external or inline resources: {name}.")
            if name == "style" and EXTERNAL_REF_RE.search(value):
                raise ValueError("SVG style attributes must not reference external or inline resources.")


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_svg_number(value: Optional[str]) -> Optional[int]:
    if value is None:
        return None
    match = NUMBER_RE.match(value)
    if not match:
        return None
    return int(round(float(match.group(1))))


def read_webp_info(data: bytes) -> ImageInfo:
    if len(data) < 20 or not (data.startswith(b"RIFF") and data[8:12] == b"WEBP"):
        raise ValueError("Invalid WebP signature.")
    riff_size = struct.unpack("<I", data[4:8])[0]
    if riff_size != len(data) - 8:
        raise ValueError("WebP RIFF size does not match file size.")
    pos = 12
    extended_info: Optional[ImageInfo] = None
    while pos + 8 <= len(data):
        chunk = data[pos : pos + 4]
        size = struct.unpack("<I", data[pos + 4 : pos + 8])[0]
        chunk_end = pos + 8 + size
        if chunk_end > len(data):
            raise ValueError("WebP chunk length exceeds file size.")
        body = data[pos + 8 : pos + 8 + size]
        if chunk == b"VP8X":
            if len(body) != 10:
                raise ValueError("WebP VP8X chunk has invalid length.")
            flags = body[0]
            width = 1 + int.from_bytes(body[4:7], "little")
            height = 1 + int.from_bytes(body[7:10], "little")
            extended_info = ImageInfo(width, height, bool(flags & 0x10), "webp")
        if chunk == b"VP8 " and len(body) >= 10 and body[3:6] == b"\x9d\x01\x2a":
            raw_width, raw_height = struct.unpack("<HH", body[6:10])
            if extended_info is not None:
                return extended_info
            return ImageInfo(raw_width & 0x3FFF, raw_height & 0x3FFF, False, "webp")
        if chunk == b"VP8L" and len(body) >= 5 and body[0] == 0x2F:
            b1, b2, b3, b4 = body[1], body[2], body[3], body[4]
            header_bits = int.from_bytes(body[1:5], "little")
            width = 1 + (((b2 & 0x3F) << 8) | b1)
            height = 1 + (((b4 & 0x0F) << 10) | (b3 << 2) | ((b2 & 0xC0) >> 6))
            if extended_info is not None:
                return ImageInfo(extended_info.width, extended_info.height, extended_info.alpha, "webp")
            return ImageInfo(width, height, bool(header_bits & (1 << 28)), "webp")
        pos = chunk_end + (size % 2)
    if extended_info is not None:
        raise ValueError("WebP missing VP8/VP8L image data.")
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

    full_path = theme_root / Path(*rel_path.parts)
    try:
        full_path.resolve().relative_to(theme_root.resolve())
    except ValueError:
        errors.append(f"{label}: path resolves outside the theme: {rel_path_value}")
        return
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

    max_bytes = integer_field(entry, "max_bytes", label, errors)
    if max_bytes is not None:
        actual_bytes = full_path.stat().st_size
        if actual_bytes > max_bytes:
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

    requires_alpha = boolean_field(entry, "requires_alpha", label, errors)
    if requires_alpha:
        if info.alpha is False:
            errors.append(f"{label}: requires alpha but {info.fmt} has no alpha channel.")
        elif info.alpha is None:
            errors.append(f"{label}: requires alpha but alpha could not be verified.")

    forbid_badges = boolean_field(entry, "forbid_badges", label, errors)
    forbid_baked_text = boolean_field(entry, "forbid_baked_text", label, errors)
    clean_export_verified = boolean_field(entry, "clean_export_verified", label, errors)
    if (forbid_badges or forbid_baked_text) and clean_export_verified is not True:
        errors.append(
            f"{label}: set clean_export_verified=true after checking for baked badges/text."
        )

    role = str(entry.get("role", "")).lower()
    decorative = boolean_field(entry, "decorative", label, errors) is True
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
    expected = integer_field(entry, key, label, errors)
    if expected is None:
        return
    if actual is None:
        errors.append(f"{label}: {key} is {expected}, but actual dimension is unknown.")
        return
    if expected != actual:
        errors.append(f"{label}: {key} is {expected}, actual is {actual}.")


def integer_field(
    entry: Dict[str, Any],
    key: str,
    label: str,
    errors: List[str],
) -> Optional[int]:
    if key not in entry or entry[key] is None:
        return None
    value = entry[key]
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label}: {key} must be an integer.")
        return None
    if value < 0:
        errors.append(f"{label}: {key} must be zero or greater.")
        return None
    return value


def boolean_field(
    entry: Dict[str, Any],
    key: str,
    label: str,
    errors: List[str],
) -> Optional[bool]:
    if key not in entry or entry[key] is None:
        return None
    value = entry[key]
    if not isinstance(value, bool):
        errors.append(f"{label}: {key} must be a boolean.")
        return None
    return value


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
