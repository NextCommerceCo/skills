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


def initialize_git_repository(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


def track(root: Path, *paths: str) -> None:
    subprocess.run(["git", "add", "--", *paths], cwd=root, check=True)


class ScanTextTests(unittest.TestCase):
    def rule_ids(self, text: str) -> set[str]:
        return {hit.rule_id for hit in safety.scan_text(text, "sample.md")}

    def test_private_repo_reference_fires(self) -> None:
        sample = "See NextCommerceCo/next-campaigns-ops for details."  # public-safety: allow private-repo
        self.assertIn("private-repo", self.rule_ids(sample))

    def test_blocklisted_customer_token_fires(self) -> None:
        sample = "Evidence copied from bareearth launch."  # public-safety: allow customer-token
        self.assertIn("customer-token", self.rule_ids(sample))

    def test_sensitive_values_inside_fence_are_scanned(self) -> None:
        sample = "\n".join(
            [
                "```text",
                "Authorization: Bearer literalToken123456789",  # public-safety: allow credential
                "Contact alice@merchant.test for access.",  # public-safety: allow email-pii
                "Call +44 20 7946 0958 today.",  # public-safety: allow phone-pii
                "```",
            ]
        )

        self.assertTrue(
            {"credential", "email-pii", "phone-pii"}.issubset(self.rule_ids(sample))
        )

    def test_suppressions_are_explicit_and_validated(self) -> None:
        allow_all = "bareearth # " + "public-" + "safety: allow all"  # public-safety: allow customer-token
        self.assertTrue(
            {"customer-token", "unknown-suppression"}.issubset(self.rule_ids(allow_all))
        )

        named = "bareearth # public-safety: allow customer-token"  # public-safety: allow customer-token
        self.assertEqual(set(), self.rule_ids(named))

        unknown = "safe text # " + "public-" + "safety: allow made-up-rule"
        self.assertEqual({"unknown-suppression"}, self.rule_ids(unknown))

    def test_literal_auth_headers_fire_but_placeholders_do_not(self) -> None:
        literals = [
            "Authorization: Token literalTokenValue",  # public-safety: allow credential
            "X-Api-Key: literalApiKeyValue",  # public-safety: allow credential
        ]
        for sample in literals:
            with self.subTest(sample=sample):
                self.assertIn("credential", self.rule_ids(sample))

        placeholders = [
            "Authorization: Token {placeholder}",
            "X-Api-Key: $ENV",
        ]
        for sample in placeholders:
            with self.subTest(sample=sample):
                self.assertNotIn("credential", self.rule_ids(sample))

    def test_jwt_and_high_entropy_token_shapes_fire(self) -> None:
        samples = [
            (
                "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJhYmMifQ.c2lnbmF0dXJl",  # public-safety: allow credential
                "credential",
            ),
            (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmn==",  # public-safety: allow high-entropy
                "high-entropy",
            ),
            (
                "AbCdEfGhIjKlMnOpQrStUvWxYz_abcdefghijklmnop",  # public-safety: allow high-entropy
                "high-entropy",
            ),
        ]
        for sample, expected_rule in samples:
            with self.subTest(sample=sample):
                self.assertIn(expected_rule, self.rule_ids(sample))

    def test_encoded_and_zero_width_private_variants_fire(self) -> None:
        cases = [
            (
                "NextCommerceCo&#x2F;oscar&#x2D;prime",  # public-safety: allow private-repo customer-token
                {"private-repo", "customer-token"},
            ),
            (
                "NextCommerceCo%2Foscar%2Dprime",  # public-safety: allow private-repo customer-token
                {"private-repo", "customer-token"},
            ),
            (
                "NextCommerceCo/\u200boscar\u200b-\u200bprime",  # public-safety: allow private-repo customer-token
                {"private-repo", "customer-token"},
            ),
            ("oscar&#32;prime", {"customer-token"}),  # public-safety: allow customer-token
            ("oscar%20prime", {"customer-token"}),  # public-safety: allow customer-token
            ("oscar\u200b \u200bprime", {"customer-token"}),  # public-safety: allow customer-token
        ]
        for sample, expected_rules in cases:
            with self.subTest(sample=sample):
                self.assertTrue(expected_rules.issubset(self.rule_ids(sample)))

    def test_false_positive_guard(self) -> None:
        sample = "\n".join(
            [
                "Authorization: Bearer {api_access_token}",
                "Authorization: Token $NEXT_ADMIN_API_TOKEN",
                "X-Api-Key: <api access token>",
                "Provider A and Provider B are interchangeable examples.",
                "Contact user@example.com or alerts@example.com.",
                "https://github.com/NextCommerceCo/skills",
                "https://github.com/NextCommerceCo/campaign-cart-starter-templates",
                "https://github.com/NextCommerceCo/campaigns-os",
            ]
        )

        self.assertEqual([], safety.scan_text(sample, "sample.md"))


class RepositoryScanTests(unittest.TestCase):
    def test_tracked_txt_file_is_scanned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            (root / "notes.txt").write_text(
                "Authorization: Bearer literalToken123456789\n",  # public-safety: allow credential
                encoding="utf-8",
            )
            track(root, "notes.txt")

            hits = safety.scan_repository(root)

        self.assertIn(("notes.txt", "credential"), {(hit.path, hit.rule_id) for hit in hits})

    def test_invalid_utf8_is_an_unsuppressible_unreadable_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            content = b"# public-" + b"safety: allow unreadable\n\xff"
            (root / "broken.txt").write_bytes(content)
            track(root, "broken.txt")

            hits = safety.scan_repository(root)

        self.assertEqual(
            [("broken.txt", "unreadable")],
            [(hit.path, hit.rule_id) for hit in hits],
        )

    def test_symlink_target_string_is_scanned_without_following(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            target = "../oscar-prime/internal.md"  # public-safety: allow private-repo customer-token
            (root / "leak-link").symlink_to(target)
            track(root, "leak-link")

            hits = safety.scan_repository(root)

        self.assertIn(
            ("leak-link", "private-repo"),
            {(hit.path, hit.rule_id) for hit in hits},
        )

    def test_binary_media_extension_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            (root / "image.png").write_bytes(
                b"Authorization: Bearer literalToken123456789"  # public-safety: allow credential
            )
            track(root, "image.png")

            self.assertEqual([], safety.scan_repository(root))

    def test_nul_sniffed_file_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            (root / "opaque.txt").write_bytes(
                b"Authorization: Bearer literalToken123456789\0"  # public-safety: allow credential
            )
            track(root, "opaque.txt")

            self.assertEqual([], safety.scan_repository(root))


class CommandLineTests(unittest.TestCase):
    def run_scanner(self, content: str, *extra_args: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            initialize_git_repository(root)
            (root / "sample.md").write_text(content, encoding="utf-8")
            track(root, "sample.md")
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
        result = self.run_scanner(
            "Authorization: Bearer literalToken123456789\n",  # public-safety: allow credential
            "--list",
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("sample.md:1: [credential]", result.stdout)

    def test_exit_two_for_git_enumeration_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--root", temporary_directory],
                text=True,
                capture_output=True,
                check=False,
            )

        self.assertEqual(2, result.returncode)
        self.assertIn("unable to list tracked files", result.stderr)


if __name__ == "__main__":
    unittest.main()
