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
ZERO_WIDTH_TRANSLATION = str.maketrans("", "", "\u200b\u200c\u200d\ufeff")
NEXTCOMMERCE_REPO_RE = re.compile(
    r"(?:https?://github\.com/)?NextCommerceCo/([A-Za-z0-9_.-]+)", re.IGNORECASE
)
PRIVATE_REFERENCE_RES = (
    re.compile(r"\boscar-prime\b", re.IGNORECASE),
    re.compile(r"\bnext-mind\b", re.IGNORECASE),
    re.compile(r"\bnext-campaigns-ops\b", re.IGNORECASE),
    re.compile(r"(?:^|[\s`'\"(])executive/", re.IGNORECASE),
    re.compile(r"\bSellmore-Co/", re.IGNORECASE),
)
CUSTOMER_TOKEN_RES = (
    re.compile(r"\bbare[ _-]?earth\b", re.IGNORECASE),
    re.compile(r"\becomm[ _-]?ops\b", re.IGNORECASE),
    re.compile(r"\bwinter[ _-]?gloves\b", re.IGNORECASE),
    re.compile(r"\boscar[ _-]?prime\b", re.IGNORECASE),
    re.compile(r"\bdlx\b", re.IGNORECASE),
)
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
    if "/" in value and "-" in value:
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


def scan_text(text: str, path: str = "<text>") -> list[Hit]:
    hits: list[Hit] = []

    for line_number, line in enumerate(text.splitlines(), 1):
        normalized = html.unescape(urllib.parse.unquote(line)).translate(ZERO_WIDTH_TRANSLATION)
        variants = tuple(dict.fromkeys((line, normalized)))
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

    return hits


def tracked_text_files(root: Path) -> list[Path]:
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
        if ".git" in relative.parts or relative.suffix.lower() in BINARY_MEDIA_EXTENSIONS:
            continue
        paths.append(relative)
    return sorted(paths, key=lambda path: path.as_posix())


def scan_repository(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for relative in tracked_text_files(root):
        full_path = root / relative
        display_path = relative.as_posix()
        try:
            mode = full_path.lstat().st_mode
            if stat.S_ISLNK(mode):
                hits.extend(scan_text(os.readlink(full_path), display_path))
                continue
            data = full_path.read_bytes()
        except OSError as error:
            hits.append(Hit(display_path, 0, "unreadable", f"unable to read tracked file: {type(error).__name__}"))
            continue

        if b"\0" in data[:BINARY_SNIFF_BYTES]:
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as error:
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
