"""Fixture tests for the copy manifest lint.

Two cases carry the gate: copy that is not in the manifest must FAIL, and copy
covered by a recorded allowed deviation must PASS. Everything else here guards
the normalization that decides whether two strings are "the same copy".
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "next-theme-figma" / "scripts" / "copy-lint.py"

MANIFEST = {
    "schema_version": "next-theme-figma/copy/v1",
    "project": "example-store",
    "source": "figma-text-layers",
    "extracted_at": "2026-01-01T00:00:00.000Z",
    "strings": [
        {
            "copy_id": "hero_heading",
            "section_id": "hero-1",
            "node_id": "20:2",
            "role": "heading",
            "text": "Everything your morning needs",
        },
        {
            "copy_id": "hero_body",
            "section_id": "hero-1",
            "node_id": "20:3",
            "role": "body",
            "text": "Ground fresh, shipped the same week, and roasted in small batches.",
        },
        {
            "copy_id": "hero_cta",
            "section_id": "hero-1",
            "node_id": "20:4",
            "role": "cta",
            "text": "Shop the collection",
        },
        {
            "copy_id": "hero_note",
            "section_id": "hero-1",
            "node_id": "20:5",
            "role": "body",
            # Smart apostrophes, an em dash, and curly quotes, as the designer
            # typed them in Figma.
            "text": "It’s the roaster’s pick — “small batch, every week”",
        },
    ],
    "allowed_deviations": [],
}

STRAIGHT_NOTE = "It's the roaster's pick - \"small batch, every week\""

CLEAN_TEMPLATE = """<section data-geo-section="hero-1">
  <h1>{{ settings.hero_heading|default:'Everything your morning needs' }}</h1>
  <p>Ground fresh, shipped the same week, and roasted in small batches.</p>
  <a href="/collections/all/">Shop the collection</a>
</section>
"""

INVENTED_TEMPLATE = CLEAN_TEMPLATE.replace(
    "</section>",
    '  <p class="fine-print">Free returns within thirty days, no questions asked.</p>\n</section>',
)


class CopyLintTest(unittest.TestCase):
    def run_lint(self, manifest, templates, *args):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "copy.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            template_dir = root / "partials"
            template_dir.mkdir()
            for name, body in templates.items():
                (template_dir / name).write_text(body, encoding="utf-8")
            return subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--manifest", str(manifest_path),
                    "--templates", str(template_dir),
                    *args,
                ],
                text=True,
                capture_output=True,
            )

    def test_manifest_copy_passes(self):
        result = self.run_lint(MANIFEST, {"hero.html": CLEAN_TEMPLATE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_invented_copy_fails(self):
        result = self.run_lint(MANIFEST, {"hero.html": INVENTED_TEMPLATE})
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT IN MANIFEST", result.stdout)
        self.assertIn("Free returns within thirty days", result.stdout)

    def test_permitted_deviation_passes(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["allowed_deviations"] = [
            {
                "deviation_id": "returns-legal-line",
                "text": "Free returns within thirty days, no questions asked.",
                "reason": "Merchant legal copy added after the Figma export.",
                "approved_by": "operator",
            }
        ]
        result = self.run_lint(manifest, {"hero.html": INVENTED_TEMPLATE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("1 allowed deviation(s) used", result.stdout)

    def test_pattern_deviation_passes(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["allowed_deviations"] = [
            {
                "deviation_id": "returns-legal-family",
                "pattern": r"^Free returns within .+ days",
                "reason": "The return window is a store setting, not design copy.",
                "approved_by": "operator",
            }
        ]
        result = self.run_lint(manifest, {"hero.html": INVENTED_TEMPLATE})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_smart_punctuation_is_not_copy_drift(self):
        # The manifest holds the designer's curly quotes, apostrophes, and em
        # dash; the template renders straight ASCII. Only normalize() makes
        # these the same string, so this fails if that folding regresses.
        template = CLEAN_TEMPLATE.replace(
            "</section>",
            '  <p class="note">%s</p>\n</section>' % STRAIGHT_NOTE,
        )
        result = self.run_lint(MANIFEST, {"hero.html": template})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_smart_punctuation_folding_is_load_bearing(self):
        # Negative control for the test above: with the note absent from the
        # manifest, the same template is drift. Proves the PASS came from
        # normalization, not from the string being ignored as a candidate.
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["strings"] = [
            entry for entry in manifest["strings"] if entry["copy_id"] != "hero_note"
        ]
        template = CLEAN_TEMPLATE.replace(
            "</section>",
            '  <p class="note">%s</p>\n</section>' % STRAIGHT_NOTE,
        )
        result = self.run_lint(manifest, {"hero.html": template})
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT IN MANIFEST", result.stdout)

    def test_whitespace_runs_are_not_copy_drift(self):
        template = CLEAN_TEMPLATE.replace(
            '<a href="/collections/all/">Shop the collection</a>',
            '<a href="/collections/all/">Shop  the\n  collection</a>',
        )
        result = self.run_lint(MANIFEST, {"hero.html": template})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_html_entities_are_not_copy_drift(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["strings"].append(
            {
                "copy_id": "value_props",
                "section_id": "hero-1",
                "node_id": "20:5",
                "role": "body",
                "text": "Roasted & shipped in small batches",
            }
        )
        template = CLEAN_TEMPLATE.replace(
            "</section>",
            "  <p>Roasted &amp; shipped in small batches</p>\n</section>",
        )
        result = self.run_lint(manifest, {"hero.html": template})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_template_expressions_and_urls_are_not_copy(self):
        template = """<section>
  {% comment %} Reviewers should never see this string in a finding {% endcomment %}
  <a href="https://example.test/collections/all/">Shop the collection</a>
  <img src="assets/img/hero-background.webp" alt="Everything your morning needs">
  <script>const label = "an invented analytics label";</script>
</section>
"""
        result = self.run_lint(MANIFEST, {"hero.html": template})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_alt_text_drift_is_caught(self):
        template = """<section>
  <img src="assets/img/hero.webp" alt="A cheerful invented alt description">
</section>
"""
        result = self.run_lint(MANIFEST, {"hero.html": template})
        self.assertEqual(result.returncode, 1)
        self.assertIn("A cheerful invented alt description", result.stdout)

    def test_require_coverage_reports_unbuilt_manifest_strings(self):
        template = """<section>
  <h1>Everything your morning needs</h1>
</section>
"""
        result = self.run_lint(MANIFEST, {"hero.html": template}, "--require-coverage")
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT BUILT", result.stdout)
        self.assertIn("hero_cta", result.stdout)

    def test_report_file_is_machine_readable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "copy.json"
            manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
            template_dir = root / "partials"
            template_dir.mkdir()
            (template_dir / "hero.html").write_text(INVENTED_TEMPLATE, encoding="utf-8")
            report_path = root / "report.json"
            result = subprocess.run(
                [
                    sys.executable, str(SCRIPT),
                    "--manifest", str(manifest_path),
                    "--templates", str(template_dir),
                    "--report", str(report_path),
                ],
                text=True,
                capture_output=True,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(len(report["violations"]), 1)
        self.assertEqual(report["violations"][0]["line"], 5)

    def test_wrong_schema_version_is_an_input_error(self):
        manifest = json.loads(json.dumps(MANIFEST))
        manifest["schema_version"] = "next-theme-figma/copy/v0"
        result = self.run_lint(manifest, {"hero.html": CLEAN_TEMPLATE})
        self.assertEqual(result.returncode, 2)
        self.assertIn("schema_version", result.stderr)


if __name__ == "__main__":
    unittest.main()
