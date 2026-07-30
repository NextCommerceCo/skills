"""Regression coverage for the July 2026 merchant-session findings."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
ACTIVE_PUBLISH_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "active-theme-publish-and-qa.md"
)


class ThemeToolingReportGuidanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")
        cls.active_publish = ACTIVE_PUBLISH_REFERENCE.read_text(encoding="utf-8")

    def test_product_template_recipe_uses_template_field_not_url_slug(self):
        recipe = re.search(
            r"^### Add a Custom Product Template\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(recipe)
        recipe = recipe.group(1)

        self.assertIn(
            "templates/catalogue/product.<template-key>.html", recipe
        )
        self.assertNotIn("product.{slug}.html", recipe)
        self.assertRegex(recipe, r"product URL slug does\s+not select")
        self.assertRegex(recipe, r"product's `template` field to `<template-key>`")
        self.assertIn('{% extends "layouts/base.html" %}', recipe)
        self.assertIn("silently falls back", recipe)
        self.assertIn("X-Theme-Template-Candidates", recipe)
        self.assertIn("X-Theme-Template", recipe)

    def test_cache_guidance_distinguishes_page_and_template_freshness(self):
        self.assertRegex(
            self.markdown,
            r"network domain.*bypasses full-page caching, but that alone does "
            r"not guarantee template-cache freshness",
        )
        self.assertIn("X-Theme-Revision", self.markdown)
        self.assertIn("X-Theme-Cache: bypass", self.markdown)
        self.assertNotIn("Template changes via ntk automatically bust", self.markdown)

    def test_preview_rules(self):
        self.assertIn("/?deactivate-theme=true", self.markdown)
        self.assertIn("a plain URL does not exit preview", self.markdown)
        self.assertIn("@media (hover: hover)", self.markdown)
        self.assertRegex(
            self.markdown,
            r"Independently injected global components must be wrapped in "
            r"their own named blocks",
        )

    def test_theme_kit_1_2_guidance_uses_the_released_surface(self):
        self.assertIn("NEXT Theme Kit 1.2.0", self.markdown)
        self.assertIn("NTK_APIKEY", self.markdown)
        self.assertIn(
            "ntk push templates/catalogue/product.<template-key>.html",
            self.markdown,
        )
        for unreleased_surface in (
            "ntk validate",
            "ntk capture",
            "--json",
            "--quiet",
            "--no-progress",
            "partial_failure",
        ):
            with self.subTest(unreleased_surface=unreleased_surface):
                self.assertNotIn(unreleased_surface, self.markdown)

    def test_preflight_identifies_the_resolved_cli_not_system_python(self):
        self.assertIn('NTK_PATH="$(command -v ntk)"', self.markdown)
        self.assertIn('"$NTK_PATH" --help', self.markdown)
        self.assertNotIn("python3 -m pip show next-theme-kit", self.markdown)
        self.assertIn("pipx, uv, and system Python", self.markdown)

    def test_active_theme_publish_contract_is_bounded_and_recoverable(self):
        self.assertIn(
            "references/active-theme-publish-and-qa.md", self.markdown
        )
        for required in (
            "printed upload count",
            "rollback source",
            "Additive assets",
            "Route entry templates last",
            "configs/settings_data.json",
            "representative unaffected",
            "X-Theme-Revision",
            "explicit approval",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.active_publish)
        self.assertRegex(self.active_publish, r"not an atomic\s+deployment")

    def test_screenshot_guidance_keeps_real_desktop_and_mobile_pngs(self):
        self.assertIn("desktop at\n1440px and mobile at 390px", self.markdown)
        self.assertRegex(
            self.markdown,
            r"DOM\s+metrics can supplement screenshots but never replace",
        )

    def test_screenshot_fallback_does_not_add_a_capture_platform(self):
        for required in (
            "already available to the current agent",
            "existing local browser",
            "Ask the operator to capture",
            "accepted visual gap",
            "Do not install or bundle Playwright",
            "Do not create or depend on a managed",
            "capture tool or manual owner",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.active_publish)

    def test_css_override_triage_checks_rendered_causes_before_escalating(self):
        for required in (
            "computed styles",
            "ancestor opacity",
            "::before",
            "::after",
            "::marker",
            "direct child selector",
            "muted color token",
            "starter-theme or platform defect",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.markdown)


if __name__ == "__main__":
    unittest.main()
