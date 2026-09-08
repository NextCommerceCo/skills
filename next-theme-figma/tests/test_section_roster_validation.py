"""Validator contract for Spark section roster fields in sections.json."""

import copy
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
FIXTURES = FIGMA / "tests" / "fixtures"
SPARK_FIXTURE = FIXTURES / "spark-vone-package.json"
COMPLETE_FIXTURE = FIXTURES / "complete-package.json"
VALIDATOR = FIGMA / "scripts" / "theme-figma.js"


class SectionRosterValidationTest(unittest.TestCase):
    def load_fixture(self, path=SPARK_FIXTURE):
        return json.loads(path.read_text(encoding="utf-8"))

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
        if "tokens" in fixture:
            files["tokens.json"] = fixture["tokens"]
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

    def run_case(self, mutate, *args, fixture_path=SPARK_FIXTURE):
        with tempfile.TemporaryDirectory() as temp:
            fixture = self.load_fixture(fixture_path)
            mutate(fixture)
            package = self.materialize(temp, fixture)
            return self.validate(package, *args)

    def add_library_sections(self, fixture):
        base = fixture["sections"]["sections"][0]
        definitions = (
            ("hero-1", "hero1-desktop", "section_hero", "shipped",
             "partials/section_hero.html"),
            ("faq-1", "faq1-desktop", "section_faq", "unshipped",
             "partials/section_faq.html"),
            ("reviews-1", "reviews1-desktop", "section_testimonials", "unshipped",
             "partials/section_testimonials.html"),
            ("footer-1", "footer1-desktop", "footer", "chrome",
             "partials/footer.html"),
        )
        for index, (section_id, figma_name, spark_section, status, template) in enumerate(
            definitions, start=2
        ):
            section = copy.deepcopy(base)
            section.update({
                "section_id": section_id,
                "route_id": "product",
                "order": index,
                "figma_names": {
                    "desktop": figma_name,
                    "tablet": "",
                    "mobile": "",
                },
                "figma_nodes": {
                    "desktop": f"40:{index}",
                    "tablet": "",
                    "mobile": "",
                },
                "classification": "semantic-rebuild",
                "classification_rationale": "Resolved through the Spark section roster.",
                "spark_section": spark_section,
                "roster_status": status,
                "implementation_target": {
                    "template": template,
                    "partials": [],
                    "assets": [],
                    "settings": [],
                },
                "commerce_surface": "",
                "asset_ids": [],
                "divergence_ids": [],
            })
            fixture["sections"]["sections"].append(section)
            fixture["routes"]["routes"][0]["section_order"].append(section_id)

    def test_standalone_unmapped_passes_with_summary_in_both_modes(self):
        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(lambda fixture: None, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # The roster segment precedes the tokens segment on the PASS line.
                self.assertIn(
                    "roster: 0 shipped, 0 unshipped, 0 chrome, 1 unmapped;",
                    result.stdout,
                )

    def test_library_named_spark_package_reports_all_roster_counts_in_both_modes(self):
        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(self.add_library_sections, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    result.stdout.count("targets unshipped Spark section"), 2
                )
                self.assertIn(
                    "roster: 1 shipped, 2 unshipped, 1 chrome, 1 unmapped",
                    result.stdout,
                )

    def test_invalid_roster_status_is_hard_in_both_modes(self):
        statuses = ("shipped-soon", "SHIPPED", " shipped", "chrome ", 7, None, {})
        for status in statuses:
            for args in ((), ("--non-strict",)):
                with self.subTest(status=status, args=args):
                    def mutate(fixture):
                        fixture["sections"]["sections"][0]["roster_status"] = status

                    result = self.run_case(mutate, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("buy-box-1: invalid roster_status", result.stdout)

    def test_unknown_spark_section_is_hard_in_both_modes(self):
        targets = (
            "section_wizard", "partials/section_hero.html", "section_hero ",
            "SECTION_HERO", "header.html",
        )
        for target in targets:
            for args in ((), ("--non-strict",)):
                with self.subTest(target=target, args=args):
                    def mutate(fixture):
                        fixture["sections"]["sections"][0]["spark_section"] = target

                    result = self.run_case(mutate, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(
                        f'buy-box-1: spark_section "{target}" is not in '
                        "references/spark-section-roster.json",
                        result.stdout,
                    )

    def test_roster_status_that_contradicts_resolved_section_is_rejected(self):
        cases = (
            ("section_faq", "shipped", "unshipped"),
            ("section_hero", "unshipped", "shipped"),
            ("section_hero", "chrome", "shipped"),
            ("section_hero", "unmapped", "shipped"),
            ("header", "shipped", "chrome"),
        )
        for target, status, roster_status in cases:
            for args in ((), ("--non-strict",)):
                with self.subTest(target=target, status=status, args=args):
                    def mutate(fixture):
                        section = fixture["sections"]["sections"][0]
                        section["spark_section"] = target
                        section["roster_status"] = status

                    result = self.run_case(mutate, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(
                        f'roster_status "{status}" contradicts the roster '
                        f"({target} is {roster_status})",
                        result.stdout,
                    )

    def test_non_unmapped_status_without_spark_section_is_rejected(self):
        for status in ("shipped", "unshipped", "chrome"):
            for args in ((), ("--non-strict",)):
                with self.subTest(status=status, args=args):
                    def mutate(fixture):
                        fixture["sections"]["sections"][0]["roster_status"] = status

                    result = self.run_case(mutate, *args)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn(
                        f'buy-box-1: roster_status "{status}" requires spark_section',
                        result.stdout,
                    )

    def test_missing_roster_status_warns_without_failing_existing_spark_package(self):
        def mutate(fixture):
            section = fixture["sections"]["sections"][0]
            section.pop("spark_section")
            section.pop("roster_status")

        for args, mode in (((), "strict"), (("--non-strict",), "non-strict")):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(result.stdout.count("missing roster_status"), 1)
                pass_line = next(
                    line for line in result.stdout.splitlines()
                    if line.startswith("[next-theme-figma] PASS")
                )
                self.assertTrue(
                    pass_line.startswith(
                        f"[next-theme-figma] PASS ({mode}) with 1 warning(s)"
                    ),
                    pass_line,
                )
                self.assertNotIn("roster:", pass_line)

    def test_unshipped_section_warns_without_error_in_both_modes(self):
        def mutate(fixture):
            section = fixture["sections"]["sections"][0]
            section["spark_section"] = "section_faq"
            section["roster_status"] = "unshipped"

        for args in ((), ("--non-strict",)):
            with self.subTest(args=args):
                result = self.run_case(mutate, *args)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual(
                    result.stdout.count("targets unshipped Spark section section_faq"),
                    1,
                )

    def test_validate_package_rejects_missing_or_empty_roster_in_both_modes(self):
        for roster_mode in ("missing", "empty"):
            for args in ((), ("--non-strict",)):
                with self.subTest(roster_mode=roster_mode, args=args), tempfile.TemporaryDirectory() as temp:
                    copied = Path(temp) / "next-theme-figma"
                    shutil.copytree(FIGMA, copied)
                    roster = copied / "references" / "spark-section-roster.json"
                    if roster_mode == "missing":
                        roster.unlink()
                    else:
                        roster.write_text("{}", encoding="utf-8")
                    package = self.materialize(temp, self.load_fixture())
                    result = subprocess.run(
                        ["node", str(copied / "scripts" / "theme-figma.js"),
                         "validate-package", str(package), *args],
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(
                        result.stdout,
                        "Error: references/spark-section-roster.json: missing or invalid\n",
                    )
                    self.assertEqual(result.stderr, "")
                    self.assertNotIn("    at ", result.stdout)

    def test_custom_package_has_no_roster_output(self):
        result = self.run_case(lambda fixture: None, fixture_path=COMPLETE_FIXTURE)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("roster", result.stdout)


if __name__ == "__main__":
    unittest.main()
