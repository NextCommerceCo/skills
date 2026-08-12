"""Regression coverage for the July 2026 merchant-session findings."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
README = Path(__file__).resolve().parents[1] / "README.md"
ACTIVE_PUBLISH_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "active-theme-publish-and-qa.md"
)


class ThemeToolingReportGuidanceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")
        cls.readme = README.read_text(encoding="utf-8")
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
        self.assertIn('data-template="product.<template-key>"', recipe)
        self.assertIn("confirm that exact attribute", recipe)
        self.assertNotIn("X-Theme-Template-Candidates", recipe)
        self.assertNotIn("X-Theme-Template", recipe)

    def test_staged_rollout_is_labeled_observed_and_uses_marker_detection(self):
        recipe = re.search(
            r"^### Add a Custom Product Template\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(recipe)
        recipe = recipe.group(1)

        self.assertIn("Observed staged-rollout pattern", recipe)
        self.assertRegex(
            recipe,
            r"`missing template ⇒ default template`\.\s+"
            r"> \*\*Observed behavior, not platform-documented:\*\* Verify this "
            r"fallback on\s+> the target store before relying on it\.",
        )
        self.assertNotRegex(recipe, r"`[^`\n]*platform-documented[^`\n]*`")
        self.assertIn("no product-field change is needed at cutover", recipe)
        self.assertIn("DOM marker", recipe)

    def test_cache_guidance_uses_network_domain_as_canonical_verification(self):
        self.assertIn(
            "Always develop, preview, debug, and verify on the `.29next.store` "
            "network domain",
            self.markdown,
        )
        self.assertIn(
            "Do not use a mapped public storefront domain to decide whether a "
            "change landed",
            self.markdown,
        )
        self.assertIn(
            "confirm an exact changed CSS or JavaScript token", self.markdown
        )
        self.assertIn(
            "compare the downloaded and local file checksums", self.markdown
        )
        combined = self.markdown + "\n" + self.active_publish
        self.assertNotIn("skip_cache", combined)
        self.assertNotIn("X-Theme-", combined)

    def test_cache_turnover_requires_repeated_cookie_less_marker_checks(self):
        for required in (
            "bypasses the 5-minute mapped-domain edge cache layer, "
            "not the page cache itself",
            "Page-cache turnover is per-edge and non-atomic on any domain",
            "responses for\n   several minutes",
            "Sample repeatedly; never judge cache turnover from one fetch",
            "cookie-less requests against the `.29next.store` network domain",
            "exact template-specific DOM marker",
            "supplement these screenshots; they do not replace them",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.markdown)
        self.assertNotIn("bypasses caching", self.markdown)
        self.assertNotIn("roughly", self.markdown)
        self.assertNotIn("5 min on mapped domains", self.markdown)

    def test_crlf_gotcha_and_minified_html_counting_are_explicit(self):
        self.assertRegex(
            self.markdown,
            r"\| \*\*Shared\*\* \| \*\*CRLF line endings\*\* \|.*"
            r"detect line endings first and preserve or normalize them deliberately",
        )
        self.assertIn("`grep -c` counts lines, not\n   occurrences", self.markdown)
        self.assertIn(
            "grep -o 'data-template=\"product.<template-key>\"' served.html | wc -l",
            self.markdown,
        )

    def test_typography_preflight_resolves_effective_stack_before_styling(self):
        preflight = re.search(
            r"^### Step 1\.25: Effective Typography Preflight\n"
            r"(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(preflight)
        self.assertLess(
            preflight.start(),
            self.markdown.index("### Step 1.5: Figma Fidelity Loop"),
        )
        preflight = preflight.group(1)

        for required in (
            "Before styling any custom template",
            "current theme settings first",
            "then the derived rules in the base layout",
            "store-derived base may hardcode families",
            "Custom templates inherit this effective stack",
            "do not redeclare fonts per node",
            "references/intro-preservation-contract.md",
            "`Typography Inheritance`",
        ):
            with self.subTest(required=required):
                self.assertIn(required, preflight)

    def test_recording_remediation_contract(self):
        fidelity_loop = re.search(
            r"^### Step 1\.5: Figma Fidelity Loop\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(fidelity_loop)
        fidelity_loop = fidelity_loop.group(1)

        self.assertRegex(
            fidelity_loop,
            r"remediation-queue\s+entry with route, section, viewport, severity, "
            r"and mismatch status",
        )
        self.assertIn("same remediation queue as step 6", fidelity_loop)
        self.assertIn(
            "`fix-now`, `intentional-platform-divergence`, or\n"
            "`blocked-input-needed`",
            fidelity_loop,
        )
        self.assertIn(
            "A recording never substitutes for the visual-QA loop",
            fidelity_loop,
        )

    def test_settings_suitability_contract(self):
        settings_design = re.search(
            r"^### Step 3: Settings Schema Design\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(settings_design)
        settings_design = settings_design.group(1)

        self.assertIn("Before hardcoding any copy", settings_design)
        self.assertRegex(
            settings_design,
            r"Merchant-iterable copy such as trust lines, shipping promises, "
            r"legal blocks,\s+and promotional text belongs in "
            r"`settings_schema\.json` and a wired template\s+region from the start",
        )

    def test_related_template_family_is_generated_from_one_source(self):
        family_recipe = re.search(
            r"^### Generate a Family of Related Templates\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(family_recipe)
        family_recipe = family_recipe.group(1)

        self.assertRegex(
            family_recipe,
            r"generate every template from\s+one source: a design-system module "
            r"plus a per-item data dictionary or map",
        )
        self.assertRegex(
            family_recipe,
            r"merchant feedback becomes data edits rather than\s+hand-editing",
        )
        for required in (
            '"overview"',
            '"details"',
            '"headline"',
            '"hero_asset"',
            '"sections"',
            "inside the theme project",
            "`scripts/` folder",
            "every run regenerate every template in the family",
        ):
            with self.subTest(required=required):
                self.assertIn(required, family_recipe)

    def test_theme_family_attribution_and_runtime_contracts_are_explicit(self):
        for required in (
            "compare the exact",
            "current upstream starter",
            "store-local or version-specific",
            "spark-platform.js",
            "<spark-cart-drawer>",
            "<spark-add-to-cart>",
            "<spark-quantity>",
            "spark-preview.js",
            "jQuery loaded before `{% core_js %}`",
            "Custom theme",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.markdown)

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
        self.assertIn(
            "sed -n '/NEXT Theme Kit version/{p;q;}'", self.markdown
        )
        self.assertNotIn("sed -n '1p'", self.markdown)
        self.assertNotIn('"$NTK_PATH" --version', self.markdown)
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
            "served HTML",
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

    def test_plain_language_readme_matches_current_theme_safety_contract(self):
        for required in (
            "Python 3.10 or newer",
            "NEXT Theme Kit 1.2.0",
            "selected remote theme",
            "rollback source",
            "printed upload count",
            "served storefront result",
            "real desktop and mobile screenshots",
            "does not install Playwright",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.readme)


if __name__ == "__main__":
    unittest.main()
