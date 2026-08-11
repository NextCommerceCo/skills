import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
DEV = ROOT / "next-theme-dev"
FIXTURES = FIGMA / "tests" / "fixtures"
GENERATOR = FIGMA / "scripts" / "theme-figma.js"
VALIDATOR = DEV / "scripts" / "validate-theme-assets.py"

CANONICAL_ASSET_KEYS = {
    "asset_id", "section_id", "path", "asset_url_path", "figma_node_id",
    "source_layer_name", "prefix", "role", "alt", "format",
    "expected_width", "expected_height", "canvas_rendered",
    "optimization_status", "replace_with_backend_product_media",
    "clean_export_verified",
}


class AssetContractTest(unittest.TestCase):
    def load_fixture(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def run_downstream_asset(self, temp, filename, declared_format, *, requires_alpha=None,
                             contents=None, alt="Example icon"):
        theme = temp / "theme"
        asset = theme / "assets" / "img" / "example-store" / filename
        asset.parent.mkdir(parents=True)
        if contents is None:
            contents = '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
        asset.write_text(contents, encoding="utf-8")
        entry = {
            "path": f"assets/img/example-store/{filename}",
            "asset_url_path": f"img/example-store/{filename}",
            "figma_node_id": "30:1",
            "role": "icon",
            "alt": alt,
            "format": declared_format,
            "expected_width": 1,
            "expected_height": 1,
            "clean_export_verified": True,
        }
        if requires_alpha is not None:
            entry["requires_alpha"] = requires_alpha
        manifest = temp / "assets.json"
        manifest.write_text(json.dumps({
            "figma_file_key": "example-key",
            "assets": [entry],
        }), encoding="utf-8")
        return subprocess.run([
            "python3", str(VALIDATOR), "--theme", str(theme),
            "--manifest", str(manifest), "--strict",
        ], text=True, capture_output=True)

    def test_committed_fixtures_use_canonical_asset_schema(self):
        for name in (
            "spark-v1-package.json",
            "intro-v1-package.json",
            "custom-v1-package.json",
            "complete-package.json",
            "legacy-v0-package.json",
            "contradiction-package.json",
            "placeholder-package.json",
        ):
            with self.subTest(name=name):
                fixture = self.load_fixture(name)
                self.assertEqual(fixture["assets"]["schema_version"], "next-theme-figma/assets/v0")
                self.assertTrue(fixture["assets"]["assets"])
                self.assertTrue(CANONICAL_ASSET_KEYS <= fixture["assets"]["assets"][0].keys())
                asset = fixture["assets"]["assets"][0]
                if asset["format"] == "svg":
                    self.assertNotIn("requires_alpha", asset)
                else:
                    self.assertIsInstance(asset.get("requires_alpha"), bool)
                self.assertNotIn("target_path", fixture["assets"]["assets"][0])
                self.assertNotIn("source_node_id", fixture["assets"]["assets"][0])
                self.assertNotIn("expected_dimensions", fixture["assets"]["assets"][0])

    def test_downstream_strict_allows_empty_alt_for_decorative_asset(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_downstream_asset(Path(temp), "hero.svg", "svg", alt="")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)

    def test_downstream_strict_rejects_missing_canonical_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            theme = temp / "theme"
            asset = theme / "assets" / "img" / "example-store" / "hero.svg"
            asset.parent.mkdir(parents=True)
            asset.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>',
                encoding="utf-8",
            )
            manifest = temp / "assets.json"
            manifest.write_text(json.dumps({
                "figma_file_key": "example-key",
                "assets": [{
                    "path": "assets/img/example-store/hero.svg",
                    "figma_node_id": "30:1",
                }],
            }), encoding="utf-8")

            strict = subprocess.run([
                "python3", str(VALIDATOR), "--theme", str(theme),
                "--manifest", str(manifest), "--strict",
            ], text=True, capture_output=True)
            self.assertNotEqual(strict.returncode, 0, strict.stderr + strict.stdout)
            self.assertIn("missing canonical required field asset_url_path", strict.stderr)

            non_strict = subprocess.run([
                "python3", str(VALIDATOR), "--theme", str(theme),
                "--manifest", str(manifest), "--no-strict",
            ], text=True, capture_output=True)
            self.assertEqual(non_strict.returncode, 0, non_strict.stderr + non_strict.stdout)
            self.assertIn("missing canonical required field asset_url_path", non_strict.stdout)

    def test_downstream_strict_rejects_blank_canonical_string(self):
        with tempfile.TemporaryDirectory() as temp:
            self.run_downstream_asset(Path(temp), "icon.svg", "svg")
            manifest = Path(temp) / "assets.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["assets"][0]["asset_url_path"] = "   "
            manifest.write_text(json.dumps(data), encoding="utf-8")
            result = subprocess.run([
                "python3", str(VALIDATOR), "--theme", str(Path(temp) / "theme"),
                "--manifest", str(manifest), "--strict",
            ], text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("missing canonical required field asset_url_path", result.stderr)

    def test_downstream_alpha_requirement_is_raster_only(self):
        with tempfile.TemporaryDirectory() as temp:
            svg = self.run_downstream_asset(Path(temp), "icon.svg", "svg")
            self.assertEqual(svg.returncode, 0, svg.stderr + svg.stdout)

        with tempfile.TemporaryDirectory() as temp:
            raster = self.run_downstream_asset(
                Path(temp), "icon.png", "png", contents="not a png"
            )
            self.assertNotEqual(raster.returncode, 0, raster.stderr + raster.stdout)
            self.assertIn(
                "missing canonical required field requires_alpha", raster.stderr
            )

    def test_format_mismatch_fails_strict(self):
        with tempfile.TemporaryDirectory() as temp:
            result = self.run_downstream_asset(Path(temp), "icon.svg", "jpg")
        self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("declared format jpg does not match path extension svg", result.stderr)

    @unittest.skipUnless(shutil.which("node"), "node is required for generator contract execution")
    def test_complete_generator_output_passes_both_validators(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            package = temp / "handoff"
            theme = temp / "theme"
            asset = theme / "assets" / "img" / "example-store" / "hero.svg"
            asset.parent.mkdir(parents=True)
            asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600" viewBox="0 0 1200 600"><rect width="1200" height="600" fill="#ddd"/></svg>\n', encoding="utf-8")

            generator_input = self.load_fixture("spark-v1-package.json")
            generator_input["assets"]["assets"] = [{
                "asset_id": "hero-background",
                "section_id": "hero-1",
                "path": "assets/img/example-store/hero.svg",
                "figma_node_id": "30:1",
                "source_layer_name": "bg: hero",
                "prefix": "bg",
                "role": "hero-background",
                "alt": "Example hero background",
                "expected_width": 1200,
                "expected_height": 600,
                "optimization_status": "optimized",
            }]
            generator_fixture = temp / "generator-input.json"
            generator_fixture.write_text(json.dumps(generator_input), encoding="utf-8")

            generated = subprocess.run([
                "node", str(GENERATOR), "new-package", "--out", str(package),
                "--project", "example-store", "--fixture", str(generator_fixture),
            ], text=True, capture_output=True)
            self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)

            generated_assets = json.loads((package / "assets.json").read_text(encoding="utf-8"))
            self.assertEqual(generated_assets["assets"][0]["asset_url_path"], "img/example-store/hero.svg")
            self.assertEqual(generated_assets["assets"][0]["format"], "svg")
            self.assertNotIn("requires_alpha", generated_assets["assets"][0])
            self.assertIs(generated_assets["assets"][0]["clean_export_verified"], False)

            generated_handoff = json.loads((package / "figma-handoff.json").read_text(encoding="utf-8"))
            self.assertEqual(generated_handoff["schema_version"], "next-theme-figma/handoff/v1")
            self.assertEqual(generated_handoff["target"]["theme_family"], "spark")
            self.assertEqual(generated_handoff["target"]["runtime_contract"], "web-components")
            self.assertEqual(
                generated_handoff["manifests"]["platform_divergence_ledger"],
                "platform-divergence-ledger.json",
            )
            self.assertTrue((package / "platform-divergence-ledger.json").exists())
            self.assertFalse((package / "spark-divergence-ledger.json").exists())
            generated_divergence = json.loads(
                (package / "platform-divergence-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                generated_divergence["schema_version"],
                "next-theme-figma/platform-divergence/v1",
            )
            self.assertEqual(generated_divergence["entries"][0]["decision"], "platform-wins")
            self.assertIn("platform_behavior", generated_divergence["entries"][0])

            own = subprocess.run(["node", str(GENERATOR), "validate-package", str(package)], text=True, capture_output=True)
            self.assertEqual(own.returncode, 0, own.stderr + own.stdout)

            downstream = subprocess.run([
                "python3", str(VALIDATOR), "--theme", str(theme),
                "--manifest", str(package / "assets.json"), "--strict",
            ], text=True, capture_output=True)
            self.assertEqual(downstream.returncode, 0, downstream.stderr + downstream.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is required for generator contract execution")
    def test_placeholder_generator_output_fails_strict_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            package = temp / "handoff"
            theme = temp / "theme"
            (theme / "assets").mkdir(parents=True)
            generated = subprocess.run([
                "node", str(GENERATOR), "new-package", "--out", str(package),
                "--project", "example-store", "--fixture", str(FIXTURES / "placeholder-package.json"),
            ], text=True, capture_output=True)
            self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
            strict = subprocess.run(["node", str(GENERATOR), "validate-package", str(package)], text=True, capture_output=True)
            self.assertNotEqual(strict.returncode, 0, strict.stderr + strict.stdout)
            downstream = subprocess.run([
                "python3", str(VALIDATOR), "--theme", str(theme),
                "--manifest", str(package / "assets.json"), "--strict",
            ], text=True, capture_output=True)
            self.assertNotEqual(downstream.returncode, 0, downstream.stderr + downstream.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is required for generator contract execution")
    def test_generator_refuses_overwrite_without_force(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "handoff"
            command = [
                "node", str(GENERATOR), "new-package", "--out", str(package),
                "--project", "example-store", "--fixture", str(FIXTURES / "spark-v1-package.json"),
            ]
            first = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            refused = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to overwrite", refused.stderr)
            forced = subprocess.run(command + ["--force"], text=True, capture_output=True)
            self.assertEqual(forced.returncode, 0, forced.stderr + forced.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is required for generator contract execution")
    def test_default_generator_emits_v1(self):
        with tempfile.TemporaryDirectory() as temp:
            package = Path(temp) / "handoff"
            result = subprocess.run([
                "node", str(GENERATOR), "new-package", "--out", str(package),
                "--project", "example-store",
                "--figma-url", "https://www.figma.com/design/example-key/example",
                "--theme-family", "intro-bootstrap",
                "--runtime-contract", "jquery-core-js",
            ], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            handoff = json.loads((package / "figma-handoff.json").read_text(encoding="utf-8"))
            divergence = json.loads(
                (package / "platform-divergence-ledger.json").read_text(encoding="utf-8")
            )
            self.assertEqual(handoff["schema_version"], "next-theme-figma/handoff/v1")
            self.assertEqual(handoff["target"]["theme_family"], "intro-bootstrap")
            self.assertEqual(handoff["target"]["runtime_contract"], "jquery-core-js")
            self.assertIn("platform_divergence_ledger", handoff["manifests"])
            self.assertEqual(
                divergence["schema_version"],
                "next-theme-figma/platform-divergence/v1",
            )
            self.assertIn("platform_behavior", divergence["entries"][0])
            self.assertEqual(divergence["entries"][0]["decision"], "platform-wins")

    @unittest.skipUnless(shutil.which("node"), "node is required for schema validation")
    def test_family_fixture_validation_matrix(self):
        expected = {
            "spark-v1-package.json": (0, "PASS (strict)"),
            "intro-v1-package.json": (0, "PASS (strict)"),
            "custom-v1-package.json": (0, "PASS (strict)"),
        }
        for fixture_name, (returncode, marker) in expected.items():
            with self.subTest(fixture=fixture_name), tempfile.TemporaryDirectory() as temp:
                package = Path(temp) / "handoff"
                generated = subprocess.run([
                    "node", str(GENERATOR), "new-package", "--out", str(package),
                    "--project", "example-store", "--fixture", str(FIXTURES / fixture_name),
                ], text=True, capture_output=True)
                self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
                result = subprocess.run([
                    "node", str(GENERATOR), "validate-package", str(package),
                ], text=True, capture_output=True)
                self.assertEqual(result.returncode, returncode, result.stderr + result.stdout)
                self.assertIn(marker, result.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is required for schema validation")
    def test_v0_warning(self):
        for mode in ([], ["--non-strict"]):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                package = Path(temp) / "handoff"
                generated = subprocess.run([
                    "node", str(GENERATOR), "new-package", "--out", str(package),
                    "--project", "example-store", "--fixture",
                    str(FIXTURES / "legacy-v0-package.json"),
                ], text=True, capture_output=True)
                self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
                result = subprocess.run([
                    "node", str(GENERATOR), "validate-package", str(package), *mode,
                ], text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn("Warning: deprecated v0 handoff accepted", result.stdout)
                self.assertIn("platform-divergence-ledger.json", result.stdout)
                self.assertNotIn("Error:", result.stdout)

    @unittest.skipUnless(shutil.which("node"), "node is required for schema validation")
    def test_identity_contradiction_is_hard_in_both_modes(self):
        for mode in ([], ["--non-strict"]):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as temp:
                package = Path(temp) / "handoff"
                generated = subprocess.run([
                    "node", str(GENERATOR), "new-package", "--out", str(package),
                    "--project", "example-store", "--fixture",
                    str(FIXTURES / "contradiction-package.json"),
                ], text=True, capture_output=True)
                self.assertEqual(generated.returncode, 0, generated.stderr + generated.stdout)
                result = subprocess.run([
                    "node", str(GENERATOR), "validate-package", str(package), *mode,
                ], text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0, result.stderr + result.stdout)
                self.assertIn("theme_family", result.stdout)
                self.assertIn("runtime_contract", result.stdout)


if __name__ == "__main__":
    unittest.main()
