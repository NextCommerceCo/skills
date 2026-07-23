"""Verify lexical/structural presence of the handoff ingestion contract.

This intentionally does not validate the contract's semantics. In particular,
it does not defend against adversarial prose that negates the gates or against
Markdown-rendering edge cases such as H2-lookalike lines inside code fences.
"""

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
HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
READING_ORDER_ROW = re.compile(
    r"^ {0,3}\|[ \t]*(\d+)[ \t]*\|[ \t]*`([^`\r\n]+)`[ \t]*\|.*\|[ \t]*$",
    re.MULTILINE,
)
READING_ORDER_SECTION = re.compile(
    r"^### Prescribed Reading Order[ \t]*\n"
    r".*?(?=^#{1,3}[ \t]+|\Z)",
    re.MULTILINE | re.DOTALL,
)
READING_ORDER_TABLE_HEADER = re.compile(
    r"^ {0,3}\|[ \t]*Order[ \t]*\|.*\|[ \t]*$"
)
MARKDOWN_TABLE_DELIMITER = re.compile(
    r"^ {0,3}\|"
    r"(?:[ \t]*:?-{3,}:?[ \t]*\|)+"
    r"[ \t]*$"
)


def _handoff_section(test_case, markdown):
    uncommented_markdown = HTML_COMMENT.sub("", markdown)
    sections = [
        match.group(0)
        for match in HANDOFF_SECTION.finditer(uncommented_markdown)
    ]
    test_case.assertTrue(
        sections,
        "missing H2 section {!r}".format(HANDOFF_HEADING),
    )
    return "\n".join(sections)


def _without_fenced_code(markdown):
    visible_lines = []
    fence_character = None
    fence_length = 0

    for line in markdown.splitlines():
        if fence_character is None:
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
            if opening:
                fence_character = opening.group(1)[0]
                fence_length = len(opening.group(1))
                continue
            visible_lines.append(line)
            continue

        if re.match(
            r"^ {{0,3}}{}{{{},}}[ \t]*$".format(
                re.escape(fence_character),
                fence_length,
            ),
            line,
        ):
            fence_character = None
            fence_length = 0

    return "\n".join(visible_lines)


def _assert_reading_order(test_case, section):
    visible_section = _without_fenced_code(section)
    reading_order_section = READING_ORDER_SECTION.search(visible_section)
    test_case.assertIsNotNone(
        reading_order_section,
        "missing H3 section 'Prescribed Reading Order'",
    )

    lines = reading_order_section.group(0).splitlines()
    table_start = None
    for line_number in range(len(lines) - 1):
        if (
            READING_ORDER_TABLE_HEADER.fullmatch(lines[line_number])
            and MARKDOWN_TABLE_DELIMITER.fullmatch(lines[line_number + 1])
        ):
            table_start = line_number + 2
            break

    test_case.assertIsNotNone(
        table_start,
        "reading-order table must contain a header row followed by a "
        "delimiter row",
    )

    rows = []
    for line in lines[table_start:]:
        row = READING_ORDER_ROW.fullmatch(line)
        if row is None:
            break
        rows.append(row.groups())

    row_summary = ", ".join(
        "{}:{!r}".format(order, filename) for order, filename in rows
    )
    test_case.assertEqual(
        len(rows),
        len(PACKAGE_FILES),
        "reading-order row/filename problem: table must contain exactly {} "
        "data rows; found {} ({})".format(
            len(PACKAGE_FILES),
            len(rows),
            row_summary or "no data rows",
        ),
    )

    for row_position, ((order, filename), expected_filename) in enumerate(
        zip(rows, PACKAGE_FILES),
        start=1,
    ):
        test_case.assertEqual(
            int(order),
            row_position,
            "reading-order row {} has order {}; expected {}".format(
                row_position,
                order,
                row_position,
            ),
        )
        test_case.assertEqual(
            filename,
            expected_filename,
            "reading-order row {} has filename {!r}; expected {!r}".format(
                row_position,
                filename,
                expected_filename,
            ),
        )


def assert_handoff_ingestion(test_case, markdown):
    section = _handoff_section(test_case, markdown)

    _assert_reading_order(test_case, section)

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

    def test_rejects_validate_package_hidden_in_html_comment(self):
        fixture = _transform_handoff_section(
            self,
            self.markdown,
            lambda section: section.replace(
                "validate-package",
                "validate-handoff",
            )
            + "\n<!-- validate-package -->\n",
        )

        with self.assertRaisesRegex(AssertionError, "validate-package"):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_reading_order_row_hidden_in_html_comment(self):
        def comment_out_routes_row(section):
            transformed, replacements = re.subn(
                r"^(\| 2 \| `routes[.]json` \|.*)$",
                r"<!-- \1 -->",
                section,
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(replacements, 1)
            return transformed

        fixture = _transform_handoff_section(
            self,
            self.markdown,
            comment_out_routes_row,
        )

        with self.assertRaisesRegex(
            AssertionError,
            r"reading-order row/filename problem: table must contain "
            r"exactly 8 data rows; found 1",
        ):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_missing_mode_gate_sentence(self):
        def remove_mode_gate(section):
            transformed, replacements = re.subn(
                r"read\s+`figma-handoff[.]json`\s+and confirm its\s+"
                r"`mode`\s+is exactly\s+`implementation-handoff`[.]",
                "",
                section,
            )
            self.assertEqual(replacements, 1)
            return transformed

        fixture = _transform_handoff_section(
            self,
            self.markdown,
            remove_mode_gate,
        )

        with self.assertRaisesRegex(AssertionError, "mode"):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_swapped_reading_order_rows(self):
        def swap_routes_and_sections_rows(section):
            routes = re.search(
                r"^\| 2 \| `routes[.]json` \|.*$",
                section,
                re.MULTILINE,
            )
            sections = re.search(
                r"^\| 3 \| `sections[.]json` \|.*$",
                section,
                re.MULTILINE,
            )
            self.assertIsNotNone(routes)
            self.assertIsNotNone(sections)
            return (
                section[:routes.start()]
                + sections.group(0)
                + section[routes.end():sections.start()]
                + routes.group(0)
                + section[sections.end():]
            )

        fixture = _transform_handoff_section(
            self,
            self.markdown,
            swap_routes_and_sections_rows,
        )

        with self.assertRaisesRegex(
            AssertionError,
            r"reading-order row 2 has order 3; expected 2",
        ):
            assert_handoff_ingestion(self, fixture)

    def test_rejects_deleted_reading_order_row(self):
        def delete_routes_row(section):
            transformed, replacements = re.subn(
                r"^\| 2 \| `routes[.]json` \|.*\n",
                "",
                section,
                count=1,
                flags=re.MULTILINE,
            )
            self.assertEqual(replacements, 1)
            return transformed

        fixture = _transform_handoff_section(
            self,
            self.markdown,
            delete_routes_row,
        )

        with self.assertRaisesRegex(
            AssertionError,
            r"reading-order row/filename problem: table must contain "
            r"exactly 8 data rows; found 7",
        ):
            assert_handoff_ingestion(self, fixture)

    def test_ignores_numeric_table_outside_prescribed_reading_order(self):
        unrelated_table = (
            "\n### Unrelated Numeric Table\n\n"
            "| Order | File | Note |\n"
            "| --- | --- | --- |\n"
            "| 99 | `unrelated.json` | Not part of the reading order. |\n"
        )
        fixture = _transform_handoff_section(
            self,
            self.markdown,
            lambda section: section + unrelated_table,
        )

        assert_handoff_ingestion(self, fixture)

    def test_rejects_reading_order_table_without_header_or_delimiter(self):
        removals = {
            "header": r"^\| Order \|.*\n",
            "delimiter": (
                r"^\|[ \t]*:?-{3,}:?[ \t]*\|"
                r"(?:[ \t]*:?-{3,}:?[ \t]*\|)+[ \t]*\n"
            ),
        }

        for missing_part, pattern in removals.items():
            with self.subTest(missing_part=missing_part):
                def remove_table_structure(section):
                    transformed, replacements = re.subn(
                        pattern,
                        "",
                        section,
                        count=1,
                        flags=re.MULTILINE,
                    )
                    self.assertEqual(replacements, 1)
                    return transformed

                fixture = _transform_handoff_section(
                    self,
                    self.markdown,
                    remove_table_structure,
                )

                with self.assertRaisesRegex(
                    AssertionError,
                    r"header row followed by a delimiter row",
                ):
                    assert_handoff_ingestion(self, fixture)

    def test_duplicate_handoff_headings_are_concatenated(self):
        placeholder = (
            "## Implementation-Handoff Entry Contract\n\n"
            "This placeholder must not hide the complete contract below.\n\n"
        )
        fixture = placeholder + self.markdown

        assert_handoff_ingestion(self, fixture)


if __name__ == "__main__":
    unittest.main()
