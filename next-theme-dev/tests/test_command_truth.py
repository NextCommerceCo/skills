"""Lint documented ntk commands against the released command surface.

This intentionally does not defend against shell-adversarial bypasses such as
$NTK variables, ``sh -c``, /usr/bin/ntk, or word-splitting tricks. It also does
not fully validate argv arity or argument values.
"""

import re
import shlex
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"
README = Path(__file__).resolve().parents[1] / "README.md"

SUBCOMMANDS = {"init", "list", "checkout", "pull", "push", "watch", "sass"}
# Mirrors ntk/ntk_parser.py _add_config_arguments in the released parser.
COMMON_FLAGS = {
    "-a",
    "--apikey",
    "-s",
    "--store",
    "-t",
    "--theme_id",
    "-e",
    "--env",
    "-sos",
    "--sass_output_style",
}
INIT_FLAGS = {"--name", "-n"} | COMMON_FLAGS
SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}
# [@+-]* accepts GNU Make recipe prefixes (@, -, +) in any combination.
NTK_INVOCATION = re.compile(
    r"(?<![\w./-])[@+-]*ntk[ \t]+([A-Za-z][A-Za-z0-9_-]*)"
)
FLAG_TOKEN = re.compile(
    r"(?<!\S)(-{1,2}[A-Za-z][A-Za-z0-9_-]*(?:=[^\s\"';&|()<>]*)?)"
)


def _shell_tokens(text):
    try:
        lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
        lexer.commenters = "#"
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return text.split()


def _invocations_in_context(text, line_number):
    if not re.search(r"\bntk(?:\s|$)", text):
        return []
    tokens = _shell_tokens(text)
    invocations = []
    for index, token in enumerate(tokens):
        if token != "ntk" or index + 1 >= len(tokens):
            continue
        subcommand = tokens[index + 1]
        if subcommand in SHELL_SEPARATORS or subcommand.startswith(("<", ">")):
            continue
        end = index + 2
        while end < len(tokens) and tokens[end] not in SHELL_SEPARATORS:
            end += 1
        invocations.append((line_number, tokens[index:end]))
    return invocations


def _regex_invocations(text, line_number):
    invocations = []
    for match in NTK_INVOCATION.finditer(text):
        prefix = text[:match.start()]
        if prefix.lstrip().startswith("#"):
            continue
        if prefix.count('"') % 2:
            opening_quote = prefix.rfind('"')
            if not re.search(r":\s*$", prefix[:opening_quote]):
                continue
        remainder = text[match.end():]
        # Skip escaped quotes so flags after \"...\" in JSON strings are seen.
        boundary = re.search(r"(?<!\\)[\"';&|()<>]", remainder)
        if boundary:
            remainder = remainder[:boundary.start()]
        flags = [flag.group(1) for flag in FLAG_TOKEN.finditer(remainder)]
        invocations.append(
            (line_number, ["ntk", match.group(1)] + flags)
        )
    return invocations


def _fenced_invocations(fenced_lines, fence_start):
    logical_lines = []
    pending = ""
    pending_line = fence_start
    for offset, fenced_line in enumerate(fenced_lines):
        current_line = fence_start + offset
        if not pending:
            pending_line = current_line
        stripped = fenced_line.rstrip()
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
        else:
            logical_lines.append((pending_line, pending + fenced_line))
            pending = ""
    if pending:
        logical_lines.append((pending_line, pending))

    shell_invocations = []
    for line_number, logical_line in logical_lines:
        shell_invocations.extend(
            _invocations_in_context(logical_line, line_number)
        )

    regex_invocations = []
    for offset, fenced_line in enumerate(fenced_lines):
        # Continuation lines are already joined and scanned by the shell
        # path above; scanning them again here would emit partial duplicates.
        if fenced_line.rstrip().endswith("\\"):
            continue
        regex_invocations.extend(
            _regex_invocations(fenced_line, fence_start + offset)
        )

    # Dedupe only exact duplicates: distinct invocations of the same
    # subcommand on one line must each stay visible to validation.
    deduplicated = {}
    for invocation in shell_invocations + regex_invocations:
        line_number, tokens = invocation
        deduplicated.setdefault((line_number, tuple(tokens)), invocation)
    return sorted(deduplicated.values(), key=lambda item: item[0])


def extract_ntk_invocations(markdown):
    invocations = []
    fenced_lines = []
    fence_start = None
    fence_marker = None

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
                fence_start = line_number + 1
                fenced_lines = []
            elif (
                marker[0] == fence_marker[0]
                and len(marker) >= len(fence_marker)
            ):
                invocations.extend(
                    _fenced_invocations(fenced_lines, fence_start)
                )
                fence_marker = None
                fence_start = None
                fenced_lines = []
            continue

        if fence_marker is not None:
            fenced_lines.append(line)
            continue

        for inline in re.finditer(
            r"(?<!`)(`{1,2})([^`\n]+)\1(?!`)", line
        ):
            invocations.extend(
                _invocations_in_context(inline.group(2), line_number)
            )

    if fence_marker is not None:
        invocations.extend(_fenced_invocations(fenced_lines, fence_start))

    return invocations


def assert_command_truth(test_case, markdown):
    for line_number, tokens in extract_ntk_invocations(markdown):
        subcommand = tokens[1]
        test_case.assertIn(
            subcommand,
            SUBCOMMANDS,
            "unknown ntk subcommand {!r} on line {}".format(
                subcommand, line_number
            ),
        )

        allowed_flags = INIT_FLAGS if subcommand == "init" else COMMON_FLAGS

        for token in tokens[2:]:
            if not token.startswith("-") or token == "-":
                continue
            flag = token.split("=", 1)[0]
            test_case.assertIn(
                flag,
                allowed_flags,
                "unknown flag {!r} for ntk {} on line {}".format(
                    flag, subcommand, line_number
                ),
            )


class CommandTruthTest(unittest.TestCase):
    def test_skill_uses_only_released_ntk_commands_and_flags(self):
        markdown = SKILL.read_text(encoding="utf-8")
        assert_command_truth(self, markdown)

        expected_embedded = {
            (line_number, "watch")
            for line_number, line in enumerate(markdown.splitlines(), start=1)
            if "@ntk watch" in line or re.search(r"&[ \t]+ntk watch", line)
        }
        extracted = {
            (line_number, tokens[1])
            for line_number, tokens in extract_ntk_invocations(markdown)
        }
        self.assertGreaterEqual(len(expected_embedded), 3)
        self.assertTrue(expected_embedded <= extracted)

    def test_theme_kit_guidance_matches_public_docs(self):
        markdown = SKILL.read_text(encoding="utf-8")
        readme = README.read_text(encoding="utf-8")
        combined = markdown + "\n" + readme

        self.assertNotIn("Settings > API Keys", combined)
        self.assertIn("Settings > API Access", combined)
        self.assertIn(
            "https://developers.nextcommerce.com/docs/storefront/themes/theme-kit",
            combined,
        )
        self.assertIn("ntk checkout", markdown)
        self.assertRegex(
            markdown,
            r"(?m)^make dev\s+# Run the Tailwind watcher and ntk watch in parallel$",
        )
        self.assertIn(
            "`ntk watch` does not compile Tailwind or run `sass-compat.py`.",
            markdown,
        )
        misleading_watch_lines = [
            line
            for line in markdown.splitlines()
            if line.strip().startswith("ntk watch")
            and re.search(r"\bcompil(?:e|es|ed|ing) Tailwind\b", line)
            and "does not compile Tailwind" not in line
        ]
        self.assertEqual([], misleading_watch_lines)

    def test_rejects_unknown_subcommand(self):
        fixture = """```bash
ntk tailwind build
```
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 2"):
            assert_command_truth(self, fixture)

    def test_rejects_unknown_init_flag(self):
        fixture = """```bash
ntk init --bogus=5
```
"""
        with self.assertRaisesRegex(AssertionError, "bogus.*line 2"):
            assert_command_truth(self, fixture)

    def test_make_dash_prefix_rejected(self):
        fixture = """```make
dev:
\t-ntk tailwind build
\t@-ntk tailwind build
```
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 3"):
            assert_command_truth(self, fixture)

    def test_second_invocation_on_same_line_still_validated(self):
        fixture = """```bash
ntk watch && ntk watch --bogus
```
"""
        with self.assertRaisesRegex(AssertionError, "bogus.*line 2"):
            assert_command_truth(self, fixture)

    def test_flag_after_escaped_quotes_in_json_string_validated(self):
        fixture = """```json
{"scripts": {"dev": "ntk init --name=\\\\"foo\\\\" --bogus"}}
```
"""
        with self.assertRaisesRegex(AssertionError, "bogus.*line 2"):
            assert_command_truth(self, fixture)

    def test_accepts_valid_push(self):
        fixture = """```bash
ntk push templates/index.html
```
"""
        assert_command_truth(self, fixture)

    def test_make_recipe_rejects_unknown_subcommand(self):
        fixture = """```make
dev:
\t@ntk tailwind build
```
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 3"):
            assert_command_truth(self, fixture)

    def test_make_recipe_accepts_watch(self):
        fixture = """```make
dev:
\t@ntk watch
```
"""
        assert_command_truth(self, fixture)

    def test_json_script_rejects_unknown_subcommand(self):
        fixture = """```json
{"scripts": {"dev": "npm run tailwind:watch & ntk tailwind"}}
```
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 2"):
            assert_command_truth(self, fixture)

    def test_json_script_accepts_watch(self):
        fixture = """```json
{"scripts": {"dev": "npm run tailwind:watch & ntk watch"}}
```
"""
        assert_command_truth(self, fixture)

    def test_json_script_rejects_unknown_flag(self):
        fixture = """```json
{"scripts": {"dev": "ntk watch --bogus"}}
```
"""
        with self.assertRaisesRegex(AssertionError, "bogus.*line 2"):
            assert_command_truth(self, fixture)

    def test_rejects_unknown_subcommand_in_tilde_fence(self):
        fixture = """~~~bash
ntk tailwind build
~~~
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 2"):
            assert_command_truth(self, fixture)

    def test_rejects_unknown_subcommand_in_unclosed_fence(self):
        fixture = """```bash
ntk tailwind build
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 2"):
            assert_command_truth(self, fixture)

    def test_accepts_released_theme_flags(self):
        fixture = """```bash
ntk checkout --theme_id=5
ntk pull -t 5
```
"""
        assert_command_truth(self, fixture)

    def test_malformed_quote_does_not_escape_as_value_error(self):
        fixture = """```bash
ntk init --name="oops
```
"""
        try:
            assert_command_truth(self, fixture)
        except AssertionError:
            pass
        except ValueError as error:
            self.fail("malformed quote raised ValueError: {}".format(error))

    def test_parser_reads_fences_inline_code_and_continuations(self):
        fixture = """Run `ntk list` and ``ntk watch``, then:
```bash
ntk init \\
  --name=example --apikey=secret --store=example.29next.store
```
"""
        self.assertEqual(
            extract_ntk_invocations(fixture),
            [
                (1, ["ntk", "list"]),
                (1, ["ntk", "watch"]),
                (
                    3,
                    [
                        "ntk",
                        "init",
                        "--name=example",
                        "--apikey=secret",
                        "--store=example.29next.store",
                    ],
                ),
            ],
        )


if __name__ == "__main__":
    unittest.main()
