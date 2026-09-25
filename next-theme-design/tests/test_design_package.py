"""Tests for design-package.py, the authoring helper.

The strongest check rebuilds next-theme-dev's committed ready fixture from its
own raw capture outputs and screenshots, and requires the helper to reproduce
the committed geometry exactly: page-relative section boxes and
section-relative element boxes.
"""

import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
ROOT = SKILL.parent
HELPER = SKILL / "scripts" / "design-package.py"
SCAFFOLD = ROOT / "next-theme-dev" / "scripts" / "design-handoff.py"
FIXTURE = ROOT / "next-theme-dev" / "tests" / "fixtures" / "live-site" / "northlight" / "package"


def run(script, *args):
    return subprocess.run([sys.executable, str(script), *map(str, args)], text=True, capture_output=True)


def png(width, height=10):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + b"\x00" * (width * 3) for _ in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


class HelperTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.package = self.root / "package"
        created = run(SCAFFOLD, "new", "--out", self.package, "--project", "rebuild",
                      "--source-url", "https://northlight.example.test/", "--theme-family", "spark")
        self.assertEqual(created.returncode, 0, created.stderr)
        for name in ("routes.json", "sections.json"):
            shutil.copyfile(FIXTURE / name, self.package / name)

    def load(self, name):
        return json.loads((self.package / name).read_text(encoding="utf-8"))

    def record(self, capture_id, kind="full-page", screenshot=True):
        args = ["record", "--package", self.package, "--raw", FIXTURE / "captures" / "raw" / f"{capture_id}.json",
                "--tool", "test"]
        if screenshot:
            args += ["--screenshot", FIXTURE / "captures" / f"{capture_id}.png", "--screenshot-kind", kind]
        return run(HELPER, *args)

    def test_rebuilds_the_committed_fixture_geometry(self):
        for viewport in ("desktop", "tablet", "mobile"):
            result = self.record(f"home-{viewport}-static")
            self.assertEqual(result.returncode, 0, result.stderr)
            result = run(HELPER, "geometry", "--package", self.package, "--capture", f"home-{viewport}-static")
            self.assertEqual(result.returncode, 0, result.stderr)
        built = self.load("geometry.json")["routes"]
        committed = json.loads((FIXTURE / "geometry.json").read_text(encoding="utf-8"))["routes"]
        self.assertEqual(built, committed)
        record = next(entry for entry in self.load("captures.json")["captures"]
                      if entry["capture_id"] == "home-mobile-static")
        self.assertEqual((record["viewport_width"], record["device_pixel_ratio"]), (390, 2))
        self.assertTrue((self.package / "captures" / "home-mobile-static.png").is_file())
        self.assertTrue((self.package / "captures" / "raw" / "home-mobile-static.json").is_file())

    def test_geometry_keeps_author_hooks_on_rerun(self):
        self.record("home-desktop-static")
        run(HELPER, "geometry", "--package", self.package, "--capture", "home-desktop-static")
        geometry = self.load("geometry.json")
        hero = next(entry for entry in geometry["routes"][0]["viewports"]["desktop"]["sections"]
                    if entry["section_id"] == "hero")
        hero["selector"] = ".custom-hero"
        hero["elements"][0]["assert"] = ["position-x", "position-y", "height"]
        (self.package / "geometry.json").write_text(json.dumps(geometry), encoding="utf-8")
        run(HELPER, "geometry", "--package", self.package, "--capture", "home-desktop-static")
        hero = next(entry for entry in self.load("geometry.json")["routes"][0]["viewports"]["desktop"]["sections"]
                    if entry["section_id"] == "hero")
        self.assertEqual(hero["selector"], ".custom-hero")
        self.assertEqual(hero["elements"][0]["assert"], ["position-x", "position-y", "height"])

    def test_geometry_refuses_a_motion_capture(self):
        self.record("home-desktop-motion", kind="viewport")
        result = run(HELPER, "geometry", "--package", self.package, "--capture", "home-desktop-motion")
        self.assertEqual(result.returncode, 1)
        self.assertIn("geometry comes from static captures", result.stderr)

    def test_draft_copy_records_captured_text_as_unresolved(self):
        self.record("home-desktop-static")
        result = run(HELPER, "draft-copy", "--package", self.package, "--capture", "home-desktop-static")
        self.assertEqual(result.returncode, 0, result.stderr)
        strings = {entry["copy_id"]: entry for entry in self.load("copy.json")["strings"]}
        title = strings["hero_title"]
        self.assertEqual(title["source_text"], "Light that follows your workday")
        self.assertEqual((title["decision"], title["text"]), ("unresolved", ""))
        self.assertEqual(title["capture_key"], "hero::title")
        self.assertEqual(strings["hero_product"]["source_text"], "Northlight desk lamp in graphite")

    def variant(self, change):
        raw = json.loads((FIXTURE / "captures" / "raw" / "home-desktop-static.json").read_text(encoding="utf-8"))
        change(raw)
        path = self.root / "variant.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        result = run(HELPER, "record", "--package", self.package, "--raw", path, "--tool", "test")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_list_target_copy_ids_always_take_a_suffix(self):
        def one_match_list(raw):
            single = raw["targets"]["hero::title"]
            raw["targets"]["hero::title"] = {"selector": single["selector"], "count": 1, "found": True,
                                             "matches": [{"text": single["text"], "visible": True}]}
        self.variant(one_match_list)
        result = run(HELPER, "draft-copy", "--package", self.package, "--capture", "home-desktop-static")
        self.assertEqual(result.returncode, 0, result.stderr)
        ids = {entry["copy_id"] for entry in self.load("copy.json")["strings"]}
        self.assertIn("hero_title_1", ids)
        self.assertNotIn("hero_title", ids)
        self.assertIn("hero_product", ids)

    def test_geometry_refuses_a_capture_without_document_height(self):
        self.variant(lambda raw: raw["page"].pop("document_height"))
        result = run(HELPER, "geometry", "--package", self.package, "--capture", "home-desktop-static")
        self.assertEqual(result.returncode, 1)
        self.assertIn("has no document_height", result.stderr)
        self.assertEqual(self.load("geometry.json").get("routes", []), [])

    def test_geometry_names_each_unmeasured_section_and_why(self):
        sections = self.load("sections.json")
        in_scope = [entry["section_id"] for entry in sections["sections"]]
        hidden, hidden_list, missing, excluded = in_scope[1], in_scope[2], in_scope[3], in_scope[4]
        for entry in sections["sections"]:
            if entry["section_id"] == excluded:
                entry.update({"in_scope": False, "exclusion_reason": "Not in this build."})
        (self.package / "sections.json").write_text(json.dumps(sections), encoding="utf-8")

        def drop_boxes(raw):
            for key in (hidden, hidden_list, missing, excluded):
                raw["boxes"].pop(key)
            # Visibility comes from the recorded targets, not from gap wording.
            raw["targets"][hidden]["visible"] = False
            raw["targets"][hidden_list] = {"selector": ".carousel", "count": 2, "found": True,
                                           "matches": [{"visible": False}, {"visible": False}]}
            raw["targets"].pop(missing, None)
            raw["gaps"] = []
        self.variant(drop_boxes)
        result = run(HELPER, "geometry", "--package", self.package, "--capture", "home-desktop-static")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"no geometry for {hidden} (hidden at this width)", result.stdout)
        self.assertIn(f"no geometry for {hidden_list} (hidden at this width)", result.stdout)
        self.assertIn(f"no geometry for {missing} (no matching element captured)", result.stdout)
        self.assertNotIn(f"no geometry for {excluded}", result.stdout)
        frame = self.load("geometry.json")["routes"][0]["viewports"]["desktop"]
        self.assertEqual(frame["frame_height"], 2360)
        self.assertTrue(all(entry["box"] for entry in frame["sections"]))

    def test_draft_styles_copies_computed_values(self):
        self.record("home-desktop-static")
        result = run(HELPER, "draft-styles", "--package", self.package, "--capture", "home-desktop-static",
                     "--keys", "hero::title", "--properties", "font-size,color")
        self.assertEqual(result.returncode, 0, result.stderr)
        styles = {entry["style_id"]: entry for entry in self.load("observed-styles.json")["styles"]}
        self.assertEqual(styles["hero-title-desktop-font-size"]["value"], "56px")
        self.assertEqual(styles["hero-title-desktop-color"]["category"], "color")

    def test_coverage_uses_static_screenshots_or_names_the_missing_width(self):
        self.record("home-desktop-static")
        self.record("home-mobile-static")
        result = run(HELPER, "coverage", "--package", self.package)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("no capture or gap for home/tablet", result.stdout)
        row = self.load("viewport-coverage.json")["coverage"][0]
        self.assertEqual(row["desktop"]["status"], "captured")
        self.assertEqual(row["desktop"]["source_ref"], "captures/home-desktop-static.png")
        self.assertEqual(row["tablet"]["status"], "")

    def test_record_refuses_a_scaled_screenshot(self):
        shot = self.root / "scaled.png"
        shot.write_bytes(png(1220))
        result = run(HELPER, "record", "--package", self.package,
                     "--raw", FIXTURE / "captures" / "raw" / "home-desktop-static.json", "--screenshot", shot)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is 1220px wide; viewport 1440 x device pixel ratio 1 requires 1440px", result.stderr)
        self.assertEqual(self.load("captures.json")["captures"], [])

    def test_record_refuses_a_capture_at_the_wrong_width(self):
        raw = json.loads((FIXTURE / "captures" / "raw" / "home-desktop-static.json").read_text(encoding="utf-8"))
        raw["page"]["viewport_width"] = 1366
        path = self.root / "wide.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        result = run(HELPER, "record", "--package", self.package, "--raw", path)
        self.assertEqual(result.returncode, 1)
        self.assertIn("a desktop capture is taken at 1440px", result.stderr)


class MissingSiblingTest(unittest.TestCase):
    def test_missing_siblings_stop_with_install_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            alone = Path(temp) / "next-theme-design"
            shutil.copytree(SKILL, alone, ignore=shutil.ignore_patterns("__pycache__"))
            result = run(alone / "scripts" / "design-package.py", "check-siblings")
            self.assertEqual(result.returncode, 2)
            for skill in ("next-theme-dev", "next-theme-figma"):
                self.assertIn(f"the sibling skill {skill} is not installed next to next-theme-design", result.stderr)
                self.assertIn(f"./skills.sh install <target> {skill}", result.stderr)
            self.assertIn("do not re-derive what the missing skill provides", result.stderr)
            (Path(temp) / "next-theme-dev").mkdir()
            shutil.copytree(ROOT / "next-theme-figma", Path(temp) / "next-theme-figma",
                            ignore=shutil.ignore_patterns("tests", "__pycache__"))
            result = run(alone / "scripts" / "design-package.py", "record", "--package", temp, "--raw", "x.json")
            self.assertEqual(result.returncode, 2)
            self.assertIn("next-theme-dev is not installed", result.stderr)
            self.assertNotIn("next-theme-figma is not installed", result.stderr)

    def test_installed_siblings_pass(self):
        result = run(HELPER, "check-siblings")
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
