"""Behavioural tests for the geometry comparator.

The case that matters most is the negative control: a wrong indent that mean
per-section pixel mismatch scores at a fraction of a percent must FAIL here,
by name, with the delta in pixels. Everything else guards the arithmetic that
makes that verdict trustworthy.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "next-theme-dev" / "scripts" / "assert-geometry.mjs"

MANIFEST = {
    "schema_version": "next-theme-figma/geometry/v1",
    "project": "example-store",
    "source": "figma-metadata",
    "extracted_at": "2026-01-01T00:00:00.000Z",
    "routes": [
        {
            "route_id": "home",
            "viewports": {
                "desktop": {
                    "frame_node_id": "10:1",
                    "frame_width": 1440,
                    "frame_height": 2400,
                    "sections": [
                        {
                            "section_id": "hero-1",
                            "node_id": "20:1",
                            "selector": '[data-geo-section="hero-1"]',
                            "box": {"x": 0, "y": 0, "width": 1440, "height": 720},
                            "elements": [
                                {
                                    "element_id": "hero_heading",
                                    "node_id": "20:2",
                                    "selector": '[data-geo="hero-heading"]',
                                    "role": "text",
                                    "box": {"x": 120, "y": 180, "width": 560, "height": 96},
                                },
                                {
                                    "element_id": "hero_body",
                                    "node_id": "20:3",
                                    "selector": '[data-geo="hero-body"]',
                                    "role": "text",
                                    "box": {"x": 120, "y": 300, "width": 480, "height": 72},
                                },
                            ],
                            "alignment_groups": [
                                {
                                    "group_id": "hero_text_left",
                                    "edge": "left",
                                    "element_ids": ["hero_heading", "hero_body"],
                                }
                            ],
                            "gaps": [
                                {
                                    "gap_id": "heading_to_body",
                                    "axis": "vertical",
                                    "from": "hero_heading",
                                    "to": "hero_body",
                                    "value": 24,
                                }
                            ],
                        }
                    ],
                }
            },
        }
    ],
}


def boxes(**overrides):
    """Boxes that match the manifest exactly, before any override is applied."""
    measured = {
        "schema_version": "next-theme-dev/geometry-boxes/v1",
        "route_id": "home",
        "viewport": "desktop",
        "viewport_width": 1440,
        "url": "https://example.test/",
        "boxes": {
            "hero-1": {"found": True, "count": 1, "x": 0, "y": 0, "width": 1440, "height": 720},
            "hero-1::hero_heading": {
                "found": True, "count": 1, "x": 120, "y": 180, "width": 560, "height": 96,
            },
            "hero-1::hero_body": {
                "found": True, "count": 1, "x": 120, "y": 300, "width": 480, "height": 72,
            },
        },
    }
    measured.update(overrides)
    return measured


class AssertGeometryTest(unittest.TestCase):
    def compare(self, measured, manifest=None, *args):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest_path = root / "geometry.json"
            manifest_path.write_text(json.dumps(manifest or MANIFEST), encoding="utf-8")
            boxes_path = root / "boxes.json"
            boxes_path.write_text(json.dumps(measured), encoding="utf-8")
            report_path = root / "report.json"
            result = subprocess.run(
                [
                    "node", str(SCRIPT), "compare",
                    "--manifest", str(manifest_path),
                    "--route", "home",
                    "--viewport", "desktop",
                    "--boxes", str(boxes_path),
                    "--report", str(report_path),
                    *args,
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

    def failing_checks(self, report):
        return [check for check in report["checks"] if check["status"] != "pass"]

    def test_matching_layout_passes(self):
        result, report = self.compare(boxes())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["failures"], 0)

    def test_wrong_indent_fails_by_name(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 160
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["status"], "fail")
        position = [
            check for check in self.failing_checks(report)
            if check["check"] == "position" and check["element_id"] == "hero_body"
        ]
        self.assertEqual(len(position), 1)
        self.assertEqual(position[0]["delta"], 40)
        self.assertIn("hero_body", result.stdout)
        self.assertIn("+40px", result.stdout)

    def test_broken_left_edge_alignment_fails(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 126
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        alignment = [
            check for check in self.failing_checks(report) if check["check"] == "alignment"
        ]
        self.assertEqual(len(alignment), 1)
        self.assertEqual(alignment[0]["group_id"], "hero_text_left")
        self.assertEqual(alignment[0]["delta"], 6)

    def test_alignment_is_tighter_than_position_tolerance(self):
        # 6px of drift is inside the 8px position tolerance and outside the
        # 4px alignment tolerance: the shared edge is the stricter contract.
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 126
        _, report = self.compare(measured)
        failing = {check["check"] for check in self.failing_checks(report)}
        self.assertEqual(failing, {"alignment"})

    def test_sibling_gap_drift_fails(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["y"] = 320
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        gaps = [check for check in self.failing_checks(report) if check["check"] == "gap"]
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["expected"], 24)
        self.assertEqual(gaps[0]["actual"], 44)

    def test_size_drift_fails(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_heading"]["height"] = 140
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        sizes = [check for check in self.failing_checks(report) if check["check"] == "size"]
        self.assertEqual(len(sizes), 1)
        self.assertEqual(sizes[0]["axis"], "height")

    def test_section_drift_does_not_move_its_elements(self):
        # Everything above the section grew by 600px. Element positions are
        # section-relative, so the section moving is not an element failure.
        measured = boxes()
        measured["boxes"]["hero-1"]["y"] = 600
        for key in ("hero-1::hero_heading", "hero-1::hero_body"):
            measured["boxes"][key]["y"] += 600
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["status"], "pass")

    def test_missing_element_is_a_failure_not_a_pass(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"] = {"found": False, "count": 0}
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["summary"]["missing"], 1)
        self.assertIn("MISSING", result.stdout)
        self.assertIn("matched no element", result.stdout)

    def test_ambiguous_selector_is_a_failure(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"] = {"found": False, "count": 3}
        result, _ = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        self.assertIn("matched 3 elements", result.stdout)

    def test_missing_section_skips_its_elements(self):
        measured = boxes()
        measured["boxes"]["hero-1"] = {"found": False, "count": 0}
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(report["summary"]["missing"], 1)
        self.assertEqual(report["checks"][0]["section_id"], "hero-1")

    def test_width_mismatch_is_refused(self):
        measured = boxes()
        measured["viewport_width"] = 1366
        result, report = self.compare(measured)
        self.assertEqual(result.returncode, 2)
        self.assertIsNone(report)
        self.assertIn("viewport width mismatch", result.stderr)

    def test_scale_mode_fit_scales_the_manifest(self):
        measured = boxes()
        measured["viewport_width"] = 720
        for key, box in measured["boxes"].items():
            for field in ("x", "y", "width", "height"):
                box[field] = box[field] / 2
        result, report = self.compare(measured, None, "--scale-mode", "fit")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(report["scale"], 0.5)

    def test_route_viewport_mismatch_is_refused(self):
        measured = boxes()
        measured["viewport"] = "mobile"
        result, _ = self.compare(measured)
        self.assertEqual(result.returncode, 2)
        self.assertIn("asked for home/desktop", result.stderr)

    def test_tolerance_override_is_honoured(self):
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 160
        result, _ = self.compare(measured, None, "--position-tolerance", "50",
                                 "--alignment-tolerance", "50")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_per_element_tolerance_from_the_manifest_is_honoured(self):
        manifest = json.loads(json.dumps(MANIFEST))
        section = manifest["routes"][0]["viewports"]["desktop"]["sections"][0]
        section["elements"][1]["tolerance_px"] = 48
        section["alignment_groups"] = []
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 160
        result, _ = self.compare(measured, manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_assert_list_drops_the_checks_extraction_cannot_support(self):
        # A hug-width Figma text layer measures its glyphs; the DOM block fills
        # its column. Asserting that width would fail on a correct build.
        manifest = json.loads(json.dumps(MANIFEST))
        section = manifest["routes"][0]["viewports"]["desktop"]["sections"][0]
        section["elements"][0]["assert"] = ["position-x", "position-y", "height"]
        measured = boxes()
        measured["boxes"]["hero-1::hero_heading"]["width"] = 768
        result, report = self.compare(measured, manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        heading_checks = {
            (check["check"], check["axis"])
            for check in report["checks"]
            if check.get("element_id") == "hero_heading" and "axis" in check
        }
        self.assertNotIn(("size", "width"), heading_checks)
        self.assertIn(("size", "height"), heading_checks)

    def test_centre_anchor_compares_the_centre_not_the_left_edge(self):
        manifest = json.loads(json.dumps(MANIFEST))
        section = manifest["routes"][0]["viewports"]["desktop"]["sections"][0]
        heading = section["elements"][0]
        heading["align_anchor"] = "center"
        heading["assert"] = ["position-x"]
        section["alignment_groups"] = []
        section["gaps"] = []
        measured = boxes()
        # Same centre (400), different box: 120+560/2 == 280+240/2.
        measured["boxes"]["hero-1::hero_heading"]["x"] = 280
        measured["boxes"]["hero-1::hero_heading"]["width"] = 240
        result, report = self.compare(measured, manifest)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        position = [
            check for check in report["checks"]
            if check["check"] == "position" and check.get("element_id") == "hero_heading"
        ]
        self.assertEqual(position[0]["axis"], "x-center")
        self.assertEqual(position[0]["delta"], 0)

    def test_centre_anchor_still_catches_a_shifted_centre(self):
        manifest = json.loads(json.dumps(MANIFEST))
        section = manifest["routes"][0]["viewports"]["desktop"]["sections"][0]
        heading = section["elements"][0]
        heading["align_anchor"] = "center"
        heading["assert"] = ["position-x"]
        section["alignment_groups"] = []
        section["gaps"] = []
        measured = boxes()
        measured["boxes"]["hero-1::hero_heading"]["x"] = 320
        measured["boxes"]["hero-1::hero_heading"]["width"] = 240
        result, _ = self.compare(measured, manifest)
        self.assertEqual(result.returncode, 1)
        self.assertIn("position.x-center", result.stdout)

    def test_tolerance_px_does_not_relax_alignment(self):
        # An elastic element still holds its shared edge. Documented in
        # references/geometry-and-readback-gates.md; asserted here so the
        # scope of tolerance_px cannot drift silently.
        manifest = json.loads(json.dumps(MANIFEST))
        section = manifest["routes"][0]["viewports"]["desktop"]["sections"][0]
        section["elements"][1]["tolerance_px"] = 48
        measured = boxes()
        measured["boxes"]["hero-1::hero_body"]["x"] = 140
        result, report = self.compare(measured, manifest)
        self.assertEqual(result.returncode, 1)
        failing = {check["check"] for check in self.failing_checks(report)}
        self.assertEqual(failing, {"alignment"})

    def test_probe_snippet_names_every_selector(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest_path = Path(temp) / "geometry.json"
            manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
            result = subprocess.run(
                [
                    "node", str(SCRIPT), "probe",
                    "--manifest", str(manifest_path),
                    "--route", "home",
                    "--viewport", "desktop",
                ],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        for selector in (
            '[data-geo-section=\\"hero-1\\"]',
            '[data-geo=\\"hero-heading\\"]',
            '[data-geo=\\"hero-body\\"]',
        ):
            self.assertIn(selector, result.stdout)
        self.assertIn("getBoundingClientRect", result.stdout)
        self.assertIn("next-theme-dev/geometry-boxes/v1", result.stdout)

    def test_unknown_route_is_an_input_error(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest_path = Path(temp) / "geometry.json"
            manifest_path.write_text(json.dumps(MANIFEST), encoding="utf-8")
            result = subprocess.run(
                [
                    "node", str(SCRIPT), "selectors",
                    "--manifest", str(manifest_path),
                    "--route", "not-a-route",
                    "--viewport", "desktop",
                ],
                text=True,
                capture_output=True,
            )
        self.assertEqual(result.returncode, 2)
        self.assertIn('no route "not-a-route"', result.stderr)


if __name__ == "__main__":
    unittest.main()
