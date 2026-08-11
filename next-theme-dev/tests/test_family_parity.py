"""Contract coverage for Spark/Intro Bootstrap family parity guidance."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
INTRO_REFERENCE = (
    Path(__file__).resolve().parents[1]
    / "references"
    / "intro-preservation-contract.md"
)


def _h2(markdown, heading):
    match = re.search(
        r"^## {}[ \t]*\n(.*?)(?=^##[ \t]+|\Z)".format(
            re.escape(heading)
        ),
        markdown,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise AssertionError("missing H2 section {!r}".format(heading))
    return match.group(1)


class FamilyParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")
        cls.intro_reference = INTRO_REFERENCE.read_text(encoding="utf-8")

    def test_greenfield_order(self):
        greenfield = _h2(self.markdown, "From an Empty Store (Greenfield Path)")
        headings = re.findall(r"^### ([^\n]+)$", greenfield, re.MULTILINE)

        self.assertGreaterEqual(len(headings), 2)
        self.assertEqual("1. Identify and Choose the Theme Family", headings[0])
        self.assertEqual("2. Acquire the Selected Theme", headings[1])
        self.assertLess(
            greenfield.index("Identify and Choose the Theme Family"),
            greenfield.index("git clone https://github.com/NextCommerceCo/spark.git"),
        )
        self.assertIn("Intro Bootstrap branch", greenfield)
        self.assertIn("ZIP upload and\ndashboard install", greenfield)
        self.assertIn("Do not invent or recommend an upstream git URL", greenfield)

    def test_handoff_identity_is_the_recorded_family_identification(self):
        architecture = _h2(self.markdown, "Architecture")
        family_section = re.search(
            r"^### Identify the Theme Family First\n(.*?)(?=^### |\Z)",
            architecture,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(family_section)
        family_section = family_section.group(1)

        self.assertIn("target.theme_family", family_section)
        self.assertIn("target.runtime_contract", family_section)
        self.assertIn("recorded outcome of this identification", family_section)

    def test_intro_runtime_is_not_silently_migrated_to_spark(self):
        for required in (
            "Never recommend replacing a working Intro Bootstrap runtime",
            "jQuery lifecycle",
            "SCSS pipeline",
            "GraphQL fetch client",
            "Spark's Web\nComponents or Tailwind stack",
            "separate, owner-approved project",
            "not a byproduct",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.markdown)

    def test_cart_line_schema_uses_properties_not_removed_attributes(self):
        cart = re.search(
            r"^### Cart and User State.*?\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        side_cart = re.search(
            r"^### Side Cart Customization\n(.*?)(?=^### |\Z)",
            self.markdown,
            re.MULTILINE | re.DOTALL,
        )
        self.assertIsNotNone(cart)
        self.assertIsNotNone(side_cart)
        guidance = cart.group(1) + side_cart.group(1)

        self.assertGreaterEqual(guidance.count("properties { key value }"), 2)
        self.assertIn("former CartLineNode `attributes`\nfield was removed", guidance)
        self.assertNotIn("attributes {", guidance)
        self.assertRegex(guidance, r"create, update, and remove")

    def test_family_specific_template_preservation_is_explicit(self):
        self.assertIn(
            "**Spark:** prefer extending/overriding the theme's template blocks",
            self.markdown,
        )
        self.assertIn(
            "**Intro Bootstrap:** extract and preserve the working commerce core verbatim",
            self.markdown,
        )

    def test_intro_preservation_reference_exists_and_is_linked_at_point_of_use(self):
        self.assertTrue(INTRO_REFERENCE.is_file())
        self.assertIn(
            "read `references/intro-preservation-contract.md` completely before",
            self.markdown,
        )
        self.assertIn(
            "See `references/intro-preservation-contract.md`",
            self.markdown,
        )

        for required in (
            "## SCSS Build and Compiled CSS",
            "## Script Order Is a Runtime Contract",
            "show.cart",
            "hide.cart",
            "storefront_cart_id",
            "a single delegated `change` listener on\n  `#cart-modal`",
            "## `cart:add` Form and Buy-Box Contract",
            "{{ settings.<name> }}",
            "## Typography Inheritance",
            "## Selector-Leak Preflight",
            "## Overridable and Unblockable Layout Surfaces",
            "The 22 overridable blocks",
            "nested second `extrastyles` block",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.intro_reference)

        self.assertGreaterEqual(
            self.intro_reference.count("Intro Bootstrap 1.2.0,"),
            25,
        )


if __name__ == "__main__":
    unittest.main()
