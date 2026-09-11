"""Tests for next-create-campaign.sh, the bash launcher (no network).

CI runs no shell linter, so this file is the launcher's only gate: syntax,
version, help, the interpreter check, verbatim argument forwarding, symlinked
installs, and real offline runs through the engine.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = SKILL_DIR.parent
WRAPPER = SKILL_DIR / "next-create-campaign.sh"
ENGINE = SKILL_DIR / "scripts" / "campaign_admin.py"
EXAMPLE_PLAN = SKILL_DIR / "examples" / "campaign-plan.example.json"
FIXTURE = SKILL_DIR / "tests" / "fixtures" / "discovery.json"


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if k != "NEXT_ADMIN_API_TOKEN" and not k.endswith("_NEXT_ADMIN_API_TOKEN")}
    env.pop("NEXT_CREATE_CAMPAIGN_PYTHON", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.update(extra)
    return env


def run(*args, cwd, env=None, wrapper=WRAPPER):
    return subprocess.run(["bash", str(wrapper), *args], cwd=str(cwd), env=env or clean_env(),
                          capture_output=True, text=True, timeout=120)


def in_git_repo(path: Path) -> bool:
    return any((d / ".git").exists() for d in (path, *path.parents))


class Launcher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def make_stub(self):
        """A fake interpreter: the launcher's version probe (-c) goes to the real
        Python; anything else records the token variable and every argument."""
        record = self.root / "record.txt"
        stub = self.root / "fake-python"
        stub.write_text(
            "#!/usr/bin/env bash\n"
            f'if [ "${{1:-}}" = "-c" ]; then exec "{sys.executable}" "$@"; fi\n'
            f'printf "TOKEN=%s\\n" "${{NEXT_ADMIN_API_TOKEN-<unset>}}" > "{record}"\n'
            f'for a in "$@"; do printf "ARG=%s\\n" "$a" >> "{record}"; done\n'
        )
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
        return stub, record

    def test_bash_syntax(self):
        r = subprocess.run(["bash", "-n", str(WRAPPER)], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)

    def test_version_matches_catalog(self):
        r = run("--version", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        want = re.search(r"^version:\s*(\S+)", (SKILL_DIR / "SKILL.md").read_text(), re.M).group(1)
        self.assertEqual(r.stdout.strip(), f"next-create-campaign {want}")
        catalog = REPO_ROOT / "skills.json"
        if catalog.exists():  # absent in an installed copy, present in the repository
            entry = next(s for s in json.loads(catalog.read_text())["skills"] if s["id"] == "next-create-campaign")
            self.assertEqual(entry["version"], want)

    def test_help_exit_zero(self):
        r = run("--help", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        for part in ("discover", "metadata", "recommend", "teardown", "NEXT_ADMIN_API_TOKEN", ".env"):
            self.assertIn(part, r.stdout)

    def test_no_args_exit_two(self):
        r = run(cwd=self.root)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Usage", r.stderr)

    def test_args_forwarded_verbatim(self):
        stub, record = self.make_stub()
        args = ["discover", "--store", "https://my-store.29next.store/", "--out", "two words"]
        r = run(*args, cwd=self.root, env=clean_env(NEXT_CREATE_CAMPAIGN_PYTHON=str(stub)))
        self.assertEqual(r.returncode, 0, r.stderr)
        lines = record.read_text().splitlines()
        self.assertEqual(lines[0], "TOKEN=<unset>")  # the launcher never loads or exports a token
        self.assertEqual([line[len("ARG="):] for line in lines[1:]], [str(ENGINE), *args])

    def test_bad_python_override(self):
        r = run("discover", "--store", "mystore", cwd=self.root,
                env=clean_env(NEXT_CREATE_CAMPAIGN_PYTHON=str(self.root / "no-such-python")))
        self.assertEqual(r.returncode, 2)
        self.assertIn("NEXT_CREATE_CAMPAIGN_PYTHON", r.stderr)

    def test_symlinked_launcher_finds_engine(self):
        (self.root / "bin").mkdir()
        link = self.root / "bin" / "ncc"
        link.symlink_to(WRAPPER)
        r = run("--version", cwd=self.root, wrapper=link)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("next-create-campaign "))
        stub, record = self.make_stub()
        r = run("plan", "--plan", "p.json", cwd=self.root, wrapper=link,
                env=clean_env(NEXT_CREATE_CAMPAIGN_PYTHON=str(stub)))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ARG={ENGINE}", record.read_text())

    def test_missing_token_reported_by_engine(self):
        r = run("discover", "--store", "my-store", cwd=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("MY_STORE_NEXT_ADMIN_API_TOKEN", r.stderr)
        self.assertIn(".env", r.stderr)
        self.assertFalse((self.root / "next-create-campaign-runs").exists())

    def test_placeholder_token_refused(self):
        (self.root / ".env").write_text("MYSTORE_NEXT_ADMIN_API_TOKEN=<paste-token-here>\n")
        r = run("discover", "--store", "mystore", cwd=self.root)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("placeholder", r.stderr)

    def test_real_plan_offline(self):
        r = run("plan", "--plan", str(EXAMPLE_PLAN), cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Plan SHA-256:", r.stdout)
        self.assertIn("next-create-campaign.sh apply --plan", r.stdout)

    def test_real_recommend_offline(self):
        if in_git_repo(self.root):
            self.skipTest("the temp directory sits inside a git repository")
        run_dir = self.root / "next-create-campaign-runs" / "teststore"
        run_dir.mkdir(parents=True)
        shutil.copy(FIXTURE, run_dir / "discovery.json")
        r = run("recommend", "--discovery", str(run_dir / "discovery.json"), "--hero", "22", "--ctc", "low",
                "--anchor-price", "49.95", "--shipping", "standard:6.95", cwd=self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        plan = json.loads((run_dir / "campaign-plan.json").read_text())
        self.assertEqual([row["order_total"] for row in plan["landed_prices"]], ["24.97", "44.96", "59.94"])
        self.assertEqual(plan["blockers"], [])


if __name__ == "__main__":
    unittest.main()
