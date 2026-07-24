#!/usr/bin/env python3
"""Scan tracked public-catalog files for private references, secrets, and PII."""

from __future__ import annotations

import argparse
import html
import math
import os
import re
import stat
import subprocess
import sys
import unicodedata
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


BINARY_MEDIA_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".zip", ".gz", ".pdf"}
)
BINARY_SNIFF_BYTES = 8192

# Public NextCommerceCo repositories intentionally referenced by this catalog.
ALLOWED_NEXTCOMMERCE_REPOS = {
    "skills",  # This public catalog and its installation URL.
    "campaign-cart-starter-templates",  # Public campaign template source.
    "campaigns-os",  # Public Campaigns OS package linked by catalog skills.
    "spark",  # Public Spark starter theme cloned by the greenfield path.
}

# Rule IDs that may be named in a per-line suppression. Invalid names emit the
# separate, unsuppressible unknown-suppression rule.
SUPPRESSIBLE_RULE_IDS = frozenset(
    {"private-repo", "customer-token", "credential", "high-entropy", "email-pii", "phone-pii"}
)

# Exact long values known to be harmless may be added here with a reason. Prefer
# fixing or suppressing the source line instead of broadly exempting patterns.
ALLOWED_HIGH_ENTROPY_TOKENS: set[str] = set()

SUPPRESSION_RE = re.compile(
    r"public-safety:\s*allow\b(?:\s+([a-z0-9_-]+(?:\s+[a-z0-9_-]+)*))?",
    re.IGNORECASE,
)

# Invisible/zero-width/formatting characters used to break boundaries and slip
# tokens past the rules. Deleted before matching. Covers the classic zero-width
# set plus the combining grapheme joiner, invisible math operators, bidirectional
# controls, variation selectors (both blocks), and the deprecated tag characters.
_INVISIBLE_CODEPOINTS: list[int] = [
    0x200B, 0x200C, 0x200D, 0xFEFF,  # ZWSP, ZWNJ, ZWJ, BOM
    0x00AD, 0x2060, 0x180E,          # SOFT HYPHEN, WORD JOINER, MONGOLIAN VOWEL SEP
    0x034F,                          # COMBINING GRAPHEME JOINER
    0x061C,                          # ARABIC LETTER MARK
    0x200E, 0x200F,                  # LEFT-TO-RIGHT / RIGHT-TO-LEFT MARK
    0x2061, 0x2062, 0x2063, 0x2064,  # FUNCTION APPLICATION, INVISIBLE TIMES/SEPARATOR/PLUS
]
_INVISIBLE_CODEPOINTS += list(range(0x202A, 0x202F))     # bidi embeddings/overrides (202A-202E)
_INVISIBLE_CODEPOINTS += list(range(0x2066, 0x206A))     # bidi isolates (2066-2069)
_INVISIBLE_CODEPOINTS += list(range(0xFE00, 0xFE10))     # variation selectors 1-16
_INVISIBLE_CODEPOINTS += list(range(0xE0100, 0xE01F0))   # variation selectors supplement
_INVISIBLE_CODEPOINTS += list(range(0xE0000, 0xE0080))   # deprecated tag characters
ZERO_WIDTH_TRANSLATION = {codepoint: None for codepoint in _INVISIBLE_CODEPOINTS}

# Homoglyph/confusable folding. NFKC already folds fullwidth and many compat
# forms to ASCII; this table covers the common Greek/Cyrillic lookalikes NFKC
# leaves alone. Keys are code points, values the ASCII letter they imitate; the
# rules are case-insensitive so every value is lowercase.
_CONFUSABLE_GROUPS: dict[str, tuple[int, ...]] = {
    "a": (0x0430, 0x0410, 0x03B1, 0x0391),
    "c": (0x0441, 0x0421, 0x03F2),
    "d": (0x0501,),
    "e": (0x0435, 0x0415, 0x03B5, 0x0395),
    "h": (0x04BB, 0x0397),
    "i": (0x0456, 0x0406, 0x03B9, 0x0399),
    "j": (0x0458, 0x0408),
    "k": (0x043A, 0x041A, 0x03BA, 0x039A),
    "m": (0x043C, 0x041C, 0x039C),
    "o": (0x043E, 0x041E, 0x03BF, 0x039F),
    "p": (0x0440, 0x0420, 0x03C1, 0x03A1),
    "s": (0x0455, 0x0405),
    "t": (0x0422, 0x03C4, 0x03A4),
    "v": (0x03BD,),
    "w": (0x03C9,),
    "x": (0x0445, 0x0425, 0x03C7, 0x03A7),
    "y": (0x0443, 0x0423),
}
CONFUSABLE_TRANSLATION = {
    codepoint: ascii_letter
    for ascii_letter, codepoints in _CONFUSABLE_GROUPS.items()
    for codepoint in codepoints
}

HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
# JS/JSON string escapes (\u{1F600}, \uXXXX, \xHH) and CSS escapes (\HH..).
JS_ESCAPE_RE = re.compile(r"\\u\{([0-9A-Fa-f]{1,6})\}|\\u([0-9A-Fa-f]{4})|\\x([0-9A-Fa-f]{2})")
CSS_ESCAPE_RE = re.compile(r"\\([0-9A-Fa-f]{1,6})[ \t]?")

# Any run of non-alphanumerics between the parts of a compound token, so a
# separator outside [ _-] (dot, slash, plus, dashes, repeats, markdown emphasis,
# collapsed HTML comments) cannot split it. Word edges use (?<![A-Za-z0-9]) /
# (?![A-Za-z0-9]) rather than \b so surrounding underscores cannot defeat them.
_GAP = r"[^A-Za-z0-9]*"


def _bounded(body: str) -> re.Pattern[str]:
    """Wrap a token body in alphanumeric edge guards. Used with the ``{_GAP}``
    interpolation so the pattern's own source line never reassembles a token
    (the literal parts stay separated by an alphanumeric run in the source)."""
    return re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])", re.IGNORECASE)


NEXTCOMMERCE_REPO_RE = re.compile(
    r"(?:https?://github\.com/)?NextCommerceCo/([A-Za-z0-9_.-]+)", re.IGNORECASE
)
PRIVATE_REFERENCE_RES = (
    _bounded(rf"oscar{_GAP}prime"),
    _bounded(rf"next{_GAP}mind"),
    _bounded(rf"next{_GAP}campaigns{_GAP}ops"),
    re.compile(r"(?:^|[\s`'\"(])executive/", re.IGNORECASE),
    _bounded(rf"sellmore{_GAP}co"),
)
CUSTOMER_TOKEN_RES = (
    _bounded(rf"bare{_GAP}earth"),
    _bounded(rf"ecomm{_GAP}ops"),
    _bounded(rf"winter{_GAP}gloves"),
    _bounded(rf"oscar{_GAP}prime"),
    # Single-word token: the [x] class keeps the literal non-contiguous in this
    # source line while still matching the bare token in scanned text.
    _bounded(r"dl[x]"),
)
# Rules re-checked across a single line break (see cross_line_hits).
CROSS_LINE_TOKEN_RES = CUSTOMER_TOKEN_RES
CROSS_LINE_PRIVATE_RES = PRIVATE_REFERENCE_RES

BEARER_RE = re.compile(r"\bBearer\s+([^\s\"'`]+)", re.IGNORECASE)
AUTHORIZATION_TOKEN_RE = re.compile(
    r"\bAuthorization\s*:\s*Token\s+([^\s\"'`]+)", re.IGNORECASE
)
API_KEY_HEADER_RE = re.compile(r"\b(?:X-)?Api-Key\s*:\s*([^\s\"'`]+)", re.IGNORECASE)
CREDENTIAL_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxoxb-[A-Za-z0-9-]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
)
JWT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"(?![A-Za-z0-9_-])"
)
HIGH_ENTROPY_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9_])")
EMAIL_RE = re.compile(r"(?<![\w.+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![\w.-])", re.IGNORECASE)
PHONE_RE = re.compile(r"(?<![\w])\+?\d[\d ().-]{7,}\d(?![\w])")
PLACEHOLDER_RE = re.compile(r"^(?:\{[^{}]+\}|<[^<>]+>|\$\{?[A-Z_][A-Z0-9_]*(?::[^}]*)?\}?|\.{3})$", re.IGNORECASE)
EXAMPLE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}


@dataclass(frozen=True)
class Hit:
    path: str
    line: int
    rule_id: str
    excerpt: str

    def format(self) -> str:
        return f"{self.path}:{self.line}: [{self.rule_id}] {self.excerpt.strip()}"


def suppressed_rules(line: str) -> tuple[set[str], bool]:
    allowed: set[str] = set()
    has_unknown = False
    for match in SUPPRESSION_RE.finditer(line):
        value = match.group(1)
        if not value:
            has_unknown = True
            continue
        for rule_id in value.lower().split():
            if rule_id in SUPPRESSIBLE_RULE_IDS:
                allowed.add(rule_id)
            else:
                has_unknown = True
    return allowed, has_unknown


def is_placeholder(value: str) -> bool:
    value = value.strip()
    if value == "...":
        return True
    value = value.rstrip(".,;:)")
    return bool(PLACEHOLDER_RE.fullmatch(value)) or any(
        marker in value for marker in ("{", "}", "<", ">", "${")
    )


def shannon_entropy(value: str) -> float:
    counts = Counter(value)
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def looks_high_entropy(value: str) -> bool:
    if value in ALLOWED_HIGH_ENTROPY_TOKENS or is_placeholder(value):
        return False
    if re.fullmatch(r"[a-fA-F0-9]{32,}", value):
        return len(set(value.lower())) >= 8
    entropy = shannon_entropy(value)
    if len(value) >= 32 and re.fullmatch(r"[A-Za-z0-9+/]+={1,2}", value):
        return entropy >= 3.5
    if (
        len(value) >= 40
        and re.fullmatch(r"[A-Za-z0-9_-]+", value)
        and re.search(r"[_-]", value)
    ):
        return entropy >= 4.0
    # Path-shaped values (e.g. img/example-store/hero) are not secrets, but only
    # exempt them when every path segment is itself low-entropy — otherwise an
    # opaque token that merely contains "/" and "-" would slip through.
    if "/" in value and "-" in value:
        segments = [seg for seg in value.split("/") if seg]
        if segments and not any(looks_high_entropy(seg) for seg in segments):
            return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[_+/=-]")
    )
    # Human-readable paths and slugs can be long and diverse, but opaque tokens
    # virtually always mix digits with letters (or are hex, handled above).
    return bool(re.search(r"\d", value)) and classes >= 3 and entropy >= 3.5


def is_example_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return bool(re.search(r"55501\d{2}$", digits))


def looks_like_phone(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    if re.search(r"\b\d{4}-\d{2}-\d{2}\b", value):
        return False
    if "." in value and "-" in value:
        return False
    has_phone_formatting = value.startswith("+") or bool(re.search(r"[ ().-]", value))
    return has_phone_formatting and 10 <= len(digits) <= 15 and not is_example_phone(value)


def looks_like_bearer_token(value: str) -> bool:
    value = value.strip().rstrip(".,;:)")
    return not is_placeholder(value) and len(value) >= 12


def _decode_js_escapes(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = match.group(1) or match.group(2) or match.group(3)
        codepoint = int(digits, 16)
        return chr(codepoint) if codepoint <= 0x10FFFF else match.group(0)

    return JS_ESCAPE_RE.sub(replace, text)


def _decode_css_escapes(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        codepoint = int(match.group(1), 16)
        return chr(codepoint) if codepoint <= 0x10FFFF else match.group(0)

    return CSS_ESCAPE_RE.sub(replace, text)


def decode_until_stable(line: str, max_passes: int = 12) -> str:
    """Repeatedly resolve HTML entities, percent-encoding (including ``+`` for
    spaces), JS/JSON ``\\uXXXX``/``\\xHH`` escapes, and CSS ``\\HH`` escapes until
    the string stops changing, so multiply-encoded evasions fully resolve.
    Bounded to avoid pathological loops."""
    current = line
    for _ in range(max_passes):
        decoded = html.unescape(urllib.parse.unquote_plus(current))
        decoded = _decode_js_escapes(decoded)
        decoded = _decode_css_escapes(decoded)
        if decoded == current:
            break
        current = decoded
    return current


def normalize(text: str) -> str:
    """Fold a string toward its canonical ASCII form for matching: decode nested
    encodings, drop invisible/formatting characters, strip HTML comments, apply
    Unicode NFKC, then fold Greek/Cyrillic confusables to ASCII."""
    text = decode_until_stable(text)
    text = HTML_COMMENT_RE.sub("", text)
    text = text.translate(ZERO_WIDTH_TRANSLATION)
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(CONFUSABLE_TRANSLATION)
    return text


def _variants(line: str, normalized: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((line, normalized)))


def cross_line_hits(
    path: str,
    lines: Sequence[str],
    normalized_lines: Sequence[str],
    already_seen: set[tuple[int, str]],
) -> list[Hit]:
    """Catch a customer-token or private reference split across a single line
    break. Each adjacent pair of normalized lines is rejoined with one space and
    re-scanned; only matches that actually span the join (so they were invisible
    to the per-line pass) are reported, at the first line of the pair. Per-line
    horizontal-whitespace evasions are already handled by the {_GAP} separator,
    so the single joining space is enough."""
    hits: list[Hit] = []

    def emit(index: int, rule_id: str) -> None:
        key = (index + 1, rule_id)
        if key in already_seen:
            return
        allowed: set[str] = set()
        for candidate in (lines[index], normalized_lines[index], lines[index + 1], normalized_lines[index + 1]):
            allowed |= suppressed_rules(candidate)[0]
        if rule_id in allowed:
            return
        already_seen.add(key)
        excerpt = f"{lines[index].strip()} / {lines[index + 1].strip()}"
        hits.append(Hit(path, index + 1, rule_id, excerpt))

    for index in range(len(normalized_lines) - 1):
        first = normalized_lines[index]
        second = normalized_lines[index + 1]
        if not first or not second:
            continue
        joined = f"{first} {second}"
        join = len(first)  # index of the joining space; second line starts at join + 1

        def spans_join(match: re.Match[str]) -> bool:
            return match.start() < join and match.end() > join + 1

        for pattern in CROSS_LINE_TOKEN_RES:
            if any(spans_join(match) for match in pattern.finditer(joined)):
                emit(index, "customer-token")
                break

        private = False
        for pattern in CROSS_LINE_PRIVATE_RES:
            if any(spans_join(match) for match in pattern.finditer(joined)):
                private = True
                break
        if not private:
            for match in NEXTCOMMERCE_REPO_RE.finditer(joined):
                if not spans_join(match):
                    continue
                repo_name = match.group(1).lower().rstrip(".").removesuffix(".git")
                if repo_name not in ALLOWED_NEXTCOMMERCE_REPOS:
                    private = True
                    break
        if private:
            emit(index, "private-repo")

    return hits


def scan_text(text: str, path: str = "<text>") -> list[Hit]:
    hits: list[Hit] = []
    lines = text.splitlines()
    normalized_lines = [normalize(line) for line in lines]

    for line_number, (line, normalized) in enumerate(zip(lines, normalized_lines), 1):
        variants = _variants(line, normalized)
        rules_allowed: set[str] = set()
        has_unknown_suppression = False
        for variant in variants:
            variant_rules, variant_has_unknown = suppressed_rules(variant)
            rules_allowed.update(variant_rules)
            has_unknown_suppression = has_unknown_suppression or variant_has_unknown

        line_hits: set[str] = set()

        def add(rule_id: str) -> None:
            if rule_id not in rules_allowed and rule_id not in line_hits:
                hits.append(Hit(path, line_number, rule_id, line))
                line_hits.add(rule_id)

        if has_unknown_suppression:
            add("unknown-suppression")

        repo_hit = False
        for variant in variants:
            for match in NEXTCOMMERCE_REPO_RE.finditer(variant):
                repo_name = match.group(1).lower().rstrip(".").removesuffix(".git")
                if repo_name not in ALLOWED_NEXTCOMMERCE_REPOS:
                    repo_hit = True
        if repo_hit or any(pattern.search(variant) for variant in variants for pattern in PRIVATE_REFERENCE_RES):
            add("private-repo")

        if any(pattern.search(variant) for variant in variants for pattern in CUSTOMER_TOKEN_RES):
            add("customer-token")

        credential_hit = any(pattern.search(variant) for variant in variants for pattern in CREDENTIAL_RES)
        credential_hit = credential_hit or any(JWT_RE.search(variant) for variant in variants)
        for variant in variants:
            for pattern in (BEARER_RE, AUTHORIZATION_TOKEN_RE, API_KEY_HEADER_RE):
                for match in pattern.finditer(variant):
                    if looks_like_bearer_token(match.group(1)):
                        credential_hit = True
        if credential_hit:
            add("credential")

        if any(
            looks_high_entropy(match.group(0))
            for variant in variants
            for match in HIGH_ENTROPY_RE.finditer(variant)
        ):
            add("high-entropy")

        email_hit = False
        for variant in variants:
            for match in EMAIL_RE.finditer(variant):
                address = match.group(1)
                domain = address.rsplit("@", 1)[1].lower()
                if domain not in EXAMPLE_EMAIL_DOMAINS and not is_placeholder(address):
                    email_hit = True
        if email_hit:
            add("email-pii")

        if any(
            looks_like_phone(match.group(0))
            for variant in variants
            for match in PHONE_RE.finditer(variant)
        ):
            add("phone-pii")

    seen = {(hit.line, hit.rule_id) for hit in hits}
    hits.extend(cross_line_hits(path, lines, normalized_lines, seen))
    return hits


def tracked_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    paths = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(raw_path.decode("utf-8", errors="surrogateescape"))
        if ".git" in relative.parts:
            continue
        paths.append(relative)
    return sorted(paths, key=lambda path: path.as_posix())


def scan_repository(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for relative in tracked_files(root):
        full_path = root / relative
        display_path = relative.as_posix()
        # A tracked path is public metadata even when its contents are binary or
        # otherwise skipped, so scan the repository-relative name itself.
        hits.extend(scan_text(display_path, display_path))
        try:
            mode = full_path.lstat().st_mode
            # Handle symlinks BEFORE the media-extension skip: a symlink named
            # image.png still has a Git-tracked target string that must be scanned.
            if stat.S_ISLNK(mode):
                hits.extend(scan_text(os.readlink(full_path), display_path))
                continue
            if relative.suffix.lower() in BINARY_MEDIA_EXTENSIONS:
                continue
            data = full_path.read_bytes()
        except OSError as error:
            hits.append(Hit(display_path, 0, "unreadable", f"unable to read tracked file: {type(error).__name__}"))
            continue

        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
            # A file that is not valid UTF-8 is treated as genuinely binary: if it
            # also carries a NUL it is silently skipped like binary media,
            # otherwise it is surfaced so unexpected undecodable text is noticed.
            # A NUL alone no longer hides an otherwise-decodable text file, which
            # was a whole-file evasion.
            if b"\0" in data[:BINARY_SNIFF_BYTES]:
                continue
            hits.append(Hit(display_path, 0, "unreadable", f"unable to read tracked file: {type(error).__name__}"))
            continue
        hits.extend(scan_text(text, display_path))
    return hits


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list every hit (the default also prints every hit)")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1], help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        hits = scan_repository(args.root.resolve())
    except subprocess.CalledProcessError as error:
        message = error.stderr.decode("utf-8", errors="replace").strip()
        print(f"public-safety: unable to list tracked files: {message}", file=sys.stderr)
        return 2
    except OSError as error:
        print(f"public-safety: unable to list tracked files: {error}", file=sys.stderr)
        return 2

    for hit in hits:
        print(hit.format())
    if hits:
        print(f"public-safety: {len(hits)} hit(s)", file=sys.stderr)
        return 1
    if not args.list:
        print("public-safety: no hits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
