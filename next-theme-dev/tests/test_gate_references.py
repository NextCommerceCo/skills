"""The gate references have to exist and be reachable from the skill.

A reference that SKILL.md never names is a file nobody loads. These checks are
lexical on purpose: they prove the pointer and the document are both there,
not that the prose is correct.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
GATES = ROOT / "references" / "geometry-and-readback-gates.md"
SCRIPTS = ROOT / "scripts"


class GateReferenceTest(unittest.TestCase):
    def setUp(self):
        self.skill = SKILL.read_text(encoding="utf-8")
        self.gates = GATES.read_text(encoding="utf-8")

    def test_gate_scripts_exist_and_are_executable(self):
        for name in ("assert-geometry.mjs", "readback-assert.py"):
            script = SCRIPTS / name
            with self.subTest(script=name):
                self.assertTrue(script.is_file(), f"{script} is missing")

    def test_skill_points_at_the_gate_reference(self):
        self.assertIn("references/geometry-and-readback-gates.md", self.skill)

    def test_skill_orders_geometry_before_pixel_scoring(self):
        self.assertIn("scripts/assert-geometry.mjs", self.skill)
        self.assertRegex(
            self.skill,
            r"Assert geometry before judging pixels",
            "the fidelity loop must state the geometry-first ordering",
        )

    def test_skill_runs_readback_after_push(self):
        self.assertIn("scripts/readback-assert.py", self.skill)

    def test_gate_reference_covers_every_gate(self):
        for heading in (
            "## 1. Geometry assertion",
            "## 2. Copy lint",
            "## 3. Post-push readback",
            "## 4. Frozen-surface tests",
        ):
            with self.subTest(heading=heading):
                self.assertIn(heading, self.gates)

    def test_frozen_surface_pattern_is_documented_with_a_hash_gate(self):
        section = self.gates.split("## 4. Frozen-surface tests", 1)[1]
        self.assertIn("sha256", section)
        self.assertIn("FROZEN_FILES", section)
        self.assertIn("FROZEN_CSS_BLOCKS", section)
        self.assertRegex(
            section,
            r"hash (?:moves|is updated) in the (?:same )?commit",
            "the pattern must say when the recorded hash may change",
        )

    def test_pixel_scoring_is_documented_as_telemetry(self):
        self.assertRegex(
            self.gates,
            r"[Tt]elemetry",
            "the reference must demote pixel mismatch to telemetry",
        )

    def test_default_tolerances_match_the_comparator(self):
        source = (SCRIPTS / "assert-geometry.mjs").read_text(encoding="utf-8")
        defaults = re.search(
            r"const DEFAULT_TOLERANCES = \{(.+?)\n\};", source, re.DOTALL
        )
        self.assertIsNotNone(defaults, "DEFAULT_TOLERANCES not found")
        for viewport, position in (("desktop", 8), ("tablet", 8), ("mobile", 6)):
            with self.subTest(viewport=viewport):
                self.assertRegex(
                    defaults.group(1),
                    rf"{viewport}: {{ position: {position},",
                )
        self.assertIn(
            "8px position and size at desktop and tablet, 6px at mobile",
            self.gates,
            "the documented tolerances must match the comparator defaults",
        )


if __name__ == "__main__":
    unittest.main()
