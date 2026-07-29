"""Regression coverage for the July 2026 merchant-session findings."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class ThemeToolingReportGuidanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")

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

    def test_screenshot_guidance_keeps_real_desktop_and_mobile_pngs(self):
        self.assertIn("desktop at\n1440px and mobile at 390px", self.markdown)
        self.assertRegex(
            self.markdown,
            r"DOM\s+metrics can supplement screenshots but never replace",
        )


if __name__ == "__main__":
    unittest.main()
