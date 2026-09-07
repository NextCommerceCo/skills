"""Contract tests for the canonical Spark section roster and frame inference."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FIGMA = ROOT / "next-theme-figma"
ROSTER = FIGMA / "references" / "spark-section-roster.json"
VALIDATOR = FIGMA / "scripts" / "theme-figma.js"

FAMILIES = {
    "hero", "promo", "sticky", "features", "benefits", "icons", "howto",
    "compare", "faq", "reviews", "testimonials", "ugc", "media", "guarantee",
    "bottomcta", "results", "beforeafter", "science", "ingredients",
    "problemsolution", "nav", "footer", "featured",
}
SHIPPED = {
    "section_hero", "section_featured_product", "section_featured_products",
    "section_featured_categories", "section_on_sale", "section_promo_banner",
}
TIER_ONE = {
    "section_value_props", "section_process_steps", "section_comparison_table",
    "section_benefit_grid", "section_press_logos", "section_faq",
    "section_image_text", "section_cta_band", "section_testimonials",
}
CLASSIFICATIONS = {
    "semantic-rebuild", "composed-asset", "background-asset",
    "live-commerce-component", "platform-app-hook", "screenshot-fallback",
}


class SectionRosterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.roster = json.loads(ROSTER.read_text(encoding="utf-8"))

    def run_command(self, *args):
        return subprocess.run(
            ["node", str(VALIDATOR), *args],
            text=True,
            capture_output=True,
        )

    def infer(self, frame_name):
        result = self.run_command("infer-section", frame_name)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return json.loads(result.stdout)

    def run_with_roster_mutation(self, mutate):
        with tempfile.TemporaryDirectory() as temp:
            copied = Path(temp) / "next-theme-figma"
            shutil.copytree(FIGMA, copied)
            roster = copied / "references" / "spark-section-roster.json"
            body = json.loads(roster.read_text(encoding="utf-8"))
            mutate(body)
            roster.write_text(json.dumps(body), encoding="utf-8")
            return subprocess.run(
                ["node", str(copied / "scripts" / "theme-figma.js"),
                 "infer-section", "hero1-desktop"],
                text=True,
                capture_output=True,
            )

    def assert_legacy_fields(self, frame_name, expected):
        actual = self.infer(frame_name)
        legacy_fields = (
            "frame_name", "normalized_base", "section_id", "category",
            "number", "breakpoint", "valid_contract_name", "expected_pattern",
        )
        self.assertEqual({field: actual[field] for field in legacy_fields}, expected)

    def test_roster_has_exactly_the_unique_23_families(self):
        families = [entry["family"] for entry in self.roster["entries"]]
        self.assertEqual(len(families), 23)
        self.assertEqual(len(families), len(set(families)))
        self.assertEqual(set(families), FAMILIES)

    def test_statuses_use_the_declared_vocabulary(self):
        statuses = set(self.roster["statuses"])
        self.assertEqual(statuses, {"shipped", "unshipped", "chrome"})
        for entry in self.roster["entries"]:
            self.assertIn(entry["status"], statuses)

    def test_shipped_sections_are_exactly_the_six_known_names(self):
        shipped = set()
        for entry in self.roster["entries"]:
            if entry["status"] == "shipped":
                shipped.add(entry["spark_section"])
                shipped.update(entry["alternates"])
        self.assertEqual(shipped, SHIPPED)

    def test_unshipped_sections_are_exactly_the_tier_one_names(self):
        unshipped = set()
        for entry in self.roster["entries"]:
            if entry["status"] != "unshipped":
                continue
            unshipped.add(entry["spark_section"])
            unshipped.update(entry["alternates"])
        self.assertEqual(unshipped, TIER_ONE)

    def test_tiers_match_status(self):
        for entry in self.roster["entries"]:
            expected_tier = 1 if entry["status"] == "unshipped" else 0
            self.assertEqual(entry["tier"], expected_tier)

    def test_chrome_entries_are_exactly_header_and_footer(self):
        chrome = {
            (entry["family"], entry["spark_section"], entry["template"])
            for entry in self.roster["entries"] if entry["status"] == "chrome"
        }
        self.assertEqual(chrome, {
            ("nav", "header", "partials/header.html"),
            ("footer", "footer", "partials/footer.html"),
        })

    def test_every_template_matches_its_spark_section(self):
        for entry in self.roster["entries"]:
            self.assertEqual(entry["template"], f"partials/{entry['spark_section']}.html")

    def test_suggested_classifications_are_valid(self):
        for entry in self.roster["entries"]:
            self.assertIn(entry["suggested_classification"], CLASSIFICATIONS)

    def test_rendered_roster_is_current(self):
        result = self.run_command("render-roster", "--check")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_rendered_roster_check_detects_drift_at_an_alternate_output(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "spark-section-roster.md"
            written = self.run_command("render-roster", "--write", "--out", str(output))
            self.assertEqual(written.returncode, 0, written.stdout + written.stderr)
            content = output.read_text(encoding="utf-8")
            output.write_text(content.replace("| hero |", "| hero-drifted |", 1), encoding="utf-8")
            checked = self.run_command("render-roster", "--check", "--out", str(output))
        self.assertEqual(checked.returncode, 1)
        self.assertIn("rendered roster differs", checked.stderr)

    def test_render_roster_rejects_write_and_check_together(self):
        result = self.run_command("render-roster", "--write", "--check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("only one of", result.stderr)

    def test_render_roster_rejects_positional_arguments(self):
        for args in (
            ("unexpected", "--check"),
            ("--check", "unexpected"),
            ("--write", "unexpected"),
            ("--out", "ignored.md", "unexpected", "--write"),
        ):
            with self.subTest(args=args):
                result = self.run_command("render-roster", *args)
                self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
                self.assertIn(
                    'does not accept positional argument "unexpected"', result.stderr
                )
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp) / "roster.md"
            written = self.run_command("render-roster", "--write", "--out", str(out))
            self.assertEqual(written.returncode, 0, written.stdout + written.stderr)
            checked = self.run_command("render-roster", "--check", "--out", str(out))
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)

    def test_render_roster_rejects_unknown_flags(self):
        result = self.run_command("render-roster", "--bogus")
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not accept --bogus", result.stderr)

    def test_render_roster_rejects_out_without_a_value_before_writing(self):
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                ["node", str(VALIDATOR), "render-roster", "--write", "--out"],
                cwd=temp,
                text=True,
                capture_output=True,
            )
            self.assertEqual(list(Path(temp).iterdir()), [])
        self.assertEqual(result.returncode, 1)
        self.assertIn("--out requires a file path", result.stderr)

    def test_hero_design_family_resolves_to_shipped_spark_section(self):
        result = self.infer("hero1-desktop")
        self.assertEqual(result["spark_section"], "section_hero")
        self.assertEqual(result["spark_status"], "shipped")

    def test_hero_spark_name_resolves_to_shipped_spark_section(self):
        result = self.infer("section_hero-desktop")
        self.assertEqual(result["spark_section"], "section_hero")
        self.assertEqual(result["spark_status"], "shipped")
        self.assertEqual(result["naming"], "spark-section")
        self.assertTrue(result["valid_contract_name"])

    def test_spark_name_matching_is_case_insensitive(self):
        result = self.infer("Section_Hero-Desktop")
        self.assertEqual(result["spark_section"], "section_hero")
        self.assertEqual(result["naming"], "spark-section")

    def test_hyphen_and_space_forms_do_not_use_spark_naming(self):
        for frame_name in ("section-hero-desktop", "section hero desktop"):
            with self.subTest(frame_name=frame_name):
                result = self.infer(frame_name)
                self.assertEqual(result["naming"], "")
                self.assertEqual(result["spark_status"], "unmapped")
                self.assertEqual(result["section_id"], "section-hero")
                self.assertFalse(result["valid_contract_name"])

    def test_spark_alternate_uses_first_matching_family_in_file_order(self):
        result = self.infer("section_benefit_grid-desktop")
        self.assertEqual(result["family"], "features")
        self.assertEqual(result["spark_section"], "section_benefit_grid")

    def test_benefits_resolves_with_classification_and_alternate(self):
        result = self.infer("benefits3-mobile")
        self.assertEqual(result["spark_section"], "section_value_props")
        self.assertEqual(result["spark_status"], "unshipped")
        self.assertEqual(result["suggested_classification"], "semantic-rebuild")
        self.assertEqual(result["spark_alternates"], ["section_benefit_grid"])

    def test_nav_resolves_to_chrome_header(self):
        result = self.infer("nav1-desktop")
        self.assertEqual(result["spark_section"], "header")
        self.assertEqual(result["spark_status"], "chrome")
        self.assertEqual(result["spark_template"], "partials/header.html")

    def test_sticky_keeps_legacy_section_id_and_raw_lookup_family(self):
        result = self.infer("sticky1-desktop")
        self.assertEqual(result["section_id"], "bottomcta-1")
        self.assertEqual(result["family"], "sticky")
        self.assertEqual(result["spark_section"], "section_promo_banner")

    def test_unknown_design_family_keeps_legacy_section_id(self):
        result = self.infer("wizard1-desktop")
        self.assertEqual(result["section_id"], "wizard-1")
        self.assertEqual(result["spark_status"], "unmapped")
        self.assertEqual(result["spark_section"], "")
        self.assertTrue(result["valid_contract_name"])

    def test_similar_names_are_not_guessed_as_design_families(self):
        cases = {
            "heroes1-desktop": "heroes-1",
            "hero1-desktop-v2": "hero1-desktop-v2",
            "hero1desktop": "hero1desktop",
        }
        for frame_name, section_id in cases.items():
            with self.subTest(frame_name=frame_name):
                result = self.infer(frame_name)
                self.assertEqual(result["spark_status"], "unmapped")
                self.assertEqual(result["spark_section"], "")
                self.assertEqual(result["section_id"], section_id)

    def test_unknown_spark_section_is_invalid_and_unmapped(self):
        result = self.infer("section_wizard-desktop")
        self.assertEqual(result["spark_status"], "unmapped")
        self.assertEqual(result["spark_section"], "")
        self.assertFalse(result["valid_contract_name"])

    def test_legacy_inference_fields_remain_unchanged(self):
        pattern = "{category}{number}-{breakpoint}"
        cases = {
            "hero1-desktop": ("hero1", "hero-1", "hero", "1", "desktop", True),
            "benefits3-mobile": ("benefits3", "benefits-3", "benefits", "3", "mobile", True),
            "faq-2-tablet": ("faq-2", "faq--2", "faq-", "2", "tablet", True),
            "sticky1-desktop": ("sticky1", "bottomcta-1", "bottomcta", "1", "desktop", True),
            "section-wizard1-desktop": ("section-wizard1", "section-wizard-1", "section-wizard", "1", "desktop", True),
            "section_hero1-desktop": ("section-hero1", "section-hero-1", "section-hero", "1", "desktop", True),
            "Hero 1 Desktop": ("hero-1", "hero--1", "hero-", "1", "desktop", True),
            "wizard1-desktop": ("wizard1", "wizard-1", "wizard", "1", "desktop", True),
            "hero1": ("hero1", "hero-1", "hero", "1", "", False),
            "section-hero-desktop": ("section-hero", "section-hero", "", "", "desktop", False),
            "section hero desktop": ("section-hero", "section-hero", "", "", "desktop", False),
            "hero-desktop": ("hero", "hero", "", "", "desktop", False),
            "1hero-desktop": ("1hero", "1hero", "", "", "desktop", False),
        }
        for frame_name, values in cases.items():
            with self.subTest(frame_name=frame_name):
                normalized, section_id, category, number, breakpoint, valid = values
                self.assert_legacy_fields(frame_name, {
                    "frame_name": frame_name,
                    "normalized_base": normalized,
                    "section_id": section_id,
                    "category": category,
                    "number": number,
                    "breakpoint": breakpoint,
                    "valid_contract_name": valid,
                    "expected_pattern": pattern,
                })

    def test_missing_or_invalid_roster_has_deliberate_error_without_stack(self):
        for mode in ("invalid", "missing"):
            for frame_name in ("hero1-desktop", "hero-desktop", "section hero desktop"):
                with self.subTest(mode=mode, frame_name=frame_name), tempfile.TemporaryDirectory() as temp:
                    copied = Path(temp) / "next-theme-figma"
                    shutil.copytree(FIGMA, copied)
                    roster = copied / "references" / "spark-section-roster.json"
                    if mode == "invalid":
                        roster.write_text("{invalid json", encoding="utf-8")
                    else:
                        roster.unlink()
                    result = subprocess.run(
                        ["node", str(copied / "scripts" / "theme-figma.js"),
                         "infer-section", frame_name],
                        text=True,
                        capture_output=True,
                    )
                    self.assertEqual(result.returncode, 1)
                    self.assertTrue(result.stderr.startswith("Error:"), result.stderr)
                    self.assertIn("references/spark-section-roster.json: missing or invalid", result.stderr)
                    self.assertEqual(len(result.stderr.rstrip().splitlines()), 1)
                    self.assertNotIn("    at ", result.stderr)

    def test_roster_rejects_missing_tier_and_invented_status(self):
        mutations = {
            "missing-tier": lambda roster: roster["entries"][0].pop("tier"),
            "invented-status": lambda roster: roster["statuses"].append("planned"),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temp:
                copied = Path(temp) / "next-theme-figma"
                shutil.copytree(FIGMA, copied)
                roster = copied / "references" / "spark-section-roster.json"
                body = json.loads(roster.read_text(encoding="utf-8"))
                mutate(body)
                roster.write_text(json.dumps(body), encoding="utf-8")
                result = subprocess.run(
                    ["node", str(copied / "scripts" / "theme-figma.js"),
                     "infer-section", "hero1-desktop"],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertTrue(result.stderr.startswith("Error:"), result.stderr)
                self.assertIn("references/spark-section-roster.json: missing or invalid", result.stderr)
                self.assertEqual(len(result.stderr.rstrip().splitlines()), 1)
                self.assertNotIn("    at ", result.stderr)

    def test_roster_rejects_duplicate_family_with_single_line_error(self):
        def duplicate_family(roster):
            roster["entries"][1]["family"] = roster["entries"][0]["family"]

        result = self.run_with_roster_mutation(duplicate_family)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "Error: references/spark-section-roster.json: missing or invalid "
            "(entries[1].family must be unique)\n",
        )

    def test_roster_rejects_non_string_alternate_with_single_line_error(self):
        def add_non_string_alternate(roster):
            roster["entries"][0]["alternates"].append(7)

        result = self.run_with_roster_mutation(add_non_string_alternate)

        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr,
            "Error: references/spark-section-roster.json: missing or invalid "
            "(entries[0].alternates entries must be strings)\n",
        )

    def test_empty_object_and_empty_entries_rosters_have_a_single_line_error(self):
        for body in ({}, {"entries": []}):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temp:
                copied = Path(temp) / "next-theme-figma"
                shutil.copytree(FIGMA, copied)
                roster = copied / "references" / "spark-section-roster.json"
                roster.write_text(json.dumps(body), encoding="utf-8")
                result = subprocess.run(
                    ["node", str(copied / "scripts" / "theme-figma.js"),
                     "infer-section", "hero1-desktop"],
                    text=True,
                    capture_output=True,
                )
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(result.stderr.rstrip().splitlines()), 1)
                self.assertIn(
                    "references/spark-section-roster.json: missing or invalid",
                    result.stderr,
                )
                self.assertNotIn("TypeError", result.stderr)

    def test_shipped_alternate_resolves_to_featured_family(self):
        result = self.infer("section_featured_product-mobile")
        self.assertEqual(result["family"], "featured")
        self.assertEqual(result["spark_section"], "section_featured_product")
        self.assertEqual(result["spark_status"], "shipped")


if __name__ == "__main__":
    unittest.main()
