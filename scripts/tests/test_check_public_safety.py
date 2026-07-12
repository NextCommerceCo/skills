from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "check_public_safety.py"
SPEC = importlib.util.spec_from_file_location("check_public_safety", SCRIPT)
assert SPEC and SPEC.loader
safety = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = safety
SPEC.loader.exec_module(safety)


class ScanTextTests(unittest.TestCase):
    def rule_ids(self, text: str) -> set[str]:
        return {hit.rule_id for hit in safety.scan_text(text, "sample.md")}

    def test_private_repo_reference_fires(self) -> None:
        sample = "See NextCommerceCo/next-campaigns-ops for details."  # public-safety: allow private-repo
        self.assertIn("private-repo", self.rule_ids(sample))

    def test_blocklisted_customer_token_fires(self) -> None:
        sample = "Evidence copied from bareearth launch."  # public-safety: allow customer-token
        self.assertIn("customer-token", self.rule_ids(sample))

    def test_real_bearer_token_fires(self) -> None:
        sample = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.realSecret123456"  # public-safety: allow credential high-entropy
        self.assertIn("credential", self.rule_ids(sample))

    def test_email_pii_fires(self) -> None:
        sample = "Contact alice@merchant.test for access."  # public-safety: allow email-pii
        self.assertIn("email-pii", self.rule_ids(sample))

    def test_phone_pii_fires(self) -> None:
        self.assertIn("phone-pii", self.rule_ids("Call +44 20 7946 0958 today."))  # public-safety: allow phone-pii

    def test_high_entropy_fires_outside_fence(self) -> None:
        token = "0123456789abcdef0123456789abcdef"  # public-safety: allow high-entropy
        self.assertIn("high-entropy", self.rule_ids(token))
        self.assertNotIn("high-entropy", self.rule_ids(f"```text\n{token}\n```"))

    def test_placeholders_and_public_repos_do_not_fire(self) -> None:
        sample = "\n".join(
            [
                "Authorization: Bearer {api_access_token}",
                "Authorization: Bearer <api access token>",
                "Authorization: Bearer $NEXT_ADMIN_API_TOKEN",
                "https://github.com/NextCommerceCo/skills",
                "https://github.com/NextCommerceCo/campaign-cart-starter-templates",
                "https://github.com/NextCommerceCo/campaigns-os",
                "user@example.com",
            ]
        )
        self.assertEqual([], safety.scan_text(sample, "sample.md"))

    def test_suppression_applies_only_to_named_rule(self) -> None:
        sample = "alice@merchant.test # public-safety: allow email-pii"  # public-safety: allow email-pii
        self.assertEqual([], safety.scan_text(sample, "sample.md"))


class CommandLineTests(unittest.TestCase):
    def run_scanner(self, content: str, *extra_args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            subprocess.run(["git", "init", "-q"], cwd=root, check=True)
            (root / "sample.md").write_text(content, encoding="utf-8")
            subprocess.run(["git", "add", "sample.md"], cwd=root, check=True)
            return subprocess.run(
                [sys.executable, str(SCRIPT), "--root", str(root), *extra_args],
                text=True,
                capture_output=True,
                check=False,
            )

    def test_exit_zero_for_clean_tree(self) -> None:
        result = self.run_scanner("user@example.com\n")
        self.assertEqual(0, result.returncode, result.stderr)

    def test_exit_one_and_lists_hit_for_bad_tree(self) -> None:
        result = self.run_scanner("Authorization: Bearer literalToken123456789\n", "--list")  # public-safety: allow credential
        self.assertEqual(1, result.returncode)
        self.assertIn("sample.md:1: [credential]", result.stdout)


if __name__ == "__main__":
    unittest.main()
