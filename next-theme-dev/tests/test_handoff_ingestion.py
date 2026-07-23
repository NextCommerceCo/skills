"""Verify the implementation-handoff package ingestion contract."""

import re
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

HANDOFF_HEADING = "Implementation-Handoff Entry Contract"
PACKAGE_FILES = (
    "figma-handoff.json",
    "routes.json",
    "sections.json",
    "assets.json",
    "spark-divergence-ledger.json",
    "viewport-coverage.json",
    "validation-checklist.md",
    "notes.md",
)
HANDOFF_SECTION = re.compile(
    r"^## Implementation-Handoff Entry Contract[ \t]*\n"
    r".*?(?=^##[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)


def _handoff_section(test_case, markdown):
    match = HANDOFF_SECTION.search(markdown)
    test_case.assertIsNotNone(
        match,
        "missing H2 section {!r}".format(HANDOFF_HEADING),
    )
    return match.group(0)


def assert_handoff_ingestion(test_case, markdown):
    section = _handoff_section(test_case, markdown)

    for filename in PACKAGE_FILES:
        exact_filename = re.compile(
            r"(?<![A-Za-z0-9_.-]){}(?![A-Za-z0-9_.-])".format(
                re.escape(filename)
            )
        )
        test_case.assertRegex(
            section,
            exact_filename,
            "missing package manifest filename {!r}".format(filename),
        )

    test_case.assertIn(
        "validate-package",
        section,
        "Implementation-Handoff Entry Contract must name validate-package",
    )

    test_case.assertRegex(
        section,
        re.compile(r"\bHARD STOP\b", re.IGNORECASE),
        "missing stop condition: HARD STOP marker",
    )
    test_case.assertRegex(
        section,
        re.compile(
            r"Do not re-infer the design from the\s+Figma source",
            re.IGNORECASE,
        ),
        "missing stop condition: do not re-infer from the Figma source",
    )

    test_case.assertRegex(
        section,
        re.compile(
            r"figma-handoff\.json.{0,200}"
            r"confirm its\s+`mode`\s+is exactly\s+"
            r"`implementation-handoff`",
            re.DOTALL,
        ),
        "figma-handoff.json must be checked for mode "
        "'implementation-handoff'",
    )


def _transform_handoff_section(test_case, markdown, transform):
    match = HANDOFF_SECTION.search(markdown)
    test_case.assertIsNotNone(match)
    section = match.group(0)
    transformed = transform(section)
    test_case.assertNotEqual(section, transformed)
    return markdown[:match.start()] + transformed + markdown[match.end():]


class HandoffIngestionTest(unittest.TestCase):
    def setUp(self):
        self.markdown = SKILL.read_text(encoding="utf-8")

    def test_real_skill_has_complete_handoff_ingestion_contract(self):
        assert_handoff_ingestion(self, self.markdown)

    def test_rejects_missing_spark_divergence_ledger(self):
        fixture = _transform_handoff_section(
            self,
            self.markdown,
            lambda section: section.replace(
                "spark-divergence-ledger.json",
                "spark-divergence-log.json",
            ),
        )

        with self.assertRaisesRegex(
            AssertionError, "spark-divergence-ledger[.]json"
        ):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_missing_stop_condition_sentence(self):
        def remove_stop_condition(section):
            transformed, replacements = re.subn(
                r"Do not re-infer the design from the\s+"
                r"Figma source as a fallback[.]",
                "",
                section,
            )
            self.assertEqual(replacements, 1)
            return transformed

        fixture = _transform_handoff_section(
            self,
            self.markdown,
            remove_stop_condition,
        )

        with self.assertRaisesRegex(AssertionError, "stop condition"):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_missing_validate_package_gate(self):
        fixture = _transform_handoff_section(
            self,
            self.markdown,
            lambda section: section.replace(
                "validate-package",
                "validate-handoff",
            ),
        )

        with self.assertRaisesRegex(AssertionError, "validate-package"):
            assert_handoff_ingestion(self, fixture)


if __name__ == "__main__":
    unittest.main()
