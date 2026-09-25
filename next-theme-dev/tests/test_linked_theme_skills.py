"""The three theme skills say, from each entrypoint, that they work together.

next-theme-figma and next-theme-design turn a source into a handoff package;
next-theme-dev validates it and builds the theme. Each SKILL.md opens with the
same roles statement, each README gives the three install commands, and each
catalog entry lists the other two in related_skills. Skills not yet in this
checkout are skipped, so the check holds while they land one at a time.
"""

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
THEME_SKILLS = ("next-theme-figma", "next-theme-design", "next-theme-dev")
INSTALL_COMMANDS = tuple(f"./skills.sh install <target> {skill}" for skill in THEME_SKILLS)


def present():
    return [skill for skill in THEME_SKILLS if (ROOT / skill / "SKILL.md").is_file()]


def roles_statement(skill):
    text = (ROOT / skill / "SKILL.md").read_text(encoding="utf-8")
    match = re.search(r"^# [^\n]+\n\n((?:> [^\n]*\n)+)", text, re.MULTILINE)
    return match.group(1) if match else None


class LinkedThemeSkillsTest(unittest.TestCase):
    def test_each_entrypoint_opens_with_the_same_roles_statement(self):
        statements = {skill: roles_statement(skill) for skill in present()}
        for skill, statement in statements.items():
            with self.subTest(skill=skill):
                self.assertIsNotNone(statement, f"{skill}/SKILL.md must open with the roles statement")
                flat = " ".join(statement.replace("> ", "").split())
                self.assertIn("Three linked theme skills.", flat)
                self.assertIn("are installed and used together", flat)
                for name in THEME_SKILLS:
                    self.assertIn(f"`{name}`", flat)
                self.assertIn("turns a Figma source into a handoff package", flat)
                self.assertIn("turns a live site or its HTML into a handoff package", flat)
                self.assertIn("validates the package and builds the theme", flat)
                self.assertIn("Work always runs from one source skill into `next-theme-dev`", flat)
                self.assertIn("never re-derive what it provides", flat)
        self.assertEqual(len(set(statements.values())), 1, "the roles statement must be identical")

    def test_each_readme_gives_the_three_install_commands(self):
        for skill in present():
            readme = (ROOT / skill / "README.md").read_text(encoding="utf-8")
            with self.subTest(skill=skill):
                for command in INSTALL_COMMANDS:
                    self.assertIn(command, readme)
                self.assertIn("Install all three", readme)

    def test_each_catalog_entry_lists_the_other_two(self):
        catalog = {entry["id"]: entry for entry in json.loads((ROOT / "skills.json").read_text())["skills"]}
        for skill in present():
            with self.subTest(skill=skill):
                related = {item["id"]: item["relationship"] for item in catalog[skill].get("related_skills", [])}
                for sibling in THEME_SKILLS:
                    if sibling == skill:
                        continue
                    self.assertIn(sibling, related)
                    self.assertIn("installed together", related[sibling])


if __name__ == "__main__":
    unittest.main()
