"""Regression coverage for the Figma failure-mode and screenshot gates."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"


class FailureModeRunbookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.markdown = SKILL.read_text(encoding="utf-8")

    def test_large_empty_metadata_is_treated_as_tool_failure(self):
        self.assertRegex(
            self.markdown,
            re.compile(
                r"metadata for a large frame is unexpectedly empty.*"
                r"truncation/tool failure, not as\s+an empty design",
                re.DOTALL,
            ),
        )
        for fallback in ("targeted section node URLs", "full-frame render", ".fig"):
            self.assertIn(fallback, self.markdown)

    def test_export_and_selection_retries_are_bounded(self):
        self.assertIn("Export exact asset nodes in bounded batches", self.markdown)
        self.assertIn("Retry selection synchronization at most three times", self.markdown)

    def test_screenshot_preflight_is_a_hard_visual_qa_gate(self):
        self.assertRegex(
            self.markdown,
            r"both a Figma reference PNG and a\s+storefront preview PNG",
        )
        self.assertRegex(
            self.markdown,
            r"stop\s+or record an explicit `accepted-gap`",
        )
        self.assertIn("DOM metrics alone are not visual QA", self.markdown)
        self.assertRegex(self.markdown, r"desktop is\s+1440px and mobile is 390px")
        self.assertIn("per-route desktop/mobile coverage gate", self.markdown)
        self.assertIn(
            "Record tablet coverage separately when the design supplies a tablet frame",
            self.markdown,
        )


if __name__ == "__main__":
    unittest.main()
