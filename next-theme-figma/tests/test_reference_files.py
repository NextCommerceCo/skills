import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
FIXTURE = FIGMA / "tests" / "fixtures" / "complete-package.json"
VALIDATOR = FIGMA / "scripts" / "theme-figma.js"


class ReferenceFilesTest(unittest.TestCase):
    def load_fixture(self):
        return json.loads(FIXTURE.read_text(encoding="utf-8"))

    def materialize_fixture(self, package, fixture):
        package.mkdir(parents=True)
        files = {
            "figma-handoff.json": fixture["handoff"],
            "routes.json": fixture["routes"],
            "sections.json": fixture["sections"],
            "assets.json": fixture["assets"],
            "platform-divergence-ledger.json": fixture["divergence"],
            "viewport-coverage.json": fixture["coverage"],
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

    def run_validator(self, package, *args):
        return subprocess.run([
            "node", str(VALIDATOR), "validate-package", str(package), *args,
        ], text=True, capture_output=True)

    def write_manifest(self, package, filename, body):
        (package / filename).write_text(json.dumps(body), encoding="utf-8")

    def test_missing_reference_screenshot_fails_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = (
                "refs/home-desktop-1440.png"
            )
            self.write_manifest(package, "routes.json", fixture["routes"])

            result = self.run_validator(package)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(
            "reference_screenshots.desktop: file not found: refs/home-desktop-1440.png",
            result.stdout,
        )

    def test_missing_reference_screenshot_warns_non_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = (
                "refs/home-desktop-1440.png"
            )
            self.write_manifest(package, "routes.json", fixture["routes"])

            result = self.run_validator(package, "--non-strict")

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(
            "Warning: home: reference_screenshots.desktop: file not found: "
            "refs/home-desktop-1440.png",
            result.stdout,
        )

    def test_present_reference_screenshot_passes_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            reference = "refs/home-desktop-1440.png"
            fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = reference
            self.write_manifest(package, "routes.json", fixture["routes"])
            (package / reference).write_bytes(b"reference")

            result = self.run_validator(package)

        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("PASS (strict)", result.stdout)

    def test_missing_coverage_figma_ref_fails_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            fixture["coverage"]["coverage"][0]["desktop"]["figma_ref"] = (
                "refs/missing.png"
            )
            self.write_manifest(package, "viewport-coverage.json", fixture["coverage"])

            result = self.run_validator(package)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(
            "desktop.figma_ref: file not found: refs/missing.png",
            result.stdout,
        )

    def test_missing_coverage_preview_ref_fails_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            fixture["coverage"]["coverage"][0]["desktop"]["preview_ref"] = (
                "refs/missing-preview.png"
            )
            self.write_manifest(package, "viewport-coverage.json", fixture["coverage"])

            result = self.run_validator(package)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn(
            "desktop.preview_ref: file not found: refs/missing-preview.png",
            result.stdout,
        )

    def test_reference_pointing_at_directory_fails_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = "refs"
            self.write_manifest(package, "routes.json", fixture["routes"])

            result = self.run_validator(package)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("not a file: refs", result.stdout)

    def test_symlink_escaping_package_is_an_error(self):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture()
            package = Path(temp) / "handoff"
            self.materialize_fixture(package, fixture)
            outside = Path(temp) / "outside.png"
            outside.write_bytes(b"png")
            link = package / "refs" / "escape.png"
            link.symlink_to(outside)
            fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = (
                "refs/escape.png"
            )
            self.write_manifest(package, "routes.json", fixture["routes"])

            result = self.run_validator(package)

        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("resolves outside the package (symlink escape)", result.stdout)

    def test_reference_path_escaping_package_is_an_error(self):
        for mode in ((), ("--non-strict",)):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                fixture = self.load_fixture()
                package = Path(temp) / "handoff"
                self.materialize_fixture(package, fixture)
                fixture["routes"]["routes"][0]["reference_screenshots"]["desktop"] = (
                    "../outside.png"
                )
                self.write_manifest(package, "routes.json", fixture["routes"])

                result = self.run_validator(package, *mode)

            self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertIn("must be a relative path inside the package", result.stdout)


if __name__ == "__main__":
    unittest.main()
