"""Verify lexical presence of Spark section roster routing guidance."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

HANDOFF_SECTION = re.compile(
    r"^## Implementation-Handoff Entry Contract[ \t]*\n"
    r".*?(?=^##[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
ROUTING_SECTION = re.compile(
    r"^### Spark Section Routing By `roster_status`[ \t]*\n"
    r".*?(?=^#{1,3}[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
ROW_THREE = re.compile(
    r"^\| 3 \| `sections[.]json` \|.*`roster_status`.*\|[ \t]*$",
    re.MULTILINE,
)


def remove_bullet(markdown, marker):
    return re.sub(
        rf"^- `{re.escape(marker)}`.*?(?=^- `|^If `|^\*\*Handback:|\Z)",
        "",
        markdown,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )


def remove_paragraph(markdown, opening):
    return re.sub(
        rf"^{re.escape(opening)}.*?(?=\n\n)",
        "",
        markdown,
        count=1,
        flags=re.MULTILINE | re.DOTALL,
    )


def assert_section_roster_routing(test_case, markdown):
    handoff = HANDOFF_SECTION.search(markdown)
    test_case.assertIsNotNone(
        handoff,
        "missing Implementation-Handoff Entry Contract",
    )
    handoff = handoff.group(0)

    routing = ROUTING_SECTION.search(handoff)
    test_case.assertIsNotNone(
        routing,
        "missing H3 section 'Spark Section Routing By `roster_status`'",
    )
    routing = routing.group(0)

    bullets = {}
    for status in ("shipped", "unshipped", "chrome", "unmapped"):
        match = re.search(
            rf"^- `{status}`.*?(?=^- `|\n\n|\Z)",
            routing,
            re.MULTILINE | re.DOTALL,
        )
        test_case.assertIsNotNone(match, f"missing {status} routing bullet")
        bullets[status] = match.group(0)

    test_case.assertIn("partials/<spark_section>.html", bullets["shipped"])
    test_case.assertIn("Do not create a parallel partial", bullets["shipped"])
    test_case.assertIn("docs/section-specs/", bullets["unshipped"])
    test_case.assertIn("partials/header.html", bullets["chrome"])
    test_case.assertIn("partials/footer.html", bullets["chrome"])
    test_case.assertIn("candidate roster addition", bullets["unmapped"])
    test_case.assertIn("surface the contradiction to the operator", routing)
    test_case.assertRegex(routing, r"(?s)missing or empty.*?infer-section")

    for prefix in (
        "Unshipped Spark sections built:",
        "Unmapped sections:",
        "Sections routed without roster_status:",
    ):
        test_case.assertIn(prefix, routing)

    test_case.assertIn("Unshipped Spark sections built: none", routing)
    test_case.assertIn("Unmapped sections: none", routing)
    test_case.assertIn("Sections routed without roster_status: none", routing)

    test_case.assertRegex(
        handoff,
        ROW_THREE,
        "reading-order row 3 must mention roster_status",
    )


class SectionRosterRoutingTest(unittest.TestCase):
    def setUp(self):
        self.markdown = SKILL.read_text(encoding="utf-8")

    def test_real_skill_has_section_roster_routing(self):
        assert_section_roster_routing(self, self.markdown)

    def test_rejects_missing_routing_heading(self):
        fixture = self.markdown.replace(
            "### Spark Section Routing By `roster_status`",
            "Spark section routing",
            1,
        )
        self.assertNotEqual(fixture, self.markdown)

        with self.assertRaisesRegex(AssertionError, "missing H3 section"):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_shipped_bullet(self):
        fixture = remove_bullet(self.markdown, "shipped")
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_unmapped_bullet(self):
        fixture = remove_bullet(self.markdown, "unmapped")
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_unmapped_none_form(self):
        fixture = self.markdown.replace(
            "`Unmapped sections: none`", "`Unmapped sections: nothing`"
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaisesRegex(AssertionError, "Unmapped sections: none"):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_contradiction_sentence(self):
        fixture = remove_paragraph(
            self.markdown,
            "If `roster_status` contradicts the checked-out theme",
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_status_rule(self):
        fixture = remove_paragraph(
            self.markdown,
            "If `roster_status` is missing or empty on a Spark package section",
        )
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_section_roster_routing(self, fixture)

    def test_rejects_missing_unmapped_handback_prefix(self):
        fixture = self.markdown.replace("Unmapped sections:", "Other sections:")
        self.assertNotEqual(fixture, self.markdown)
        with self.assertRaises(AssertionError):
            assert_section_roster_routing(self, fixture)


if __name__ == "__main__":
    unittest.main()
