"""Pin the parent/variant cart identity contract in public theme guidance."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


def extract_section(markdown, start, end):
    matches = {}
    for label, anchor in (("start", start), ("end", end)):
        matches[label] = list(
            re.finditer(rf"^{re.escape(anchor)}$", markdown, flags=re.MULTILINE)
        )
        if len(matches[label]) != 1:
            raise AssertionError(
                f"expected exactly one {label} heading {anchor!r}, "
                f"found {len(matches[label])}"
            )

    start_index = matches["start"][0].end()
    end_index = matches["end"][0].start()
    if end_index <= start_index:
        raise AssertionError(f"end heading {end!r} must follow start heading {start!r}")
    return markdown[start_index:end_index]


class TestCartProductIdentityGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")
        cls.known_gotchas = cls._section("## Known Gotchas", "## Dashboard-Theme Bridge")
        cls.pdp = cls._section("### Custom Spark PDP Redesigns", "### Update Product Media From Figma")
        cls.side_cart = cls._section("### Side Cart Customization", "## Deployment Workflow")

    @classmethod
    def _section(cls, start, end):
        return extract_section(cls.markdown, start, end)

    @staticmethod
    def _normalized(text):
        return " ".join(text.split())

    def test_known_gotcha_covers_parent_and_standalone_products(self):
        guidance = self._normalized(self.known_gotchas)
        self.assertIn("Resolve purchasable cart identity", guidance)
        self.assertIn(
            "{% firstof settings.gift_product.children.first.pk "
            "settings.gift_product.pk as gift_product_pk %}",
            guidance,
        )
        self.assertIn("resolves `firstof` candidates left-to-right", guidance)
        self.assertIn("standalone PK is the fallback", guidance)

    def test_section_extraction_fails_loudly_for_missing_anchors(self):
        cases = {
            "start": ("end\n", "start", "end"),
            "end": ("start\n", "start", "end"),
        }
        for label, arguments in cases.items():
            with self.subTest(label=label):
                with self.assertRaisesRegex(AssertionError, f"{label} heading"):
                    extract_section(*arguments)

    def test_pdp_wrapper_and_form_share_the_initial_identity(self):
        guidance = self._normalized(self.pdp)
        self.assertIn(
            "{% firstof product.children.first.pk product.pk as atc_pk %}",
            guidance,
        )
        self.assertIn('product-id="{{ atc_pk }}"', guidance)
        self.assertIn("{% url 'cart:add' pk=atc_pk %}", guidance)
        self.assertNotIn("{% url 'cart:add' pk=product.pk %}", guidance)

    def test_variant_selection_updates_the_form_identity(self):
        guidance = self._normalized(self.pdp)
        self.assertIn("`SparkVariantState.updateFormAction()`", guidance)
        self.assertIn("spark-variant-state.js#L75-L80", guidance)
        self.assertIn("form action follows the selected child", guidance)
        self.assertIn("gives that form action precedence", guidance)
        self.assertIn("spark-add-to-cart.js#L132-L145", guidance)

    def test_form_and_graphql_share_the_identity_rule(self):
        guidance = self._normalized(self.side_cart)
        self.assertIn("same identity rule applies to form posts and GraphQL", guidance)
        self.assertIn("`addCartLines`", guidance)
        self.assertIn("require `result.success`", guidance)
        self.assertIn("compare the returned or re-fetched cart", guidance)
        self.assertIn("quantities across every line", guidance)
        self.assertIn("aggregate must increase by the requested amount", guidance)


if __name__ == "__main__":
    unittest.main()
