from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "check_skill_versions.py"
SPEC = importlib.util.spec_from_file_location("check_skill_versions", SCRIPT)
assert SPEC and SPEC.loader
versions = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = versions
SPEC.loader.exec_module(versions)


def write_skill(root: Path, version: str, body: str = "instructions") -> None:
    skill = root / "next-example"
    skill.mkdir(exist_ok=True)
    (skill / "SKILL.md").write_text(
        f"---\nname: next-example\nversion: {version}\ndescription: example\n---\n{body}\n"
    )
    (root / "skills.json").write_text(
        json.dumps(
            {
                "skills": [
                    {
                        "id": "next-example",
                        "version": version,
                        "path": "next-example/SKILL.md",
                    }
                ]
            }
        )
    )


class SkillVersionTests(unittest.TestCase):
    def test_semver_order(self) -> None:
        self.assertLess(versions.semver("1.2.9"), versions.semver("1.3.0"))
        with self.assertRaises(ValueError):
            versions.semver("v1.2.3")

    def test_changed_skill_needs_bump(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            write_skill(root, "1.0.0")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()

            write_skill(root, "1.0.0", "changed behavior")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "change without bump"], cwd=root, check=True)
            self.assertTrue(any("did not advance" in error for error in versions.validate(root, base)))

            write_skill(root, "1.1.0", "changed behavior")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-qm", "add bump"], cwd=root, check=True)
            self.assertEqual([], versions.validate(root, base))

    def test_manifest_and_frontmatter_must_match(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_skill(root, "1.0.0")
            manifest = json.loads((root / "skills.json").read_text())
            manifest["skills"][0]["version"] = "1.0.1"
            (root / "skills.json").write_text(json.dumps(manifest))
            self.assertTrue(any("does not match" in error for error in versions.validate(root)))


if __name__ == "__main__":
    unittest.main()
