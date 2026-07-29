"""Exercise the read-only, version-aware skill status workflow."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills.sh"


class SkillStatusTest(unittest.TestCase):
    def test_status_reports_stale_source_and_installed_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            target = Path(temp_dir)
            installed = target / "next-theme-dev"
            shutil.copytree(ROOT / "next-theme-dev", installed)
            skill_file = installed / "SKILL.md"
            skill_file.write_text(
                skill_file.read_text(encoding="utf-8").replace(
                    "version: 1.7.0", "version: 1.4.0", 1
                ),
                encoding="utf-8",
            )

            result = subprocess.run(
                [
                    "bash",
                    str(SCRIPT),
                    "status",
                    "--target",
                    str(target),
                    "next-theme-dev",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stale", result.stdout)
        self.assertIn("source=1.7.0", result.stdout)
        self.assertIn("installed=1.4.0", result.stdout)


if __name__ == "__main__":
    unittest.main()
