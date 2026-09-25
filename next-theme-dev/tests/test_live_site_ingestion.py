"""Lexical checks for the live-site subsection of the handoff entry contract.

Parallel to test_handoff_ingestion.py, which pins the Figma subsection. Like
that test, this one proves the gates are written down, not that the prose is
semantically correct.
"""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

LIVE_FILES = (
    "design-handoff.json",
    "captures.json",
    "routes.json",
    "sections.json",
    "assets.json",
    "platform-divergence-ledger.json",
    "viewport-coverage.json",
    "geometry.json",
    "copy.json",
    "observed-styles.json",
    "behaviors.json",
    "validation-checklist.md",
    "notes.md",
)
HANDOFF_SECTION = re.compile(
    r"^## Implementation-Handoff Entry Contract[ \t]*\n.*?(?=^##[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
LIVE_SECTION = re.compile(
    r"^### Live-Site Package Entry \(`design-handoff\.json`\)[ \t]*\n.*?(?=^#{1,3}[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
ROW = re.compile(r"^\|[ \t]*(\d+)[ \t]*\|[ \t]*`([^`]+)`[ \t]*\|.*\|[ \t]*$")


def live_section(test_case, markdown):
    handoff = HANDOFF_SECTION.search(HTML_COMMENT.sub("", markdown))
    test_case.assertIsNotNone(handoff, "missing Implementation-Handoff Entry Contract")
    live = LIVE_SECTION.search(handoff.group(0))
    test_case.assertIsNotNone(live, "missing H3 'Live-Site Package Entry (`design-handoff.json`)'")
    return handoff.group(0), live.group(0)


def reading_order(section):
    lines = section.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("| Order |"):
            rows = []
            for row in lines[index + 2:]:
                match = ROW.match(row)
                if not match:
                    break
                rows.append((int(match.group(1)), match.group(2)))
            return rows
    return []


def assert_live_ingestion(test_case, markdown):
    handoff, live = live_section(test_case, markdown)
    rows = reading_order(live)
    test_case.assertEqual(
        rows, list(enumerate(LIVE_FILES, start=1)),
        "live-site reading order must list the {} package files in order".format(len(LIVE_FILES)),
    )
    test_case.assertIn("scripts/design-handoff.py validate", live, "live-site gate must name its validator")
    test_case.assertRegex(live, r"\*\*HARD STOP \(live-site\):\*\*", "missing live-site HARD STOP")
    test_case.assertRegex(
        live, r"Never capture the source site again, re-read\s+its HTML, or re-derive the design",
        "missing stop condition: no fallback to the source",
    )
    test_case.assertRegex(live, r"never run the Figma\s+`theme-figma\.js` gate on a live-site package")
    test_case.assertIn("`intake-only`", live)
    test_case.assertIn("Blocked sections: none", live)
    test_case.assertIn("references/design-handoff-format.md", live)
    for surfaced in ("`omit`", "`unresolved`", "without a\ntreatment", "unresolved divergence-ledger entries"):
        test_case.assertIn(surfaced, live, "missing surfaced item {!r}".format(surfaced))
    test_case.assertRegex(
        handoff,
        r"Dispatch on the entry file in the\s+package directory: `figma-handoff\.json` runs the Figma gate",
        "the contract must dispatch on the entry file",
    )
    test_case.assertRegex(handoff, r"`design-handoff\.json` runs the \*\*Live-Site Package Entry\*\* subsection")
    test_case.assertIn("Neither path falls back to the other", handoff)


class LiveSiteIngestionTest(unittest.TestCase):
    def setUp(self):
        self.markdown = SKILL.read_text(encoding="utf-8")

    def test_real_skill_has_the_live_site_subsection(self):
        assert_live_ingestion(self, self.markdown)

    def test_rejects_a_deleted_reading_order_row(self):
        broken = re.sub(r"^\| 2 \| `captures\.json` \|.*\n", "", self.markdown, count=1, flags=re.MULTILINE)
        self.assertNotEqual(broken, self.markdown)
        with self.assertRaisesRegex(AssertionError, "live-site reading order"):
            assert_live_ingestion(self, broken)

    def test_rejects_a_row_hidden_in_an_html_comment(self):
        broken = re.sub(r"^(\| 10 \| `observed-styles\.json` \|.*)$", r"<!-- \1 -->", self.markdown,
                        count=1, flags=re.MULTILINE)
        self.assertNotEqual(broken, self.markdown)
        with self.assertRaisesRegex(AssertionError, "live-site reading order"):
            assert_live_ingestion(self, broken)

    def test_rejects_a_missing_hard_stop(self):
        broken = self.markdown.replace("**HARD STOP (live-site):**", "**Note:**")
        with self.assertRaisesRegex(AssertionError, "HARD STOP"):
            assert_live_ingestion(self, broken)

    def test_rejects_a_missing_dispatch(self):
        broken = self.markdown.replace("Dispatch on the entry file", "Look at the files")
        with self.assertRaisesRegex(AssertionError, "dispatch"):
            assert_live_ingestion(self, broken)

    def test_live_subsection_does_not_repeat_the_figma_mode_gate(self):
        # test_handoff_ingestion removes the Figma mode sentence and expects the
        # gate to disappear; a second copy here would hide that regression.
        _, live = live_section(self, self.markdown)
        self.assertNotRegex(live, r"confirm its\s+`mode`\s+is exactly")
        self.assertNotRegex(live, r"Do not re-infer the design from the\s+Figma source")


if __name__ == "__main__":
    unittest.main()
