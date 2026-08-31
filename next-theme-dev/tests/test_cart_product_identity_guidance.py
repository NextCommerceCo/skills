"""Pin the parent/variant cart identity contract in public theme guidance."""

import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class TestCartProductIdentityGuidance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")

    def test_pdp_uses_child_with_standalone_fallback(self):
        self.assertIn(
            "{% firstof product.children.first.pk product.pk as atc_pk %}",
            self.markdown,
        )
        self.assertIn("{% url 'cart:add' pk=atc_pk %}", self.markdown)
        self.assertNotIn("{% url 'cart:add' pk=product.pk %}", self.markdown)

    def test_variant_selection_wins_over_first_child_default(self):
        self.assertIn("submit the selected child's PK", self.markdown)
        self.assertIn("keep the submitted PK synchronized to the selected child", self.markdown)

    def test_form_and_graphql_share_the_identity_rule(self):
        self.assertIn(
            "The identity\n  rule applies to form posts and GraphQL `addCartLines`",
            self.markdown,
        )


if __name__ == "__main__":
    unittest.main()
