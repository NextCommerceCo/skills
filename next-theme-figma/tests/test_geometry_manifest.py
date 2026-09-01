"""Validator contract for the geometry and copy manifests.

The geometry manifest is the deterministic acceptance instrument for the
fidelity loop, so the validator has to reject the ways it can quietly become
useless: a hand-typed source, a frame measured at the wrong width, an element
box that does not belong to its section, and a rule that points at an element
the manifest does not carry.
"""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
FIXTURE = FIGMA / "tests" / "fixtures" / "complete-package.json"
VALIDATOR = FIGMA / "scripts" / "theme-figma.js"


class GeometryManifestTest(unittest.TestCase):
    def load_fixture(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def materialize(self, root, fixture):
        package = Path(root) / "handoff"
        package.mkdir(parents=True)
        files = {
            "figma-handoff.json": fixture["handoff"],
            "routes.json": fixture["routes"],
            "sections.json": fixture["sections"],
            "assets.json": fixture["assets"],
            "platform-divergence-ledger.json": fixture["divergence"],
            "viewport-coverage.json": fixture["coverage"],
            "geometry.json": fixture["geometry"],
            "copy.json": fixture["copy"],
        }
        for filename, body in files.items():
            (package / filename).write_text(json.dumps(body), encoding="utf-8")
        (package / "validation-checklist.md").write_text(
            "# Validation checklist\n", encoding="utf-8"
        )
        references = []
        for route in fixture["routes"]["routes"]:
            references.extend(route.get("reference_screenshots", {}).values())
        for entry in fixture["coverage"]["coverage"]:
            for name in ("desktop", "tablet", "mobile"):
                viewport = entry.get(name)
                if isinstance(viewport, dict):
                    references.extend(
                        viewport.get(field) for field in ("figma_ref", "preview_ref")
                    )
        for reference in filter(None, references):
            target = package / reference
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"reference")
        return package

    def validate(self, package, *args):
        return subprocess.run(
            ["node", str(VALIDATOR), "validate-package", str(package), *args],
            text=True,
            capture_output=True,
        )

    def run_case(self, mutate, *args):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            mutate(fixture)
            package = self.materialize(temp, fixture)
            return self.validate(package, *args)

    def test_complete_package_passes(self):
        result = self.run_case(lambda fixture: None)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS", result.stdout)

    def test_missing_geometry_fails_implementation_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = self.materialize(temp, fixture)
            (package / "geometry.json").unlink()
            result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing geometry.json", result.stdout)

    def test_missing_copy_fails_implementation_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = self.materialize(temp, fixture)
            (package / "copy.json").unlink()
            result = self.validate(package)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing copy.json", result.stdout)

    def test_missing_geometry_is_a_warning_outside_implementation_handoff(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            fixture["handoff"]["mode"] = "handoff-prep"
            del fixture["handoff"]["manifests"]["geometry"]
            package = self.materialize(temp, fixture)
            (package / "geometry.json").unlink()
            result = self.validate(package)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("geometry.json not present", result.stdout)

    def test_hand_written_geometry_source_is_rejected(self):
        def mutate(fixture):
            fixture["geometry"]["source"] = "operator-transcribed"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("source must be one of figma-metadata", result.stdout)

    def test_frame_width_must_match_the_viewport(self):
        def mutate(fixture):
            fixture["geometry"]["routes"][0]["viewports"]["desktop"]["frame_width"] = 1366

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("frame_width 1366 must be one of 1440", result.stdout)

    def test_section_box_outside_the_frame_is_rejected(self):
        def mutate(fixture):
            frame = fixture["geometry"]["routes"][0]["viewports"]["desktop"]
            frame["sections"][0]["box"]["x"] = 2000

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("lies entirely outside its frame box", result.stdout)

    def test_element_box_outside_its_section_is_rejected(self):
        def mutate(fixture):
            frame = fixture["geometry"]["routes"][0]["viewports"]["desktop"]
            frame["sections"][0]["elements"][0]["box"]["y"] = 5000

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("lies entirely outside its section box", result.stdout)

    def test_duplicate_selector_within_a_section_is_rejected(self):
        def mutate(fixture):
            elements = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]["elements"]
            elements[1]["selector"] = elements[0]["selector"]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is already used by", result.stdout)

    def test_alignment_group_referencing_an_unknown_element_is_rejected(self):
        def mutate(fixture):
            section = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]
            section["alignment_groups"][0]["element_ids"].append("hero_missing")

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('unknown element_id "hero_missing"', result.stdout)

    def test_gap_referencing_an_unknown_element_is_rejected(self):
        def mutate(fixture):
            section = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]
            section["gaps"][0]["to"] = "hero_missing"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('unknown to element_id "hero_missing"', result.stdout)

    def test_geometry_route_must_exist_in_routes_manifest(self):
        def mutate(fixture):
            fixture["geometry"]["routes"][0]["route_id"] = "not-a-route"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("route_id is not in routes.json", result.stdout)

    def test_geometry_section_must_exist_in_sections_manifest(self):
        def mutate(fixture):
            frame = fixture["geometry"]["routes"][0]["viewports"]["desktop"]
            frame["sections"][0]["section_id"] = "not-a-section"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("section_id is not in sections.json", result.stdout)

    def test_section_selector_is_required(self):
        def mutate(fixture):
            frame = fixture["geometry"]["routes"][0]["viewports"]["desktop"]
            del frame["sections"][0]["selector"]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing selector", result.stdout)

    def test_copy_deviation_without_a_reason_is_rejected(self):
        def mutate(fixture):
            fixture["copy"]["allowed_deviations"] = [
                {"deviation_id": "legal", "text": "Free returns", "approved_by": "operator"}
            ]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing reason", result.stdout)

    def test_copy_deviation_needs_exactly_one_matcher(self):
        def mutate(fixture):
            fixture["copy"]["allowed_deviations"] = [
                {
                    "deviation_id": "legal",
                    "text": "Free returns",
                    "pattern": "Free returns",
                    "reason": "both supplied",
                    "approved_by": "operator",
                }
            ]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("needs exactly one of text or pattern", result.stdout)

    def test_copy_string_must_name_a_known_section(self):
        def mutate(fixture):
            fixture["copy"]["strings"][0]["section_id"] = "not-a-section"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("section_id is not in sections.json", result.stdout)

    def test_invalid_assert_entry_is_rejected(self):
        def mutate(fixture):
            element = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]["elements"][0]
            element["assert"] = ["position-x", "left-edge"]

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid assert entry "left-edge"', result.stdout)

    def test_empty_assert_list_is_rejected(self):
        def mutate(fixture):
            element = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]["elements"][0]
            element["assert"] = []

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn("assert must be a non-empty array", result.stdout)

    def test_invalid_align_anchor_is_rejected(self):
        def mutate(fixture):
            element = fixture["geometry"]["routes"][0]["viewports"]["desktop"]["sections"][0]["elements"][0]
            element["align_anchor"] = "middle"

        result = self.run_case(mutate)
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid align_anchor "middle"', result.stdout)

    def test_generated_package_carries_both_manifests(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "package"
            result = subprocess.run(
                [
                    "node", str(VALIDATOR), "new-package",
                    "--out", str(out),
                    "--project", "example-store",
                    "--figma-url", "https://www.figma.com/design/example-key/example",
                    "--store", "example.29next.store",
                    "--repo", "/path/to/theme",
                    "--theme-family", "custom",
                    "--runtime-contract", "unknown",
                    "--mode", "implementation-handoff",
                ],
                text=True,
                capture_output=True,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            geometry = json.loads((out / "geometry.json").read_text(encoding="utf-8"))
            copy_manifest = json.loads((out / "copy.json").read_text(encoding="utf-8"))
            handoff = json.loads((out / "figma-handoff.json").read_text(encoding="utf-8"))
        self.assertEqual(geometry["schema_version"], "next-theme-figma/geometry/v1")
        self.assertEqual(geometry["source"], "figma-metadata")
        self.assertEqual(copy_manifest["schema_version"], "next-theme-figma/copy/v1")
        self.assertEqual(handoff["manifests"]["geometry"], "geometry.json")
        self.assertEqual(handoff["manifests"]["copy"], "copy.json")


if __name__ == "__main__":
    unittest.main()
