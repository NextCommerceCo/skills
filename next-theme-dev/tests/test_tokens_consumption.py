"""Verify lexical presence of design-token consumption guidance."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

HANDOFF_SECTION = re.compile(
    r"^## Implementation-Handoff Entry Contract[ \t]*\n"
    r".*?(?=^##[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
TOKENS_SECTION = re.compile(
    r"^### Design Tokens From tokens[.]json[ \t]*\n"
    r".*?(?=^#{1,3}[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
ROW_NINE = re.compile(
    r"^\| 9 \| `tokens[.]json` \|.*\|[ \t]*$",
    re.MULTILINE,
)


def assert_tokens(test_case, markdown):
    handoff = HANDOFF_SECTION.search(markdown)
    test_case.assertIsNotNone(
        handoff,
        "missing Implementation-Handoff Entry Contract",
    )
    handoff = handoff.group(0)

    tokens = TOKENS_SECTION.search(handoff)
    test_case.assertIsNotNone(
        tokens,
        "missing H3 section 'Design Tokens From tokens.json'",
    )
    tokens = tokens.group(0)

    for kind in (
        "theme-setting",
        "css-custom-property",
        "one-off",
        "unmapped",
    ):
        test_case.assertRegex(
            tokens,
            re.compile(rf"^- `{re.escape(kind)}` ->", re.MULTILINE),
            "missing target.kind bullet {!r}".format(kind),
        )

    test_case.assertIn(
        "{% if settings.<setting_id> %}--<css_var>: "
        "{{ settings.<setting_id> }};{% endif %}",
        tokens,
    )
    test_case.assertIn(
        "`settings.*` must never appear as a filter argument; it causes a 500 response.",
        tokens,
    )

    for prefix in (
        "Tokens mapped to settings:",
        "Tokens as custom properties:",
        "Unmapped tokens:",
    ):
        test_case.assertIn(prefix, tokens)
        test_case.assertIn(prefix + " none", tokens)

    test_case.assertRegex(
        handoff,
        ROW_NINE,
        "reading-order row 9 must be tokens.json",
    )


class TokensConsumptionTest(unittest.TestCase):
    def setUp(self):
        self.markdown = SKILL.read_text(encoding="utf-8")

    def test_contract(self):
        assert_tokens(self, self.markdown)

    def test_heading(self):
        fixture = self.markdown.replace(
            "### Design Tokens From tokens.json",
            "Design token routing",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaisesRegex(AssertionError, "missing H3 section"):
            assert_tokens(self, fixture)

    def test_kind(self):
        fixture = self.markdown.replace(
            "- `unmapped` -> build nothing",
            "- Unmapped -> build nothing",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaisesRegex(AssertionError, "target.kind bullet"):
            assert_tokens(self, fixture)

    def test_dtl(self):
        fixture = self.markdown.replace(
            "{% if settings.<setting_id> %}",
            "{% if <setting_id> %}",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_tokens(self, fixture)

    def test_filter(self):
        fixture = self.markdown.replace(
            "`settings.*` must never appear as a filter argument; it causes a 500 response.",
            "Avoid filter arguments here.",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_tokens(self, fixture)

    def test_handback(self):
        fixture = self.markdown.replace(
            "`Unmapped tokens: none`",
            "`Unmapped tokens: empty`",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_tokens(self, fixture)

    def test_row(self):
        fixture = self.markdown.replace(
            "| 9 | `tokens.json` |",
            "| 9 | `design-tokens.json` |",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaisesRegex(AssertionError, "row 9"):
            assert_tokens(self, fixture)


if __name__ == "__main__":
    unittest.main()
