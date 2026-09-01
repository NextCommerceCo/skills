#!/usr/bin/env python3
"""Assert that what the store now serves matches what was just pushed.

Run this immediately after every ``ntk push``. It answers four questions in
seconds, each of which has cost a full scoring round when it went unasked:

1. Does every target route return 200?
2. Is the served ``assets/main.css`` byte-identical to the committed file?
3. Did the expected sections actually render?
4. Is the page a plausible height, or did it collapse to a near-empty shell?

The collapse cases are the reason for (3) and (4): a settings mistake can serve
a 200 with the sections silently empty, and a pixel-scoring round is a very
expensive way to discover a 1,290px page that should be 10,000px.

    python3 readback-assert.py --expect expectations.json

Offline mode replays saved responses so the gate is testable without a store:

    python3 readback-assert.py --expect expectations.json --offline-dir captures/

Exit codes: 0 all assertions pass, 1 an assertion failed, 2 usage or input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCHEMA = "next-theme-dev/readback-expectations/v1"
REPORT_SCHEMA = "next-theme-dev/readback-report/v1"

DEFAULT_TIMEOUT = 30
DEFAULT_HEIGHT_TOLERANCE = 0.2
USER_AGENT = "next-theme-dev-readback/1"

SCRIPT_STYLE_RE = re.compile(
    r"<(script|style)\b.*?</\1\s*>", re.DOTALL | re.IGNORECASE
)
TAG_RE = re.compile(r"<[^>]*>", re.DOTALL)
COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


class Fetcher:
    """Reads URLs from the network, or from a capture directory when offline."""

    def __init__(self, offline_dir: Path | None, timeout: int) -> None:
        self.offline_dir = offline_dir
        self.timeout = timeout

    def get(self, url: str) -> tuple[int, bytes]:
        if self.offline_dir is not None:
            return self._get_offline(url)
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()
        except (urllib.error.URLError, OSError) as error:
            raise RuntimeError(f"{url}: {error}") from error

    def _get_offline(self, url: str) -> tuple[int, bytes]:
        # A capture is <sha1-of-url>.json alongside <sha1-of-url>.body, so a
        # replay directory can be produced by any capture tool.
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        meta_path = self.offline_dir / f"{key}.json"
        body_path = self.offline_dir / f"{key}.body"
        if not meta_path.is_file():
            raise RuntimeError(f"{url}: no offline capture at {meta_path}")
        # A missing body file is a broken capture, not an empty response. Left
        # to default to b"", it would surface as an asset-sha256 mismatch
        # against the hash of nothing, which reads like a real serve failure.
        # A genuinely empty response is captured as an empty file.
        if not body_path.is_file():
            raise RuntimeError(
                f"{url}: offline capture at {meta_path} has no body file "
                f"({body_path}); re-run the capture"
            )
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return int(meta["status"]), body_path.read_bytes()


def strip_markup(html: str) -> str:
    without_code = SCRIPT_STYLE_RE.sub(" ", html)
    without_comments = COMMENT_RE.sub(" ", without_code)
    return TAG_RE.sub(" ", without_comments)


def count_occurrences(html: str, needle: str) -> int:
    """Count matches, not matching lines: served markup is often one line."""
    return html.count(needle)


def estimate_height(html: str) -> int:
    """A crude proxy for 'the page has content', not a layout measurement.

    Rendered text length correlates well enough with page height to separate a
    fully rendered route from a collapsed one, which is all this gate claims.
    """
    return len(re.sub(r"\s+", " ", strip_markup(html)).strip())


def load_expectations(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != SCHEMA:
        raise ValueError(f'{path}: schema_version must be "{SCHEMA}"')
    if not isinstance(data.get("routes"), list) or not data["routes"]:
        raise ValueError(f"{path}: routes must be a non-empty array")
    return data


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check_assets(expectations: dict, fetcher: Fetcher, base: Path) -> list[dict]:
    checks: list[dict] = []
    for asset in expectations.get("assets", []) or []:
        url = asset["served_url"]
        local = base / asset["committed_path"]
        try:
            status, body = fetcher.get(url)
        except RuntimeError as error:
            checks.append({
                "check": "asset-served",
                "target": url,
                "status": "fail",
                "detail": str(error),
            })
            continue
        if status != 200:
            checks.append({
                "check": "asset-served",
                "target": url,
                "status": "fail",
                "detail": f"HTTP {status}",
            })
            continue
        if not local.is_file():
            checks.append({
                "check": "asset-sha256",
                "target": url,
                "status": "fail",
                "detail": f"committed file not found: {local}",
            })
            continue
        served = hashlib.sha256(body).hexdigest()
        committed = sha256_file(local)
        checks.append({
            "check": "asset-sha256",
            "target": url,
            "status": "pass" if served == committed else "fail",
            "expected": committed,
            "actual": served,
            "detail": (
                "served bytes match the committed file"
                if served == committed
                else f"served sha256 {served[:12]} != committed {committed[:12]} "
                     f"({asset['committed_path']}); the push did not land or the CDN is stale"
            ),
        })
    return checks


def check_routes(expectations: dict, fetcher: Fetcher, height_tolerance: float) -> list[dict]:
    checks: list[dict] = []
    for route in expectations["routes"]:
        url = route["url"]
        label = route.get("route_id", url)
        try:
            status, body = fetcher.get(url)
        except RuntimeError as error:
            checks.append({
                "check": "route-status",
                "target": label,
                "status": "fail",
                "detail": str(error),
            })
            continue

        expected_status = int(route.get("expect_status", 200))
        checks.append({
            "check": "route-status",
            "target": label,
            "status": "pass" if status == expected_status else "fail",
            "expected": expected_status,
            "actual": status,
            "detail": f"HTTP {status}",
        })
        if status != expected_status:
            continue

        html = body.decode("utf-8", errors="replace")

        for marker in route.get("section_markers", []) or []:
            needle = marker["marker"]
            expected_count = int(marker.get("expect_count", 1))
            actual = count_occurrences(html, needle)
            checks.append({
                "check": "section-marker",
                "target": f"{label}:{marker.get('section_id', needle)}",
                "status": "pass" if actual == expected_count else "fail",
                "expected": expected_count,
                "actual": actual,
                "detail": (
                    f"marker {needle!r} rendered {actual} time(s), expected {expected_count}"
                ),
            })

        expected_sections = route.get("expect_section_count")
        if expected_sections is not None:
            markers = route.get("section_markers", []) or []
            rendered = sum(
                1 for marker in markers if count_occurrences(html, marker["marker"]) > 0
            )
            checks.append({
                "check": "section-count",
                "target": label,
                "status": "pass" if rendered == int(expected_sections) else "fail",
                "expected": int(expected_sections),
                "actual": rendered,
                "detail": f"{rendered} of {len(markers)} mapped sections rendered",
            })

        expected_height = route.get("expect_content_length")
        if expected_height is not None:
            tolerance = float(route.get("height_tolerance", height_tolerance))
            actual_height = estimate_height(html)
            low = int(expected_height) * (1 - tolerance)
            high = int(expected_height) * (1 + tolerance)
            within = low <= actual_height <= high
            checks.append({
                "check": "content-length",
                "target": label,
                "status": "pass" if within else "fail",
                "expected": int(expected_height),
                "actual": actual_height,
                "detail": (
                    f"rendered text length {actual_height} outside "
                    f"{int(low)}..{int(high)} (+/-{int(tolerance * 100)}%); "
                    "a large shortfall means sections rendered empty"
                    if not within
                    else f"rendered text length {actual_height} within tolerance"
                ),
            })
    return checks


def run(expectations: dict, fetcher: Fetcher, base: Path, height_tolerance: float) -> dict:
    checks = check_routes(expectations, fetcher, height_tolerance)
    checks.extend(check_assets(expectations, fetcher, base))
    failures = [check for check in checks if check["status"] == "fail"]
    return {
        "schema_version": REPORT_SCHEMA,
        "theme_id": expectations.get("theme_id"),
        "checks": checks,
        "summary": {"checks": len(checks), "failures": len(failures)},
        "status": "fail" if failures else "pass",
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--expect", required=True, help="expectations JSON file")
    parser.add_argument(
        "--repo-root",
        default=".",
        help="root the committed asset paths resolve against (default: cwd)",
    )
    parser.add_argument("--offline-dir", help="replay captured responses instead of fetching")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT)
    parser.add_argument("--height-tolerance", type=float, default=DEFAULT_HEIGHT_TOLERANCE)
    parser.add_argument("--report", help="write the JSON report to this path")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        expectations = load_expectations(Path(args.expect))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"readback-assert: {error}", file=sys.stderr)
        return 2
    if not 0 <= args.height_tolerance < 1:
        print("readback-assert: --height-tolerance must be in [0, 1)", file=sys.stderr)
        return 2

    offline_dir = Path(args.offline_dir) if args.offline_dir else None
    if offline_dir is not None and not offline_dir.is_dir():
        print(f"readback-assert: {offline_dir}: not a directory", file=sys.stderr)
        return 2

    report = run(
        expectations,
        Fetcher(offline_dir, args.timeout),
        Path(args.repo_root),
        args.height_tolerance,
    )

    if args.report:
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        Path(args.report).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    for check in report["checks"]:
        if check["status"] != "pass":
            print("FAIL  {check}  {target}  {detail}".format(**check))
    print(
        "[readback-assert] {status}: {checks} checks, {failures} failed".format(
            status=report["status"].upper(),
            checks=report["summary"]["checks"],
            failures=report["summary"]["failures"],
        )
    )
    return 1 if report["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
