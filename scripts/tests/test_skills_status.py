"""Exercise the read-only, version-aware skill status workflow."""

import os
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
    def run_script(self, *args, env=None):
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def installed_target(self, version=SOURCE_VERSION):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name)
        installed = target / "next-theme-dev"
        shutil.copytree(ROOT / "next-theme-dev", installed)
        if version != SOURCE_VERSION:
            skill_file = installed / "SKILL.md"
            skill_file.write_text(
                skill_file.read_text(encoding="utf-8").replace(
                    f"version: {SOURCE_VERSION}", f"version: {version}", 1
                ),
                encoding="utf-8",
            )
        return target, installed

    def run_status_with_installed_version(self, installed_version):
        target, _installed = self.installed_target(installed_version)
        return self.run_script(
            "status", "--target", str(target), "next-theme-dev"
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

    def test_install_creates_missing_skill(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        target = Path(temp_dir.name)

        result = self.run_script(
            "install", "--target", str(target), "next-theme-dev"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("create", result.stdout)
        self.assertTrue((target / "next-theme-dev" / "SKILL.md").is_file())

    def test_install_updates_stale_skill(self):
        target, installed = self.installed_target("1.4.0")

        result = self.run_script(
            "install", "--target", str(target), "next-theme-dev"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("stale", result.stdout)
        self.assertIn(f"version: {SOURCE_VERSION}", (installed / "SKILL.md").read_text())

    def test_force_guard(self):
        cases = {
            "modified": SOURCE_VERSION,
            "local-newer": "99.0.0",
            "unknown-version": f"{SOURCE_VERSION}-local",
        }
        for expected_status, version in cases.items():
            with self.subTest(status=expected_status):
                target, installed = self.installed_target(version)
                marker = installed / "local-note.txt"
                marker.write_text("keep me")

                result = self.run_script(
                    "install", "--target", str(target), "next-theme-dev"
                )

                self.assertEqual(result.returncode, 1)
                self.assertIn(expected_status, result.stdout)
                self.assertIn("--force", result.stderr)
                self.assertTrue(marker.is_file())

    def test_force_replaces_divergent_copy_and_deletes_extra_files(self):
        target, installed = self.installed_target()
        readme = installed / "README.md"
        readme.write_text(readme.read_text() + "\nlocal edit\n")
        marker = installed / "local-note.txt"
        marker.write_text("remove me")

        result = self.run_script(
            "install", "--force", "--target", str(target), "next-theme-dev"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("modified", result.stdout)
        self.assertFalse(marker.exists())
        self.assertEqual(
            (ROOT / "next-theme-dev" / "README.md").read_text(), readme.read_text()
        )

    def test_all_deduplicates_symlinked_physical_targets(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        home = Path(temp_dir.name)
        shared = home / "shared-skills"
        shared.mkdir()
        for agent in (".claude", ".codex"):
            agent_dir = home / agent
            agent_dir.mkdir()
            (agent_dir / "skills").symlink_to(shared, target_is_directory=True)
        (home / ".agents" / "skills").mkdir(parents=True)
        env = os.environ.copy()
        env["HOME"] = str(home)

        result = self.run_script("install", "all", "next-theme-dev", env=env)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Skipping duplicate target", result.stdout)
        self.assertEqual(result.stdout.count("Target:"), 2)
        self.assertTrue((shared / "next-theme-dev" / "SKILL.md").is_file())
        self.assertTrue(
            (home / ".agents" / "skills" / "next-theme-dev" / "SKILL.md").is_file()
        )


if __name__ == "__main__":
    unittest.main()
