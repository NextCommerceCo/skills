import re
import shlex
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1] / "SKILL.md"

SUBCOMMANDS = {"init", "list", "checkout", "pull", "push", "watch", "sass"}
COMMON_FLAGS = {"--apikey", "-a", "--store", "-s"}
INIT_FLAGS = {"--name", "-n"} | COMMON_FLAGS
SASS_FLAGS = COMMON_FLAGS | {"-sos", "--sass_output_style"}
SHELL_SEPARATORS = {";", "&&", "||", "|", "&", "(", ")"}


def _shell_tokens(text):
    lexer = shlex.shlex(text, posix=True, punctuation_chars=";&|()")
    lexer.commenters = "#"
    lexer.whitespace_split = True
    return list(lexer)


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


def extract_ntk_invocations(markdown):
    invocations = []
    fenced_lines = []
    fence_start = None
    fence_marker = None

    for line_number, line in enumerate(markdown.splitlines(), start=1):
        fence = re.match(r"^\s*(`{3,})", line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
                fence_start = line_number + 1
                fenced_lines = []
            elif len(marker) >= len(fence_marker):
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
                for logical_line_number, logical_line in logical_lines:
                    invocations.extend(
                        _invocations_in_context(logical_line, logical_line_number)
                    )
                fence_marker = None
                fence_start = None
                fenced_lines = []
            continue

        if fence_marker is not None:
            fenced_lines.append(line)
            continue

        for inline in re.finditer(r"(?<!`)`([^`\n]+)`(?!`)", line):
            invocations.extend(
                _invocations_in_context(inline.group(1), line_number)
            )

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

        if subcommand == "init":
            allowed_flags = INIT_FLAGS
        elif subcommand == "sass":
            allowed_flags = SASS_FLAGS
        else:
            allowed_flags = COMMON_FLAGS

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
        assert_command_truth(self, SKILL.read_text(encoding="utf-8"))

    def test_rejects_unknown_subcommand(self):
        fixture = """```bash
ntk tailwind build
```
"""
        with self.assertRaisesRegex(AssertionError, "tailwind.*line 2"):
            assert_command_truth(self, fixture)

    def test_rejects_unknown_init_flag(self):
        fixture = """```bash
ntk init --theme_id=5
```
"""
        with self.assertRaisesRegex(AssertionError, "theme_id.*line 2"):
            assert_command_truth(self, fixture)

    def test_accepts_valid_push(self):
        fixture = """```bash
ntk push templates/index.html
```
"""
        assert_command_truth(self, fixture)

    def test_parser_reads_fences_inline_code_and_continuations(self):
        fixture = """Run `ntk list`, then:
```bash
ntk init \\
  --name=example --apikey=secret --store=example.29next.store
```
"""
        self.assertEqual(
            extract_ntk_invocations(fixture),
            [
                (1, ["ntk", "list"]),
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
