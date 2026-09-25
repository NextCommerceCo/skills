"""The existing gates accept a live-site package without new modes.

copy-lint.py (in next-theme-figma) and assert-geometry.mjs read the live-site
schema IDs with unchanged logic: copy decisions are enforced because
strings[].text holds only permitted build text, and geometry uses the same
coordinate rules as a Figma manifest.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parent
COPY_LINT = ROOT / "next-theme-figma" / "scripts" / "copy-lint.py"
ASSERT_GEOMETRY = SKILL / "scripts" / "assert-geometry.mjs"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "live-site"
READY = FIXTURES / "northlight" / "package"
BLOCKED = FIXTURES / "tidewell" / "package"


def strings(package):
    manifest = json.loads((package / "copy.json").read_text(encoding="utf-8"))
    return {entry["copy_id"]: entry for entry in manifest["strings"]}


class LiveSiteCopyLintTest(unittest.TestCase):
    def lint(self, package, template):
        with tempfile.TemporaryDirectory() as temp:
            partial = Path(temp) / "section.html"
            partial.write_text(template, encoding="utf-8")
            return subprocess.run(
                [sys.executable, str(COPY_LINT), "--package", str(package), "--templates", str(partial)],
                text=True, capture_output=True,
            )

    def test_fixture_copy_manifests_use_the_live_site_schema(self):
        for package in (READY, BLOCKED):
            manifest = json.loads((package / "copy.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "next-theme-dev/handoff-copy/v1")

    def test_build_with_approved_replacement_passes(self):
        bar = strings(READY)["bar_text"]
        self.assertEqual(bar["decision"], "replace")
        result = self.lint(READY, f'<div class="bar"><p>{bar["text"]}</p></div>\n'
                                  f'<h1>{strings(READY)["hero_title"]["text"]}</h1>\n')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_build_with_replaced_source_string_fails(self):
        bar = strings(READY)["bar_text"]
        result = self.lint(READY, f'<div class="bar"><p>{bar["source_text"]}</p></div>\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT IN MANIFEST", result.stdout)

    def test_build_with_omitted_source_string_fails(self):
        save = strings(READY)["offer_save_3"]
        self.assertEqual((save["decision"], save["text"]), ("omit", ""))
        result = self.lint(READY, f'<span class="save">{save["source_text"]}</span>\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn(save["source_text"], result.stdout)

    def test_build_with_unresolved_source_string_fails(self):
        strip = strings(BLOCKED)["strip_text"]
        self.assertEqual((strip["decision"], strip["text"]), ("unresolved", ""))
        result = self.lint(BLOCKED, f'<div id="strip"><span>{strip["source_text"]}</span></div>\n')
        self.assertEqual(result.returncode, 1)
        self.assertIn("NOT IN MANIFEST", result.stdout)

    def test_coverage_ignores_strings_with_no_permitted_text(self):
        reused = [entry["text"] for entry in strings(READY).values() if entry["text"]]
        template = "\n".join(f"<p>{text}</p>" for text in reused)
        with tempfile.TemporaryDirectory() as temp:
            partial = Path(temp) / "page.html"
            partial.write_text(template, encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(COPY_LINT), "--package", str(READY), "--templates", str(partial),
                 "--require-coverage", "--min-length", "1"],
                text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class LiveSiteGeometryTest(unittest.TestCase):
    def compare(self, viewport, measured_boxes, capture):
        with tempfile.TemporaryDirectory() as temp:
            boxes = Path(temp) / "boxes.json"
            boxes.write_text(json.dumps({
                "schema_version": "next-theme-dev/geometry-boxes/v1",
                "route_id": "home",
                "viewport": viewport,
                "viewport_width": capture["viewport_width"],
                "url": capture["url"],
                "boxes": {key: {"found": True, "count": 1, **box} for key, box in measured_boxes.items()},
            }), encoding="utf-8")
            return subprocess.run(
                ["node", str(ASSERT_GEOMETRY), "compare", "--manifest", str(READY / "geometry.json"),
                 "--route", "home", "--viewport", viewport, "--boxes", str(boxes)],
                text=True, capture_output=True,
            )

    def captures(self):
        records = json.loads((READY / "captures.json").read_text(encoding="utf-8"))["captures"]
        return {record["capture_id"]: record for record in records}

    def test_live_site_geometry_passes_against_its_own_capture(self):
        for viewport in ("desktop", "tablet", "mobile"):
            with self.subTest(viewport=viewport):
                capture = self.captures()[f"home-{viewport}-static"]
                result = self.compare(viewport, capture["boxes"], capture)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f"PASS home/{viewport}", result.stdout)

    def test_section_drift_below_the_fold_is_not_an_element_failure(self):
        capture = self.captures()["home-desktop-static"]
        moved = {key: dict(box) for key, box in capture["boxes"].items()}
        for key in ("specs", "specs::title", "specs::list"):
            moved[key]["y"] += 300
        result = self.compare("desktop", moved, capture)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_wrong_indent_in_a_live_site_build_fails_by_name(self):
        capture = self.captures()["home-desktop-static"]
        moved = {key: dict(box) for key, box in capture["boxes"].items()}
        moved["specs::title"]["x"] += 40
        result = self.compare("desktop", moved, capture)
        self.assertEqual(result.returncode, 1)
        self.assertIn("specs.title", result.stdout)
        self.assertIn("+40px", result.stdout)

    def test_selectors_lists_the_build_hooks_not_the_source_selectors(self):
        result = subprocess.run(
            ["node", str(ASSERT_GEOMETRY), "selectors", "--manifest", str(READY / "geometry.json"),
             "--route", "home", "--viewport", "desktop"],
            text=True, capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('hero::title\t[data-geo="hero-title"]', result.stdout)
        self.assertNotIn(".nl-hero__title", result.stdout)


if __name__ == "__main__":
    unittest.main()
