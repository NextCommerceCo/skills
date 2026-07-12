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
    "expected_width", "expected_height", "requires_alpha", "canvas_rendered",
    "optimization_status", "replace_with_backend_product_media",
    "clean_export_verified",
}


class AssetContractTest(unittest.TestCase):
    def load_fixture(self, name):
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_committed_fixtures_use_canonical_asset_schema(self):
        for name in ("complete-package.json", "placeholder-package.json"):
            with self.subTest(name=name):
                fixture = self.load_fixture(name)
                self.assertEqual(fixture["assets"]["schema_version"], "next-theme-figma/assets/v0")
                self.assertTrue(fixture["assets"]["assets"])
                self.assertTrue(CANONICAL_ASSET_KEYS <= fixture["assets"]["assets"][0].keys())
                self.assertNotIn("target_path", fixture["assets"]["assets"][0])
                self.assertNotIn("source_node_id", fixture["assets"]["assets"][0])
                self.assertNotIn("expected_dimensions", fixture["assets"]["assets"][0])

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

    @unittest.skipUnless(shutil.which("node"), "node is required for generator contract execution")
    def test_complete_generator_output_passes_both_validators(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            package = temp / "handoff"
            theme = temp / "theme"
            asset = theme / "assets" / "img" / "example-store" / "hero.svg"
            asset.parent.mkdir(parents=True)
            asset.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600" viewBox="0 0 1200 600"><rect width="1200" height="600" fill="#ddd"/></svg>\n', encoding="utf-8")

            generator_input = self.load_fixture("complete-package.json")
            generator_input["assets"]["assets"] = [{
                "asset_id": "hero-background",
                "section_id": "hero-1",
                "path": "assets/img/example-store/hero.svg",
                "figma_node_id": "30:1",
                "source_layer_name": "bg: hero",
                "prefix": "bg",
                "role": "hero-background",
                "alt": "",
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
            self.assertIs(generated_assets["assets"][0]["requires_alpha"], False)
            self.assertIs(generated_assets["assets"][0]["clean_export_verified"], False)

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
                "--project", "example-store", "--fixture", str(FIXTURES / "complete-package.json"),
            ]
            first = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
            refused = subprocess.run(command, text=True, capture_output=True)
            self.assertNotEqual(refused.returncode, 0)
            self.assertIn("refusing to overwrite", refused.stderr)
            forced = subprocess.run(command + ["--force"], text=True, capture_output=True)
            self.assertEqual(forced.returncode, 0, forced.stderr + forced.stdout)


if __name__ == "__main__":
    unittest.main()
