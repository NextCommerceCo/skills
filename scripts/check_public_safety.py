#!/usr/bin/env python3
"""Scan tracked public-catalog files for private references, secrets, and PII."""

from __future__ import annotations

import argparse
import math
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


TEXT_EXTENSIONS = {".md", ".py", ".js", ".json", ".sh", ".yaml", ".yml"}

# Public NextCommerceCo repositories intentionally referenced by this catalog.
ALLOWED_NEXTCOMMERCE_REPOS = {
    "skills",  # This public catalog and its installation URL.
    "campaign-cart-starter-templates",  # Public campaign template source.
    "campaigns-os",  # Public Campaigns OS package linked by catalog skills.
}

# Known customer or internal evidence names. Extend this set when a new token is
# discovered; keep entries lowercase because matching is case-insensitive.
BLOCKLIST = {"bareearth", "ecommops", "dlx", "wintergloves", "oscar-prime"}  # public-safety: allow customer-token private-repo

# Exact long values known to be harmless may be added here with a reason. Prefer
# fixing or suppressing the source line instead of broadly exempting patterns.
ALLOWED_HIGH_ENTROPY_TOKENS: set[str] = set()

SUPPRESSION_RE = re.compile(
    r"public-safety:\s*allow\s+([a-z0-9_-]+(?:[\s,]+[a-z0-9_-]+)*)",
    re.IGNORECASE,
)
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
BEARER_RE = re.compile(r"\bBearer\s+([^\s\"'`]+)", re.IGNORECASE)
CREDENTIAL_RES = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
    re.compile(r"\bxoxb-[A-Za-z0-9-]{16,}"),
    re.compile(r"\bAKIA[A-Z0-9]{16}\b"),
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


def suppressed_rules(line: str) -> set[str]:
    match = SUPPRESSION_RE.search(line)
    if not match:
        return set()
    return {part.lower() for part in re.split(r"[\s,]+", match.group(1)) if part}


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
    if "/" in value and "-" in value:
        return False
    classes = sum(
        bool(re.search(pattern, value))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[_+/=-]")
    )
    # Human-readable paths and slugs can be long and diverse, but opaque tokens
    # virtually always mix digits with letters (or are hex, handled above).
    return bool(re.search(r"\d", value)) and classes >= 3 and shannon_entropy(value) >= 3.5


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
    in_fence = False

    for line_number, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        fence_line = stripped.startswith("```") or stripped.startswith("~~~")
        rules_allowed = suppressed_rules(line)

        def add(rule_id: str) -> None:
            if rule_id not in rules_allowed and "all" not in rules_allowed:
                hits.append(Hit(path, line_number, rule_id, line))

        repo_hit = False
        for match in NEXTCOMMERCE_REPO_RE.finditer(line):
            repo_name = match.group(1).lower().rstrip(".").removesuffix(".git")
            if repo_name not in ALLOWED_NEXTCOMMERCE_REPOS:
                repo_hit = True
        if repo_hit or any(pattern.search(line) for pattern in PRIVATE_REFERENCE_RES):
            add("private-repo")

        if any(re.search(rf"\b{re.escape(token)}\b", line, re.IGNORECASE) for token in BLOCKLIST):
            add("customer-token")

        credential_hit = any(pattern.search(line) for pattern in CREDENTIAL_RES)
        for match in BEARER_RE.finditer(line):
            if looks_like_bearer_token(match.group(1)):
                credential_hit = True
        if credential_hit:
            add("credential")

        if not in_fence and not fence_line:
            if any(looks_high_entropy(match.group(0)) for match in HIGH_ENTROPY_RE.finditer(line)):
                add("high-entropy")

            email_hit = False
            for match in EMAIL_RE.finditer(line):
                address = match.group(1)
                domain = address.rsplit("@", 1)[1].lower()
                if domain not in EXAMPLE_EMAIL_DOMAINS and not is_placeholder(address):
                    email_hit = True
            if email_hit:
                add("email-pii")

            if any(looks_like_phone(match.group(0)) for match in PHONE_RE.finditer(line)):
                add("phone-pii")

        if fence_line:
            in_fence = not in_fence

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
        if relative.suffix.lower() in TEXT_EXTENSIONS and ".git" not in relative.parts:
            paths.append(relative)
    return sorted(paths, key=lambda path: path.as_posix())


def scan_repository(root: Path) -> list[Hit]:
    hits: list[Hit] = []
    for relative in tracked_text_files(root):
        full_path = root / relative
        try:
            text = full_path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        hits.extend(scan_text(text, relative.as_posix()))
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
