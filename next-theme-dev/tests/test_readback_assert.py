"""Failure-mode tests for the post-push readback gate.

Each test is one class of push failure that previously cost a scoring round or
a diagnostic push bisect: a route that stopped returning 200, a CDN still
serving the previous CSS, a section that vanished from the render, and a page
that collapsed to a fraction of its height while still returning 200.

Responses are replayed from a capture directory, so the gate is exercised
end to end without a store.
"""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "next-theme-dev" / "scripts" / "readback-assert.py"

PDP_URL = "https://example.29next.store/products/example/?preview_theme=42"
CSS_URL = "https://cdn.example.test/assets/main.css"
CSS_BODY = b".hero{color:#101010}\n"

FULL_PAGE = (
    "<html><body>"
    '<section data-section="product-main"><h1>Example product</h1>'
    + ("<p>Ground fresh, shipped the same week, and roasted in small batches.</p>" * 40)
    + "</section>"
    '<section data-section="reviews"><p>What buyers say about this roast.</p>'
    + ("<p>A steady, sweet cup that holds up to milk.</p>" * 40)
    + "</section>"
    "</body></html>"
)

COLLAPSED_PAGE = (
    "<html><body>"
    '<section data-section="product-main"></section>'
    '<section data-section="reviews"></section>'
    "</body></html>"
)


def expectations(content_length):
    return {
        "schema_version": "next-theme-dev/readback-expectations/v1",
        "theme_id": "42",
        "routes": [
            {
                "route_id": "product",
                "url": PDP_URL,
                "expect_status": 200,
                "expect_section_count": 2,
                "expect_content_length": content_length,
                "section_markers": [
                    {"section_id": "product-main", "marker": 'data-section="product-main"'},
                    {"section_id": "reviews", "marker": 'data-section="reviews"'},
                ],
            }
        ],
        "assets": [
            {"served_url": CSS_URL, "committed_path": "assets/main.css"},
        ],
    }


class ReadbackAssertTest(unittest.TestCase):
    def capture(self, directory, url, status, body):
        key = hashlib.sha1(url.encode("utf-8")).hexdigest()
        (directory / f"{key}.json").write_text(
            json.dumps({"url": url, "status": status}), encoding="utf-8"
        )
        (directory / f"{key}.body").write_bytes(body)

    def run_gate(self, page_status, page_body, css_body, committed_css=CSS_BODY):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captures = root / "captures"
            captures.mkdir()
            self.capture(captures, PDP_URL, page_status, page_body.encode("utf-8"))
            self.capture(captures, CSS_URL, 200, css_body)

            assets = root / "assets"
            assets.mkdir()
            (assets / "main.css").write_bytes(committed_css)

            expect_path = root / "expect.json"
            expect_path.write_text(
                json.dumps(expectations(self.rendered_length())), encoding="utf-8"
            )
            report_path = root / "report.json"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--expect", str(expect_path),
                    "--offline-dir", str(captures),
                    "--repo-root", str(root),
                    "--report", str(report_path),
                ],
                text=True,
                capture_output=True,
            )
            report = (
                json.loads(report_path.read_text(encoding="utf-8"))
                if report_path.is_file()
                else None
            )
        return result, report

    def rendered_length(self):
        """The reference content length, measured with the script's own rule."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("readback_assert", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.estimate_height(FULL_PAGE)

    def failing(self, report):
        return [check for check in report["checks"] if check["status"] != "pass"]

    def test_healthy_push_passes(self):
        result, report = self.run_gate(200, FULL_PAGE, CSS_BODY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "pass")

    def test_route_500_fails(self):
        result, report = self.run_gate(500, "<html><body>Server error</body></html>", CSS_BODY)
        self.assertEqual(result.returncode, 1)
        statuses = [check for check in self.failing(report) if check["check"] == "route-status"]
        self.assertEqual(len(statuses), 1)
        self.assertEqual(statuses[0]["actual"], 500)

    def test_stale_served_css_fails(self):
        result, report = self.run_gate(200, FULL_PAGE, b".hero{color:#202020}\n")
        self.assertEqual(result.returncode, 1)
        sha_checks = [check for check in self.failing(report) if check["check"] == "asset-sha256"]
        self.assertEqual(len(sha_checks), 1)
        self.assertIn("did not land", sha_checks[0]["detail"])

    def test_missing_section_fails(self):
        page = FULL_PAGE.replace('data-section="reviews"', 'data-section="reviews-disabled"')
        result, report = self.run_gate(200, page, CSS_BODY)
        self.assertEqual(result.returncode, 1)
        markers = [check for check in self.failing(report) if check["check"] == "section-marker"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["actual"], 0)

    def test_collapsed_page_still_fails(self):
        result, report = self.run_gate(200, COLLAPSED_PAGE, CSS_BODY)
        self.assertEqual(result.returncode, 1)
        statuses = {check["check"] for check in self.failing(report)}
        self.assertIn("content-length", statuses)
        # The markers are still in the markup: only the height check sees this.
        self.assertNotIn("section-marker", statuses)

    def test_duplicate_section_marker_fails(self):
        page = FULL_PAGE.replace(
            '<section data-section="reviews">',
            '<section data-section="reviews"></section><section data-section="reviews">',
            1,
        )
        result, report = self.run_gate(200, page, CSS_BODY)
        self.assertEqual(result.returncode, 1)
        markers = [check for check in self.failing(report) if check["check"] == "section-marker"]
        self.assertEqual(markers[0]["actual"], 2)

    def test_missing_capture_is_reported_as_a_failure(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            captures = root / "captures"
            captures.mkdir()
            expect_path = root / "expect.json"
            expect_path.write_text(json.dumps(expectations(1000)), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--expect", str(expect_path),
                    "--offline-dir", str(captures),
                    "--repo-root", str(root),
                ],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 1)
        self.assertIn("no offline capture", result.stdout)

    def test_wrong_schema_version_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as temp:
            expect_path = Path(temp) / "expect.json"
            body = expectations(1000)
            body["schema_version"] = "next-theme-dev/readback-expectations/v0"
            expect_path.write_text(json.dumps(body), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--expect", str(expect_path)],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn("schema_version", result.stderr)


if __name__ == "__main__":
    unittest.main()
