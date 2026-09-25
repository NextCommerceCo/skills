"""Static checks for the next-theme-design package: capture script shape,
entrypoint contract, trigger separation, and package contents."""

import json
import re
import subprocess
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parent
SCRIPT = SKILL / "scripts" / "capture-page.js"
ENTRY = SKILL / "SKILL.md"
FIXTURES = ROOT / "next-theme-dev" / "tests" / "fixtures" / "live-site"


class CaptureScriptTest(unittest.TestCase):
    def setUp(self):
        self.source = SCRIPT.read_text(encoding="utf-8")

    def test_is_one_expression_any_browser_tool_can_evaluate(self):
        self.assertTrue(self.source.startswith("(async (options) => {"))
        self.assertTrue(self.source.rstrip().endswith("})(globalThis.NEXT_THEME_DESIGN_CAPTURE);"))
        result = subprocess.run(["node", "--check", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_comments_contain_no_quote_characters(self):
        # Some tools scan evaluated code for string delimiters to decide how to
        # wrap it; an apostrophe in a comment made one return nothing.
        for number, line in enumerate(self.source.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("//"):
                self.assertNotRegex(stripped, r"['\"`]", f"quote character in comment on line {number}")

    def test_stays_read_only_and_offline(self):
        for forbidden in (r"\.click\(", r"\.submit\(", r"\bfetch\(", "XMLHttpRequest", "sendBeacon",
                          r"location\.(?:href|assign|replace)\s*=", r"\.requestSubmit\("):
            self.assertNotRegex(self.source, forbidden)

    def test_records_the_provenance_fields_the_format_requires(self):
        for field in ("viewport_width", "viewport_height", "device_pixel_ratio", "scroll_x", "scroll_y",
                      "browser", "captured_at", "html_sha256", "readiness", "keyframes", "duration_s",
                      "autoplay", "loop", "muted", "boxes"):
            self.assertIn(field, self.source)
        self.assertIn("next-theme-design/capture-output/v1", self.source)

    def test_committed_fixture_captures_came_from_this_script_version(self):
        version = re.search(r"const SCRIPT_VERSION = '([^']+)'", self.source).group(1)
        raws = sorted(FIXTURES.glob("*/package/captures/raw/*.json"))
        self.assertTrue(raws)
        for raw in raws:
            output = json.loads(raw.read_text(encoding="utf-8"))
            self.assertEqual(output["schema_version"], "next-theme-design/capture-output/v1")
            self.assertEqual(output["script_version"], version, raw.name)


class EntrypointTest(unittest.TestCase):
    def setUp(self):
        self.entry = ENTRY.read_text(encoding="utf-8")
        catalog = json.loads((ROOT / "skills.json").read_text(encoding="utf-8"))
        self.catalog = {skill["id"]: skill for skill in catalog["skills"]}

    def test_frontmatter_matches_the_catalog(self):
        self.assertRegex(self.entry, r"(?m)^name: next-theme-design$")
        version = re.search(r"(?m)^version: (\S+)$", self.entry).group(1)
        self.assertEqual(version, self.catalog["next-theme-design"]["version"])

    def test_trigger_is_distinct_from_figma_and_funnel_import(self):
        description = re.search(r"description: \|\n((?:  .*\n)+)", self.entry).group(1)
        flat = " ".join(description.split())
        self.assertIn("live website", flat)
        self.assertIn("Not for Figma sources (use next-theme-figma)", flat)
        self.assertIn("not for importing a funnel into Campaign Page Kit", flat)
        mine = set(self.catalog["next-theme-design"]["triggers"])
        figma = set(self.catalog["next-theme-figma"]["triggers"])
        self.assertFalse(mine & figma)
        self.assertFalse(any("figma" in trigger.lower() for trigger in mine))

    def test_calls_the_sibling_scaffolder_and_validator(self):
        self.assertIn("<dev-dir>/scripts/design-handoff.py new", self.entry)
        self.assertIn("<dev-dir>/scripts/design-handoff.py validate", self.entry)
        self.assertIn("design-package.py check-siblings", self.entry)
        self.assertIn("<figma-dir>/references/spark-section-roster.md", self.entry)

    def test_stops_at_the_handoff(self):
        self.assertIn("This skill stops at the handoff", self.entry)
        self.assertNotRegex(self.entry, r"(?m)^\s*ntk (?:push|watch|init|checkout)")

    def test_package_holds_only_what_the_skill_needs(self):
        files = sorted(
            path.relative_to(SKILL).as_posix()
            for path in SKILL.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.parts[len(SKILL.parts)] != "tests"
        )
        self.assertEqual(files, [
            "README.md",
            "SKILL.md",
            "references/capture.md",
            "scripts/capture-page.js",
            "scripts/design-package.py",
        ])
        for reference in ("references/capture.md", "scripts/capture-page.js", "scripts/design-package.py"):
            self.assertIn(reference.split("/")[-1], self.entry)


if __name__ == "__main__":
    unittest.main()
