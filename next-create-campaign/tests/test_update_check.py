"""Tests for scripts/update_check.py (no network).

The fetch is injected as a transport callable, so nothing here touches GitHub,
the operator's ~/.cache, or a real npx skills lock file. Every test builds a
fake HOME and an isolated XDG_CACHE_HOME.
"""
from __future__ import annotations

import io
import json
import shutil
import sys
import tempfile
import time
import unittest
import unittest.mock
from contextlib import redirect_stdout
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))
import update_check as uc  # noqa: E402

NOW = 1_800_000_000.0


def catalog(**versions) -> bytes:
    return json.dumps({"skills": [{"id": k.replace("_", "-"), "version": v} for k, v in versions.items()]}).encode()


class FakeTransport:
    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def __call__(self, url, timeout):
        self.calls.append(url)
        if self.error:
            raise self.error
        return self.body


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {"HOME": str(self.home), "XDG_CACHE_HOME": str(self.root / "cache")}

    def make_skill(self, parent: Path, version="0.3.1", name="next-create-campaign") -> Path:
        skill = parent / name
        skill.mkdir(parents=True)
        lines = ["---", f"name: {name}"] + ([f"version: {version}"] if version is not None else []) + ["---", "", "# body"]
        (skill / "SKILL.md").write_text("\n".join(lines) + "\n")
        return skill

    def run_check(self, skill, transport=None, **kw):
        transport = transport or FakeTransport(catalog(next_create_campaign="0.4.0"))
        return uc.check(skill, self.env, now=kw.pop("now", NOW), transport=transport, **kw), transport


class Frontmatter(Base):
    def test_quoted_unquoted_crlf(self):
        for raw, want in (("version: 1.2.3", "1.2.3"), ('version: "1.2.3"', "1.2.3"), ("version: '1.2.3'", "1.2.3")):
            path = self.root / "SKILL.md"
            path.write_bytes(f"---\r\nname: x\r\n{raw}\r\n---\r\nversion: 9.9.9\r\n".encode())
            self.assertEqual(uc.read_frontmatter(path), {"name": "x", "version": want})

    def test_missing_file_or_block(self):
        self.assertEqual(uc.read_frontmatter(self.root / "nope.md"), {})
        path = self.root / "SKILL.md"
        path.write_text("version: 1.0.0\n")
        self.assertEqual(uc.read_frontmatter(path), {})

    def test_semver(self):
        self.assertEqual(uc.parse_semver("10.0.1"), (10, 0, 1))
        for bad in ("1.0", "01.0.0", "1.0.0-rc1", "", None, 3):
            self.assertIsNone(uc.parse_semver(bad), bad)


class Comparison(Base):
    def test_update_available_equal_newer(self):
        skill = self.make_skill(self.root / "anywhere")
        for installed, want in (("0.3.1", "update-available"), ("0.4.0", "up-to-date"), ("0.10.0", "local-newer")):
            shutil.rmtree(skill)
            skill = self.make_skill(self.root / "anywhere", version=installed)
            result, _ = self.run_check(skill, no_cache=True)
            self.assertEqual(result["status"], want, installed)
            self.assertEqual(result["latest"], "0.4.0")

    def test_unrelated_skill_bump_does_not_warn(self):
        skill = self.make_skill(self.root / "anywhere", version="0.4.0")
        transport = FakeTransport(catalog(next_create_campaign="0.4.0", next_theme_dev="9.0.0"))
        result, _ = self.run_check(skill, transport)
        self.assertEqual(result["status"], "up-to-date")

    def test_missing_or_invalid_installed_version(self):
        for version in (None, "latest"):
            skill = self.make_skill(self.root / f"v{version}", version=version)
            result, transport = self.run_check(skill)
            self.assertEqual(result["status"], "unknown-version")
            self.assertEqual(transport.calls, [])

    def test_disabled(self):
        skill = self.make_skill(self.root / "anywhere")
        self.env["NEXT_SKILLS_NO_UPDATE_CHECK"] = "1"
        result, transport = self.run_check(skill)
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(transport.calls, [])


class Failures(Base):
    def assert_could_not_check(self, transport):
        skill = self.make_skill(self.root / "anywhere")
        result, _ = self.run_check(skill, transport, no_cache=True)
        self.assertEqual(result["status"], "could-not-check")
        self.assertIn("Could not check", result["lines"][0])

    def test_transport_errors(self):
        self.assert_could_not_check(FakeTransport(error=OSError("HTTP Error 404")))

    def test_bad_json(self):
        self.assert_could_not_check(FakeTransport(b"<html>not json"))

    def test_missing_entry(self):
        self.assert_could_not_check(FakeTransport(catalog(next_theme_dev="1.0.0")))

    def test_hostile_version_ignored(self):
        body = json.dumps({"skills": [{"id": "next-create-campaign", "version": "9.9.9; rm -rf ~", "extra": "x"}]}).encode()
        self.assert_could_not_check(FakeTransport(body))

    def test_hung_transport_cut_at_deadline(self):
        skill = self.make_skill(self.root / "anywhere")

        def hang(url, timeout):
            time.sleep(5)

        started = time.monotonic()
        result = uc.check(skill, self.env, now=NOW, transport=hang, no_cache=True, deadline=0.2)
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(result["status"], "could-not-check")

    def test_main_never_raises_or_exits_nonzero(self):
        def boom(url, timeout):
            raise RuntimeError("boom")

        skill = self.make_skill(self.root / "anywhere")
        out = io.StringIO()
        with redirect_stdout(out):
            code = uc.main(["--skill-dir", str(skill), "--no-cache"], env=self.env, transport=boom)
        self.assertEqual(code, 0)
        self.assertIn("Could not check", out.getvalue())
        with redirect_stdout(io.StringIO()), unittest.mock.patch.object(uc, "check", side_effect=ValueError):
            self.assertEqual(uc.main(["--skill-dir", str(skill)], env=self.env), 0)
        with redirect_stdout(io.StringIO()), unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(uc.main(["--bogus"], env=self.env), 0)


class Cache(Base):
    def cache_path(self) -> Path:
        return Path(self.env["XDG_CACHE_HOME"]) / "next-skills" / "catalog.json"

    def test_failure_does_not_overwrite_concurrent_success(self):
        skill = self.make_skill(self.root / "anywhere")
        good = FakeTransport(catalog(next_create_campaign="0.4.0"))

        def fail_after_other_launch_succeeds(url, timeout):
            uc.check(skill, self.env, transport=good, no_cache=True)  # a concurrent launch wins the race
            raise OSError("offline")

        result = uc.check(skill, self.env, transport=fail_after_other_launch_succeeds, no_cache=True)
        self.assertEqual(result["status"], "update-available")
        self.assertTrue(json.loads(self.cache_path().read_text())["ok"])

    def test_fresh_cache_skips_transport(self):
        skill = self.make_skill(self.root / "anywhere")
        self.run_check(skill)
        self.assertTrue(self.cache_path().is_file())
        result, transport = self.run_check(skill, now=NOW + 3600)
        self.assertEqual(transport.calls, [])
        self.assertEqual(result["status"], "update-available")

    def test_installed_version_never_cached(self):
        skill = self.make_skill(self.root / "anywhere")
        self.run_check(skill)
        (skill / "SKILL.md").write_text("---\nname: next-create-campaign\nversion: 0.4.0\n---\n")
        result, transport = self.run_check(skill, now=NOW + 60)
        self.assertEqual(transport.calls, [])
        self.assertEqual(result["status"], "up-to-date")

    def test_stale_cache_refetches(self):
        skill = self.make_skill(self.root / "anywhere")
        self.run_check(skill)
        _, transport = self.run_check(skill, now=NOW + uc.SUCCESS_TTL + 1)
        self.assertEqual(len(transport.calls), 1)

    def test_negative_cache_ttl(self):
        skill = self.make_skill(self.root / "anywhere")
        self.run_check(skill, FakeTransport(error=OSError("offline")))
        result, transport = self.run_check(skill, now=NOW + 60)
        self.assertEqual(transport.calls, [])
        self.assertEqual(result["status"], "could-not-check")
        _, transport = self.run_check(skill, now=NOW + uc.FAILURE_TTL + 1)
        self.assertEqual(len(transport.calls), 1)

    def test_corrupt_cache_is_ignored(self):
        skill = self.make_skill(self.root / "anywhere")
        self.cache_path().parent.mkdir(parents=True)
        self.cache_path().write_text("{not json")
        result, transport = self.run_check(skill)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(result["status"], "update-available")

    def test_unwritable_cache_still_checks(self):
        skill = self.make_skill(self.root / "anywhere")
        blocker = self.root / "blocked"
        blocker.write_text("a file where the cache directory should be")
        self.env["XDG_CACHE_HOME"] = str(blocker)
        result, _ = self.run_check(skill)
        self.assertEqual(result["status"], "update-available")

    def test_relative_xdg_cache_home_ignored(self):
        skill = self.make_skill(self.root / "anywhere")
        self.env["XDG_CACHE_HOME"] = "relative-cache"
        self.run_check(skill)
        self.assertTrue((self.home / ".cache" / "next-skills" / "catalog.json").is_file())
        self.assertFalse(Path("relative-cache").exists())

    def test_usage_error_prints_text_fallback(self):
        out = io.StringIO()
        with redirect_stdout(out), unittest.mock.patch("sys.stderr", io.StringIO()):
            self.assertEqual(uc.main(["--bogus"], env=self.env), 0)
        self.assertIn("Could not check for updates", out.getvalue())

    def test_no_cache_flag_fetches(self):
        skill = self.make_skill(self.root / "anywhere")
        self.run_check(skill)
        _, transport = self.run_check(skill, no_cache=True, now=NOW + 60)
        self.assertEqual(len(transport.calls), 1)

    def test_override_bypasses_cache_and_labels(self):
        skill = self.make_skill(self.root / "anywhere")
        self.env["NEXT_SKILLS_CATALOG_URL"] = "file:///fixture.json"
        result, transport = self.run_check(skill)
        self.assertEqual(transport.calls, ["file:///fixture.json"])
        self.assertFalse(self.cache_path().exists())
        self.assertIn("in file:///fixture.json", result["lines"][0])
        self.assertNotIn("on main", result["lines"][0])

    def test_default_url_is_canonical_https(self):
        skill = self.make_skill(self.root / "anywhere")
        _, transport = self.run_check(skill)
        self.assertEqual(transport.calls, [uc.CATALOG_URL])
        self.assertTrue(uc.CATALOG_URL.startswith("https://raw.githubusercontent.com/NextCommerceCo/skills/main/"))


class InstallMethod(Base):
    def lock(self, path: Path, **entry):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"version": 3, "skills": {"next-create-campaign": {"source": "NextCommerceCo/skills", **entry}}}))

    def update_text(self, skill) -> str:
        result, _ = self.run_check(skill, no_cache=True)
        self.assertEqual(result["status"], "update-available")
        return "\n".join(result["lines"])

    def make_checkout(self, parent: Path, head="ref: refs/heads/main\n", worktree=False) -> Path:
        checkout = parent / "skills"
        skill = self.make_skill(checkout)
        (checkout / "skills.json").write_text("{}")
        (checkout / "skills.sh").write_text("#!/usr/bin/env bash\n")
        if worktree:
            gitdir = parent / "main-repo" / ".git" / "worktrees" / "wt"
            gitdir.mkdir(parents=True)
            (gitdir / "HEAD").write_text(head)
            (checkout / ".git").write_text("gitdir: ../main-repo/.git/worktrees/wt\n")
        else:
            (checkout / ".git").mkdir()
            (checkout / ".git" / "HEAD").write_text(head)
        return skill

    def test_npx_global(self):
        skill = self.make_skill(self.home / ".agents" / "skills")
        self.lock(self.home / ".agents" / ".skill-lock.json")
        text = self.update_text(skill)
        self.assertIn("npx skills update -g next-create-campaign", text)
        self.assertIn("copy your changes out first", text)

    def test_npx_pinned_ref(self):
        skill = self.make_skill(self.home / ".agents" / "skills")
        self.lock(self.home / ".agents" / ".skill-lock.json", ref="v0")
        text = self.update_text(skill)
        self.assertIn("pinned to v0", text)
        self.assertIn("npx skills add NextCommerceCo/skills -g --skill next-create-campaign", text)
        self.assertNotIn("npx skills update -g", text)

    def test_npx_lock_under_xdg_state_home(self):
        skill = self.make_skill(self.home / ".agents" / "skills")
        state = self.root / "state"
        self.env["XDG_STATE_HOME"] = str(state)
        self.lock(state / "skills" / ".skill-lock.json")
        self.assertIn("npx skills update -g", self.update_text(skill))

    def test_agents_copy_without_lock_is_installer_target(self):
        skill = self.make_skill(self.home / ".agents" / "skills")
        text = self.update_text(skill)
        self.assertIn("./skills.sh install agents next-create-campaign", text)
        self.assertNotIn("npx", text)

    def test_lock_from_other_source_ignored(self):
        skill = self.make_skill(self.home / ".agents" / "skills")
        lock = self.home / ".agents" / ".skill-lock.json"
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"skills": {"next-create-campaign": {"source": "someone/else"}}}))
        self.assertNotIn("npx", self.update_text(skill))

    def test_claude_and_codex_targets(self):
        for target in ("claude", "codex"):
            skill = self.make_skill(self.home / f".{target}" / "skills")
            self.assertIn(f"./skills.sh install {target} next-create-campaign", self.update_text(skill))

    def test_checkout_on_main_bound_to_checkout_path(self):
        skill = self.make_checkout(self.root / "src dir")
        self.lock(self.home / ".agents" / ".skill-lock.json")  # a separate global install must not win
        text = self.update_text(skill)
        checkout = str(skill.parent)
        self.assertIn(f"git -C '{checkout}' pull --ff-only && bash '{checkout}/skills.sh' status", text)
        self.assertNotIn("npx", text)
        self.assertNotIn("replaces this skill folder", text)

    def test_checkout_on_branch_in_worktree(self):
        skill = self.make_checkout(self.root, head="ref: refs/heads/feature/x\n", worktree=True)
        text = self.update_text(skill)
        self.assertIn("on branch feature/x", text)
        self.assertIn("If the branch is intentional, ignore this.", text)
        self.assertNotIn("checkout main", text)

    def test_checkout_detached_and_unknown(self):
        skill = self.make_checkout(self.root / "a", head="a" * 40 + "\n")
        self.assertIn("detached at aaaaaaaaaaaa", self.update_text(skill))
        skill = self.make_checkout(self.root / "b", head="garbage\n")
        self.assertIn("(branch unknown)", self.update_text(skill))

    def test_arbitrary_target_dir(self):
        skill = self.make_skill(self.root / "custom target")
        text = self.update_text(skill)
        self.assertIn("could not be identified", text)
        self.assertIn(f"./skills.sh install --target '{skill.parent}' next-create-campaign", text)
        self.assertNotIn("npx", text)


class NoSecrets(Base):
    def test_never_reads_env_file_or_token(self):
        skill = self.make_skill(self.root / "anywhere")
        (self.root / ".env").write_text("MYSTORE_NEXT_ADMIN_API_TOKEN=secret-value\n")

        class TokenEnv(dict):
            def get(self, key, default=None):
                if "TOKEN" in key:
                    raise AssertionError(f"token variable read: {key}")
                return super().get(key, default)

            def __getitem__(self, key):
                if "TOKEN" in key:
                    raise AssertionError(f"token variable read: {key}")
                return super().__getitem__(key)

        env = TokenEnv(self.env, NEXT_ADMIN_API_TOKEN="secret-value")
        result = uc.check(skill, env, now=NOW, transport=FakeTransport(catalog(next_create_campaign="0.4.0")))
        self.assertEqual(result["status"], "update-available")
        self.assertNotIn("secret-value", json.dumps(result))

    def test_json_mode_errors_stay_json(self):
        skill = self.make_skill(self.root / "anywhere")
        for argv, patch in ((["--json", "--bogus"], None), (["--skill-dir", str(skill), "--json"], ValueError)):
            out = io.StringIO()
            with redirect_stdout(out), unittest.mock.patch("sys.stderr", io.StringIO()):
                if patch:
                    with unittest.mock.patch.object(uc, "check", side_effect=patch):
                        code = uc.main(argv, env=self.env)
                else:
                    code = uc.main(argv, env=self.env)
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.getvalue())["status"], "could-not-check", argv)

    def test_json_output_shape(self):
        skill = self.make_skill(self.root / "anywhere")
        out = io.StringIO()
        with redirect_stdout(out):
            uc.main(["--skill-dir", str(skill), "--json"], env=self.env,
                    transport=FakeTransport(catalog(next_create_campaign="0.4.0")))
        data = json.loads(out.getvalue())
        self.assertEqual({"skill", "installed", "latest", "status", "lines"} - set(data), set())
        self.assertEqual(data["status"], "update-available")


if __name__ == "__main__":
    unittest.main()
