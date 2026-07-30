"""Exercise the read-only, version-aware skill status workflow."""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "skills.sh"
SOURCE_SKILL = ROOT / "next-theme-dev" / "SKILL.md"
SOURCE_VERSION = re.search(
    r"^version:\s*(\d+\.\d+\.\d+)$",
    SOURCE_SKILL.read_text(encoding="utf-8"),
    re.MULTILINE,
).group(1)


class SkillStatusTest(unittest.TestCase):
    def run_status_with_installed_version(self, installed_version):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name)
        installed = target / "next-theme-dev"
        shutil.copytree(ROOT / "next-theme-dev", installed)
        skill_file = installed / "SKILL.md"
        skill_file.write_text(
            skill_file.read_text(encoding="utf-8").replace(
                f"version: {SOURCE_VERSION}", f"version: {installed_version}", 1
            ),
            encoding="utf-8",
        )

        return subprocess.run(
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

    def test_status_reports_stale_source_and_installed_versions(self):
        result = self.run_status_with_installed_version("1.4.0")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stale", result.stdout)
        self.assertIn(f"source={SOURCE_VERSION}", result.stdout)
        self.assertIn("installed=1.4.0", result.stdout)

    def test_status_reports_unknown_for_non_strict_versions(self):
        major, minor, _patch = SOURCE_VERSION.split(".")
        for installed_version in (
            f"{SOURCE_VERSION}-rc1",
            f"{major}.{minor}",
            f"{SOURCE_VERSION}+build.1",
        ):
            with self.subTest(installed_version=installed_version):
                result = self.run_status_with_installed_version(installed_version)

                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("unknown-version", result.stdout)
                self.assertIn(
                    f"installed={installed_version}", result.stdout
                )


if __name__ == "__main__":
    unittest.main()
