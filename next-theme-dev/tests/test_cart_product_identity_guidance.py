"""Pin the parent/variant cart identity contract in public theme guidance."""

import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class TestCartProductIdentityGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")
        cls.known_gotchas = cls._section("## Known Gotchas", "## Dashboard-Theme Bridge")
        cls.pdp = cls._section("### Custom Spark PDP Redesigns", "### Update Product Media From Figma")
        cls.side_cart = cls._section("### Side Cart Customization", "## Deployment Workflow")

    @classmethod
    def _section(cls, start, end):
        return cls.markdown.split(start, 1)[1].split(end, 1)[0]

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
        self.assertIn("preserve `SparkVariantState.updateFormAction()`", guidance)
        self.assertIn("form action follows the selected child", guidance)
        self.assertIn("reads the updated form action", guidance)

    def test_form_and_graphql_share_the_identity_rule(self):
        guidance = self._normalized(self.side_cart)
        self.assertIn("same identity rule applies to form posts and GraphQL", guidance)
        self.assertIn("`addCartLines`", guidance)
        self.assertIn("require `result.success`", guidance)
        self.assertIn("compare the returned or re-fetched cart", guidance)
        self.assertIn("total quantity must increase by the requested amount", guidance)


if __name__ == "__main__":
    unittest.main()
