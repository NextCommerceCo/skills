"""Unit tests for scripts/campaign_admin.py (no network).

Covers the safety properties the skill relies on: slug/origin binding, the
credential order, paced same-origin client with no redirects, the run-directory
gitignore guard, the metadata audit and provisioning,
doctrine-shaped recommendations, plan validation, the apply gate, the
two-phase journal with reconcile-by-identity, teardown identity checks,
and verify's calculate comparison.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import builtins
import io
from decimal import Decimal
from pathlib import Path
from unittest import mock

SKILL_DIR = Path(__file__).resolve().parents[1]
SCRIPT = SKILL_DIR / "scripts" / "campaign_admin.py"
FIXTURES = Path(__file__).resolve().parent / "fixtures"

spec = importlib.util.spec_from_file_location("campaign_admin", SCRIPT)
ca = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = ca
spec.loader.exec_module(ca)  # type: ignore[union-attr]
ca.print = lambda *a, **k: None  # silence the script's progress output in tests

ORIGIN = "https://teststore.29next.store"
# Shaped like the real thing: the admin API returns a server-built thumbnail URL,
# never the source URL that was uploaded or fetched.
CATALOGUE_IMAGE = "https://cdn.test/media/thumbnails/catalogue.webp"
OVERRIDE_IMAGE = "https://cdn.test/media/thumbnails/override.webp"
# The fake store's campaign currency, and the rates it converts an additional
# currency at when a write asks it to recalculate.
BASE_CURRENCY = "USD"
FOREX = {"EUR": "0.90", "GBP": "0.80"}


def load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


class FakeTransport:
    """Route (method, path) -> (status, headers, json_body). Records every call."""

    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.created = {"campaign": 0, "package": 0, "shipping": 0, "offer": 0}

    def __call__(self, req, timeout):
        method = req.get_method()
        u = urllib.parse.urlsplit(req.full_url)
        path = u.path + (f"?{u.query}" if u.query else "")
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((method, path, body, dict(req.headers)))
        handler = self.routes.get((method, path.split("?")[0]))
        if handler is None:
            return 404, {}, json.dumps({"detail": f"no route {method} {path}"})
        status, resp = handler(path, body) if callable(handler) else handler
        headers = {}
        if isinstance(resp, tuple):
            resp, headers = resp
        return status, headers, json.dumps(resp) if resp is not None else ""


def make_client(transport, token="tok", origin=ORIGIN):
    return ca.Client(origin, token, transport=transport, clock=FakeClock(), sleep=lambda s: None)


class FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        self.t += 0.001
        return self.t


def ns(**kw):
    base = dict(hero=22, ctc="low", anchor_price="49.95", shipping=["standard:6.95"], name=None,
                gateway_group=None, payment_methods=None, express_methods=None, currency=None,
                language=None, countries=None, offer_type="quantity", paid_qty=None, free_qty=None,
                gift=None, gift_mode="auto", tiers=None, exit=None, exit_code=None, bump=None,
                upsell=None, short_name=None, free_shipping=False, free_shipping_min_qty=None, rounding=None,
                statement_descriptor=None)
    base.update(kw)
    return argparse.Namespace(**base)


# --------------------------------------------------------------------------- #
class SlugAndOrigin(unittest.TestCase):
    def test_valid_slug_maps_to_origin_and_env(self):
        self.assertEqual(ca.slug_to_origin("my-store"), "https://my-store.29next.store")
        self.assertEqual(ca.slug_to_env("my-store"), "MY_STORE_NEXT_ADMIN_API_TOKEN")

    def test_malicious_slugs_rejected(self):
        for bad in ("evil.com", "a/b", "user@host", "Upper", "-lead", "trail-", "", "x" * 64):
            with self.assertRaises(ca.CampaignAdminError, msg=bad):
                ca.validate_slug(bad)

    def test_origin_binding_rejects_tampered_files(self):
        for origin in ("http://teststore.29next.store", "https://teststore.29next.store:8443",
                       "https://teststore.29next.store/x", "https://u@" + "teststore.29next.store",
                       "https://evil.example", "https://other.29next.store"):
            with self.assertRaises(ca.CampaignAdminError, msg=origin):
                ca.check_origin_binding({"store_slug": "teststore", "store_origin": origin}, "t")
        self.assertEqual(ca.check_origin_binding({"store_slug": "teststore", "store_origin": ORIGIN}, "t"), ORIGIN)

    def test_normalize_store_forms(self):
        for v in ("mystore", "MyStore", "mystore.29next.store", "https://mystore.29next.store/",
                  "https://mystore.29next.store/api/admin/", " mystore "):
            self.assertEqual(ca.normalize_store(v), "mystore", v)
        for bad in ("mystore.example.com", "a/b", "https://evil.example/", "u@mystore", ""):
            with self.assertRaises(ca.CampaignAdminError, msg=bad):
                ca.normalize_store(bad)


class Credentials(unittest.TestCase):
    NAME = "TESTSTORE_NEXT_ADMIN_API_TOKEN"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dotenv = Path(self.tmp.name) / ".env"
        self.absent = Path(self.tmp.name) / "absent.env"

    def test_token_order(self):
        self.dotenv.write_text(f"{self.NAME}=fromfile\n")
        generic = {"NEXT_ADMIN_API_TOKEN": "generic"}
        self.assertEqual(ca.load_token("teststore", env=dict(generic, **{self.NAME: "fromenv"}),
                                       dotenv=self.dotenv), "fromenv")
        self.assertEqual(ca.load_token("teststore", env=generic, dotenv=self.dotenv), "fromfile")
        self.assertEqual(ca.load_token("teststore", env=generic, dotenv=self.absent), "generic")
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.load_token("teststore", env={}, dotenv=self.absent)
        for part in (self.NAME, "absent.env", "NEXT_ADMIN_API_TOKEN"):
            self.assertIn(part, str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError):
            ca.load_token("teststore", env={self.NAME: "<paste-token-here>"}, dotenv=self.absent)
        # An empty store line stops the run; it never falls through to the generic token.
        self.dotenv.write_text(f"{self.NAME}=\n")
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.load_token("teststore", env=generic, dotenv=self.dotenv)
        self.assertIn("empty", str(cm.exception))

    def test_dotenv_reads_one_line_only(self):
        marker = Path(self.tmp.name) / "executed"
        self.dotenv.write_text(
            "# Next Commerce Admin API tokens, one line per store\n"
            "OTHER_NEXT_ADMIN_API_TOKEN=other-value\n"
            f"$(touch {marker})\n"
            f"{self.NAME}_OLD=stale\n"
            f'{self.NAME}="abc=123"\r\n'
        )
        self.assertEqual(ca.load_token("teststore", env={}, dotenv=self.dotenv), "abc=123")
        self.assertEqual(ca.load_token("other", env={}, dotenv=self.dotenv), "other-value")
        self.assertFalse(marker.exists())

    def test_store_choice_and_token_agree(self):
        """Origin and credential come from the same parsed --store, so a repeated
        flag can never pair one store's token with another store's host."""
        seen = []

        class Recorder:
            def __init__(self, origin, token, **kw):
                seen.append((origin, token))
                raise ca.CampaignAdminError("stop before any request")

        cwd = os.getcwd()
        os.chdir(self.tmp.name)  # no stray .env from the caller's directory
        self.addCleanup(os.chdir, cwd)
        both = {"STORE_A_NEXT_ADMIN_API_TOKEN": "a", "STORE_B_NEXT_ADMIN_API_TOKEN": "b"}
        argv = ["discover", "--store", "store-a", "--store", "store-b", "--out", self.tmp.name]
        with mock.patch.object(ca, "Client", Recorder), mock.patch.dict(ca.os.environ, both, clear=True):
            self.assertEqual(ca.main(argv), 1)
        self.assertEqual(seen, [("https://store-b.29next.store", "b")])

        seen.clear()
        only_a = {"STORE_A_NEXT_ADMIN_API_TOKEN": "a"}
        with mock.patch.object(ca, "Client", Recorder), mock.patch.dict(ca.os.environ, only_a, clear=True), \
                mock.patch.object(ca, "print", builtins.print), \
                mock.patch("sys.stderr", new_callable=io.StringIO) as err:
            self.assertEqual(ca.main(argv), 1)
        self.assertEqual(seen, [])
        self.assertIn("STORE_B_NEXT_ADMIN_API_TOKEN", err.getvalue())


class Timestamps(unittest.TestCase):
    def test_same_instant_across_offsets(self):
        self.assertTrue(ca.same_instant("2026-09-02T11:03:55.909+02:00", "2026-09-02T02:03:55.909-07:00"))
        self.assertTrue(ca.same_instant("2026-09-02T09:03:55.909Z", "2026-09-02T11:03:55.909+02:00"))
        self.assertFalse(ca.same_instant("2026-09-02T11:03:55+02:00", "2026-09-02T11:03:55+03:00"))
        self.assertFalse(ca.same_instant(None, "2026-09-02T11:03:55Z"))


class ClientBehaviour(unittest.TestCase):
    def test_cross_origin_and_redirect_refused(self):
        t = FakeTransport({("GET", "/api/admin/store/"): (200, {"name": "x"})})
        c = make_client(t)
        with self.assertRaises(ca.CampaignAdminError):
            c.request("GET", "https://evil.example/api/admin/store/")
        with self.assertRaises(ca.CampaignAdminError):
            c.request("GET", "https://other.29next.store/api/admin/store/")
        self.assertEqual(t.calls, [])
        t2 = FakeTransport({("GET", "/api/admin/store/"): (302, None)})
        with self.assertRaises(ca.CampaignAdminError):
            make_client(t2).request("GET", "/api/admin/store/")

    def test_get_ok_raises_status_error(self):
        for status in (401, 403, 500):
            t = FakeTransport({("GET", "/api/admin/store/"): (status, {"detail": "no"})})
            with self.assertRaises(ca.HttpStatusError) as cm:
                make_client(t).get_ok("/api/admin/store/")
            self.assertEqual(cm.exception.status, status)
            if status in (401, 403):
                self.assertIn("metadata:read", str(cm.exception))
                self.assertIn("campaigns:write", str(cm.exception))

    def test_cross_origin_pagination_next_aborts(self):
        t = FakeTransport({("GET", "/api/admin/products/"): (200, {"results": [{"id": 1}], "next": "https://evil.example/p?page=2"})})
        with self.assertRaises(ca.CampaignAdminError):
            make_client(t).paginate("/api/admin/products/")

    def test_pagination_two_pages_and_loop_guard(self):
        t = FakeTransport({("GET", "/api/admin/products/"): lambda path, body: (200, {
            "results": [{"id": 2}], "next": None} if "page=2" in path else {
            "results": [{"id": 1}], "next": ORIGIN + "/api/admin/products/?page=2"})})
        self.assertEqual([p["id"] for p in make_client(t).paginate("/api/admin/products/")], [1, 2])
        t2 = FakeTransport({("GET", "/api/admin/products/"): (200, {"results": [], "next": ORIGIN + "/api/admin/products/"})})
        with self.assertRaises(ca.CampaignAdminError):
            make_client(t2).paginate("/api/admin/products/")

    def test_pacing_and_headers(self):
        sleeps = []
        t = FakeTransport({("GET", "/api/admin/store/"): (200, {})})
        clock = FakeClock()
        c = ca.Client(ORIGIN, "tok", transport=t, clock=clock, sleep=sleeps.append)
        c.request("GET", "/api/admin/store/")
        c.request("GET", "/api/admin/store/")
        self.assertTrue(sleeps and sleeps[0] > 0.25)
        hdrs = t.calls[0][3]
        self.assertEqual(hdrs.get("Authorization"), "Bearer tok")
        self.assertEqual(hdrs.get("X-29next-api-version"), ca.API_VERSION)

    def test_get_retries_with_retry_after_but_post_never(self):
        sleeps, n = [], {"i": 0}

        def flaky(path, body):
            n["i"] += 1
            return (429, ({}, {"Retry-After": "2"})) if n["i"] == 1 else (200, {"ok": True})
        t = FakeTransport({("GET", "/api/admin/store/"): flaky, ("POST", "/api/admin/campaigns/"): (503, {})})
        c = ca.Client(ORIGIN, "tok", transport=t, clock=FakeClock(), sleep=sleeps.append)
        status, body = c.request("GET", "/api/admin/store/")
        self.assertEqual((status, body), (200, {"ok": True}))
        self.assertIn(2.0, sleeps)
        posts_before = len(t.calls)
        status, _ = c.request("POST", "/api/admin/campaigns/", {"a": 1})
        self.assertEqual(status, 503)
        self.assertEqual(len(t.calls), posts_before + 1)


def git_init(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)


class OutputDirectory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()

    def outside_any_repo(self):
        if ca._repo_marker(self.root):
            self.skipTest("the temp directory sits inside a git repository")

    def test_default_dir_outside_any_repo(self):
        self.outside_any_repo()
        default = self.root / ca.RUNS_DIR_NAME / "teststore"
        self.assertEqual(ca.resolve_out_dir(None, default), default)
        self.assertTrue(default.is_dir())

    def test_repo_without_ignore_refuses(self):
        git_init(self.root)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.resolve_out_dir(None, self.root / ca.RUNS_DIR_NAME / "teststore")
        self.assertIn(ca.RUNS_DIR_NAME + "/", str(cm.exception))
        self.assertFalse((self.root / ca.RUNS_DIR_NAME).exists())

    def test_repo_with_ignore_accepts(self):
        git_init(self.root)
        (self.root / ".gitignore").write_text(ca.RUNS_DIR_NAME + "/\n")
        out = ca.resolve_out_dir(None, self.root / ca.RUNS_DIR_NAME / "teststore")
        self.assertTrue(out.is_dir())

    def test_out_flag_outside_repo_accepts(self):
        self.outside_any_repo()
        repo = self.root / "repo"
        repo.mkdir()
        git_init(repo)
        elsewhere = self.root / "elsewhere"
        self.assertEqual(ca.resolve_out_dir(str(elsewhere), repo / "unused"), elsewhere)
        with self.assertRaises(ca.CampaignAdminError):
            ca.resolve_out_dir(str(repo / "notignored"), repo / "unused")

    def test_symlink_inside_repo_refuses(self):
        git_init(self.root)
        (self.root / ".gitignore").write_text("runs/\nlink\n")
        (self.root / "runs").mkdir()
        (self.root / "link").symlink_to(self.root / "runs")
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.resolve_out_dir(str(self.root / "link" / "a"), self.root)
        self.assertIn("symlink", str(cm.exception))

    def test_outside_symlink_into_repo_refuses(self):
        repo = self.root / "repo"
        repo.mkdir()
        git_init(repo)
        (repo / "tracked").mkdir()
        alias = self.root / "alias"
        alias.symlink_to(repo / "tracked")
        with self.assertRaises(ca.CampaignAdminError):
            ca.resolve_out_dir(str(alias / "run"), self.root)
        self.assertFalse((repo / "tracked" / "run").exists())

    def test_no_marker_no_git_allows(self):
        self.outside_any_repo()
        with mock.patch.object(ca.subprocess, "run", side_effect=OSError("no git")):
            out = ca.resolve_out_dir(None, self.root / "runs" / "teststore")
        self.assertTrue(out.is_dir())

    def test_marker_without_git_refuses(self):
        (self.root / ".git").mkdir()
        with mock.patch.object(ca.subprocess, "run", side_effect=OSError("no git")):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.resolve_out_dir(None, self.root / "runs" / "teststore")
        self.assertIn("could not be asked", str(cm.exception))

    def test_marker_git_error_refuses(self):
        (self.root / ".git").mkdir()
        fatal = subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: detected dubious ownership")
        with mock.patch.object(ca.subprocess, "run", return_value=fatal):
            with self.assertRaises(ca.CampaignAdminError):
                ca.resolve_out_dir(None, self.root / "runs" / "teststore")

    def test_git_ignored_return_codes(self):
        for rc, want in ((0, True), (1, False), (128, None)):
            done = subprocess.CompletedProcess([], rc, stdout=b"", stderr=b"")
            with mock.patch.object(ca.subprocess, "run", return_value=done):
                self.assertIs(ca._git_ignored(self.root / "x", self.root), want)
        with mock.patch.object(ca.subprocess, "run", side_effect=OSError("no git")):
            self.assertIsNone(ca._git_ignored(self.root / "x", self.root))

    def test_git_file_counts_as_marker(self):
        (self.root / "wt").mkdir()
        (self.root / "wt" / ".git").write_text("gitdir: /nowhere\n")
        self.assertEqual(ca._repo_marker(self.root / "wt" / "runs" / "x"), self.root / "wt")

    def test_manifest_written_atomically_with_mode_600(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            ca.atomic_write_json(p, {"a": 1}, mode=0o600)
            self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")
            self.assertEqual(json.loads(p.read_text()), {"a": 1})
            self.assertEqual([x.name for x in Path(d).iterdir()], ["m.json"])


class Recommendation(unittest.TestCase):
    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_low_ctc_multi_variant(self):
        plan = ca.recommend(self.disc, ns(exit_code="BRACELET10"))
        self.assertEqual(ca.validate_plan(plan), [])
        hero = [p for p in plan["packages"] if p["role"] == "hero"]
        self.assertEqual(len(hero), 4)
        self.assertEqual(hero[0]["name"], "Photo Bracelet")
        self.assertEqual(hero[0]["variant_title"], "Photo Bracelet - 6.5 in")
        self.assertTrue(all(p["price"] == "49.95" for p in hero))
        tiers = [o for o in plan["offers"] if o["key"].startswith("tier-")]
        self.assertEqual([o["condition"]["value"] for o in tiers], [1, 2, 3])
        self.assertEqual([o["benefit"]["value"] for o in tiers], ["50.00", "55.00", "60.00"])
        for o in tiers:
            self.assertEqual(sorted(o["condition"]["package_keys"]), sorted(p["key"] for p in hero))
            self.assertEqual(o["offer_type"], "offer")
        exit_ = next(o for o in plan["offers"] if o["key"] == "exit-pop")
        self.assertEqual((exit_["offer_type"], exit_["code"], exit_["benefit"]["value"]), ("voucher", "BRACELET10", "10.00"))
        landed = {l["tier"]: l for l in plan["landed_prices"]}
        self.assertEqual(landed["Buy 1"]["unit_after"], "24.97")   # 49.95 - round(24.975)=24.98 -> 24.97
        self.assertEqual(landed["Buy 2"]["unit_after"], "22.48")   # 49.95 - 27.47
        self.assertEqual(landed["Buy 2"]["order_total"], "44.96")
        self.assertEqual(landed["Buy 3"]["unit_after"], "19.98")   # 49.95 - 29.97
        self.assertEqual(plan["campaign"]["payment_gateway_group_id"], 1)
        self.assertEqual(plan["campaign"]["available_payment_methods"], ["bankcard"])
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(plan["offer_kind"], "quantity")
        self.assertFalse(any(o["key"].startswith("bxgy-") or o["key"] == "gift-free" for o in plan["offers"]))

    def test_rounding_applies_charm_cents(self):
        plan = ca.recommend(self.disc, ns(rounding="0.95"))
        landed = {l["tier"]: l for l in plan["landed_prices"]}
        self.assertEqual(landed["Buy 1"]["unit_after"], "24.95")
        self.assertEqual(plan["offers"][0]["benefit"]["price_rounding"], "0.95")

    def assertRoundingRefused(self, *needles, **kw):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(**kw))
        msg = str(cm.exception)
        for n in needles + ("not below", "--rounding"):
            self.assertIn(n, msg)
        return msg

    def test_rounding_above_anchor_refused_tier(self):
        # 20.50 at 1%: discount 0.21 -> 20.29, floored and pinned to 20.95
        self.assertRoundingRefused("Buy 1", "20.29", "20.95", "20.50",
                                   anchor_price="20.50", tiers="1,2,3", rounding="0.95", exit="0")

    def test_rounding_equal_to_anchor_refused_tier(self):
        self.assertRoundingRefused("Buy 1", "20.74", "20.95",
                                   anchor_price="20.95", tiers="1,50,60", rounding="0.95", exit="0")

    def test_rounding_refused_bxgy(self):
        self.assertRoundingRefused("Buy 99 get 1 free", anchor_price="20.50", offer_type="bxgy",
                                   paid_qty=99, free_qty=1, rounding="0.95", exit="0")

    def test_rounding_refused_upsell(self):
        self.assertRoundingRefused("upsell-16", "39.55", "39.95", hero=10, ctc="high",
                                   anchor_price="189.95", upsell=["16:39.95:1"], rounding="0.95", exit="0")

    def test_rounding_refused_exit_voucher(self):
        self.assertRoundingRefused("Exit - 2% on Buy 1", "24.45", "24.95", rounding="0.95", exit="2")

    def test_rounding_exit_checked_on_every_tier(self):
        # Buy 1 at 24.99 -> 23.99 clears; Buy 2 at 22.99 -> 22.07 -> 22.99 does not.
        msg = self.assertRoundingRefused("Exit - 4% on Buy 2", "22.07", "22.99", rounding="0.99", exit="4")
        self.assertNotIn("on Buy 1", msg)
        ca.recommend(self.disc, ns(rounding="0.99", exit="5"))

    def test_high_ctc_with_bumps_and_upsells(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          bump=["7:9.95"], upsell=["16:39.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertFalse([o for o in plan["offers"] if o["key"].startswith("tier-")])
        roles = sorted(p["role"] for p in plan["packages"])
        self.assertEqual(roles, ["bump", "hero", "hero", "hero", "hero", "upsell"])
        up = next(o for o in plan["offers"] if o["key"].startswith("upsell-"))
        self.assertEqual((up["offer_type"], up["code"], up["condition"]["package_keys"]), ("voucher", "PHOTOMAGNET50", ["upsell-16"]))
        self.assertTrue(any(o["key"] == "exit-pop" for o in plan["offers"]))

    def test_inputs_never_inferred(self):
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(bump=["7"]))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(upsell=["16:39.95"]))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(shipping=[]))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(ctc="medium"))

    def test_store_level_choices(self):
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(currency="EUR"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(payment_methods="paypal"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(shipping=["express:1.00"]))
        two = json.loads(json.dumps(self.disc))
        two["gateway_groups"].append({"id": 2, "name": "Alt", "currencies": ["USD"], "payment_methods": ["bankcard"], "express_payment_methods": []})
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(two, ns())
        self.assertEqual(ca.recommend(two, ns(gateway_group=2))["campaign"]["payment_gateway_group_id"], 2)

    def test_metadata_gap_and_offers_unsupported(self):
        d = json.loads(json.dumps(self.disc))
        d["metadata_missing"] = ["nc_campaign_id"]
        d["offers_supported"] = False
        plan = ca.recommend(d, ns())
        self.assertTrue(plan["blockers"] and "nc_campaign_id" in plan["blockers"][0])
        for part in ("next-campaigns-create.sh metadata --store teststore --apply", "metadata:write", "re-run discover"):
            self.assertIn(part, plan["blockers"][0])
        self.assertEqual(plan["offers"], [])
        self.assertTrue(any("dashboard" in h for h in plan["handoff"]))

    def test_recommend_emits_no_package_image(self):
        """An override is hand-authored. recommend builds from the catalogue, which is
        the same source the package already inherits its image from."""
        plan = ca.recommend(load_fixture("discovery.json"),
                            ns(bump=["7:19.95"], upsell=["16:39.95:20"]))
        self.assertTrue(all("image" not in p for p in plan["packages"]))
        self.assertFalse([r for r in ca.render_requests(plan) if r[0] == "PUT"])

    def test_bxgy_buy_1_get_1(self):
        plan = ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=1, free_qty=1))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(plan["offer_kind"], "bxgy")
        o = next(x for x in plan["offers"] if x["key"] == "bxgy-1-1")
        self.assertEqual((o["condition"]["type"], o["condition"]["value"], o["benefit"]["value"]),
                         ("count", 2, "50.00"))
        self.assertFalse([x for x in plan["offers"] if x["key"].startswith("tier-")])
        buy1 = next(l for l in plan["landed_prices"] if l["tier"] == "Buy 1")
        self.assertNotIn("paid_qty", buy1)
        self.assertNotIn("approximation", buy1)
        deal = next(l for l in plan["landed_prices"] if l["qty"] == 2)
        self.assertEqual((deal["unit_after"], deal["order_total"]), ("24.97", "49.94"))
        self.assertEqual(deal["paid_qty"], 1)
        self.assertEqual(deal["pct"], 50)

    def test_bxgy_buy_2_get_1(self):
        plan = ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=2, free_qty=1))
        self.assertEqual(ca.validate_plan(plan), [])
        o = next(x for x in plan["offers"] if x["key"] == "bxgy-2-1")
        self.assertEqual(o["condition"]["value"], 3)
        self.assertEqual(o["benefit"]["value"], "33.33")
        self.assertEqual(o["benefit"]["type"], "package_percentage")
        self.assertTrue(o["name"].startswith("Photo Bracelet - Buy 2 get 1 free"))
        landed = {l["tier"]: l for l in plan["landed_prices"]}
        self.assertEqual(landed["Buy 1"]["unit_after"], "49.95")
        self.assertNotIn("paid_qty", landed["Buy 1"])
        self.assertNotIn("approximation", landed["Buy 1"])
        deal = landed["Buy 2 get 1 free"]
        self.assertEqual((deal["qty"], deal["unit_after"], deal["order_total"], deal["payable"], deal["savings"]),
                         (3, "33.30", "99.90", "99.90", "49.95"))
        self.assertEqual(deal["pct"], "33.33")
        extra = next(l for l in plan["landed_prices"] if l["qty"] == 4)
        self.assertEqual(extra["order_total"], "133.20")
        self.assertTrue(extra["approximation"])
        self.assertTrue(any("does not repeat" in r for r in plan["rationale"]))
        self.assertTrue(any("proportional-off-all" in r for r in plan["rationale"]))

    def test_bxgy_buy_3_get_2(self):
        plan = ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=3, free_qty=2))
        self.assertEqual(ca.validate_plan(plan), [])
        o = next(x for x in plan["offers"] if x["key"] == "bxgy-3-2")
        self.assertEqual((o["condition"]["value"], o["benefit"]["value"]), (5, "40.00"))
        deal = next(l for l in plan["landed_prices"] if l["qty"] == 5)
        self.assertEqual((deal["unit_after"], deal["order_total"]), ("29.97", "149.85"))

    def _bxgy_2_1(self, **kw):
        plan = ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=2, free_qty=1, **kw))
        deal = next(l for l in plan["landed_prices"] if l["qty"] == 3)
        note = next(r for r in plan["rationale"] if r.startswith("Buy 2 get 1 free is an approximation"))
        return deal, note

    def test_bxgy_rationale_says_match_only_when_totals_match(self):
        deal, note = self._bxgy_2_1()
        self.assertEqual(deal["order_total"], "99.90")
        self.assertIn("lands at 99.90, which matches paying for the paid quantity", note)

    def test_bxgy_rationale_states_landed_total_when_it_differs(self):
        cases = [
            (dict(rounding="0.95"), "101.85", "99.90"),          # charm cents
            (dict(anchor_price="59.99"), "120.00", "119.98"),    # discount rounded to cents
            (dict(anchor_price="300"), "600.03", "600.00"),      # 33.33% held to 2 decimals
        ]
        for kw, landed, paid in cases:
            with self.subTest(**kw):
                deal, note = self._bxgy_2_1(**kw)
                self.assertEqual(deal["order_total"], landed)
                self.assertIn(f"lands at {landed}, not the {paid} that paying for 2", note)
                self.assertIn("Customer copy must quote the landed total", note)
                self.assertNotIn("matches paying", note)

    def test_bxgy_refuses_tiers_high_ctc_and_missing_qty(self):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=2, free_qty=1, tiers="50,55,60"))
        self.assertIn("--tiers", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=2, free_qty=1, ctc="high"))
        self.assertIn("high", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="bxgy"))
        self.assertIn("--paid-qty", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(offer_type="quantity", paid_qty=2, free_qty=1))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=100, free_qty=1))
        self.assertIn("<= 99", str(cm.exception))

    def test_gwp_gift_scoped_100(self):
        plan = ca.recommend(self.disc, ns(offer_type="gwp", gift=["7:24.95"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(plan["offer_kind"], "gwp")
        self.assertFalse([o for o in plan["offers"] if o["key"].startswith("tier-")])
        gift = next(p for p in plan["packages"] if p["role"] == "gift")
        self.assertEqual((gift["key"], gift["price"], gift["product_variant_ids"]), ("gift-7", "24.95", [7]))
        o = next(x for x in plan["offers"] if x["key"] == "gift-free")
        self.assertEqual(o["condition"], {"type": "any", "value": None, "package_keys": ["gift-7"]})
        self.assertEqual(o["benefit"], {"type": "package_percentage", "value": "100.00", "price_rounding": None})
        row = next(l for l in plan["landed_prices"] if l["offer_key"] == "gift-free")
        self.assertEqual((row["kind"], row["unit_after"], row["order_total"]), ("single", "0.00", "0.00"))
        hero_row = next(l for l in plan["landed_prices"] if l["tier"] == "Buy 1")
        self.assertEqual(hero_row["unit_after"], "49.95")
        self.assertTrue(any("noSlot" in h for h in plan["handoff"]))
        self.assertTrue(any("Min-spend" in h for h in plan["handoff"]))

    def test_gwp_low_ctc_rationale(self):
        plan = ca.recommend(self.disc, ns(offer_type="gwp", ctc="low", gift=["7:24.95"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertFalse([o for o in plan["offers"] if o["key"].startswith("tier-")])
        self.assertTrue(any("GWP at low CTC" in r for r in plan["rationale"]))
        self.assertTrue(any("--offer-type quantity --gift" in r for r in plan["rationale"]))

    def test_gwp_rounding_forced_none_and_quantity_plus_gift(self):
        mixed = ca.recommend(self.disc, ns(gift=["7:24.95"], rounding="0.95"))
        self.assertEqual(ca.validate_plan(mixed), [])
        self.assertEqual(mixed["offer_kind"], "quantity")
        tiers = [o for o in mixed["offers"] if o["key"].startswith("tier-")]
        self.assertEqual([o["benefit"]["value"] for o in tiers], ["50.00", "55.00", "60.00"])
        self.assertTrue(all(o["benefit"]["price_rounding"] == "0.95" for o in tiers))
        gift = next(o for o in mixed["offers"] if o["key"] == "gift-free")
        self.assertEqual(gift["benefit"]["price_rounding"], None)
        self.assertEqual(gift["condition"]["package_keys"], ["gift-7"])
        self.assertTrue(all("gift-7" not in o["condition"]["package_keys"] for o in tiers))
        ids = {p["key"]: 100 + i for i, p in enumerate(mixed["packages"])}
        cases = ca._cart_cases_from_plan(mixed, ids)
        mixed_case = next(c for c in cases if "Buy 1 + Gift" in c[0])
        self.assertEqual(mixed_case[3], Decimal("24.95"))  # rounded 50% hero + free gift

    def test_multi_gift_mixed_cart_cases(self):
        plan = ca.recommend(self.disc, ns(offer_type="gwp", gift=["7:24.95", "8:15.00"]))
        self.assertEqual(ca.validate_plan(plan), [])
        gifts = [p for p in plan["packages"] if p["role"] == "gift"]
        self.assertEqual(sorted(p["key"] for p in gifts), ["gift-7", "gift-8"])
        gift_offer = next(o for o in plan["offers"] if o["key"] == "gift-free")
        self.assertEqual(sorted(gift_offer["condition"]["package_keys"]), ["gift-7", "gift-8"])
        ids = {p["key"]: 100 + i for i, p in enumerate(plan["packages"])}
        mixed_names = [c[0] for c in ca._cart_cases_from_plan(plan, ids) if "Buy 1 + Gift" in c[0]]
        self.assertEqual(len(mixed_names), 2)
        self.assertTrue(any("Memorial Ornament" in n for n in mixed_names))

    def test_gwp_refusals(self):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="gwp"))
        self.assertIn("--gift", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(offer_type="gwp", gift=["7:24.95"], tiers="50,55,60"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(offer_type="gwp", gift=["7:24.95"], paid_qty=2, free_qty=1))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(offer_type="gwp", gift=["23:24.95"]))
        self.assertIn("already", str(cm.exception))
        d = json.loads(json.dumps(self.disc))
        for p in d["products"]:
            for v in p["variants"]:
                if v["id"] == 7:
                    v["purchase_availability"] = "unavailable"
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(d, ns(offer_type="gwp", gift=["7:24.95"]))
        self.assertIn("not purchasable", str(cm.exception))

    def test_bxgy_helpers(self):
        self.assertEqual(ca.money(ca.bxgy_percentage(1, 1)), "50.00")
        self.assertEqual(ca.money(ca.bxgy_percentage(2, 1)), "33.33")
        self.assertEqual(ca.money(ca.bxgy_percentage(3, 2)), "40.00")
        self.assertEqual(ca.true_bxgy_payable(Decimal("49.95"), 2, 1, 3), Decimal("99.90"))
        self.assertEqual(ca.true_bxgy_payable(Decimal("49.95"), 2, 1, 4), Decimal("149.85"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.bxgy_percentage(0, 1)
        with self.assertRaises(ca.CampaignAdminError):
            ca.bxgy_percentage(2, 0)
        with self.assertRaises(ca.CampaignAdminError):
            ca.bxgy_percentage(100, 1)


class PlanValidation(unittest.TestCase):
    def setUp(self):
        self.plan = ca.recommend(load_fixture("discovery.json"), ns())

    def _errs(self, mutate):
        p = json.loads(json.dumps(self.plan))
        mutate(p)
        return ca.validate_plan(p)

    def test_rejections(self):
        def all_packages(p): p["offers"][0]["condition"]["all_packages"] = True
        def voucher_no_code(p): p["offers"][0]["offer_type"] = "voucher"; p["offers"][0]["code"] = None
        def count_no_value(p): p["offers"][0]["condition"]["value"] = None
        def bad_decimal(p): p["packages"][0]["price"] = "49.955"
        def dup_names(p): p["offers"][1]["name"] = p["offers"][0]["name"]
        def qty_pkg(p): p["packages"][0]["name"] = "2x Bracelet"
        def bad_origin(p): p["store_origin"] = "https://evil.example"
        def dangling(p): p["offers"][0]["condition"]["package_keys"] = ["nope"]
        def multi_variant(p): p["packages"][0]["product_variant_ids"] = [23, 24]
        def dup_offer_key(p): p["offers"][1]["key"] = p["offers"][0]["key"]
        def dup_ship_key(p): p["shipping_methods"].append({"shipping_method": "standard", "price": "8.95"})
        def same_code_same_price(p): p["shipping_methods"].append({"shipping_method": "standard", "price": "6.95", "key": "again"})
        for m in (all_packages, voucher_no_code, count_no_value, bad_decimal, dup_names, qty_pkg, bad_origin, dangling, multi_variant, dup_offer_key,
                  dup_ship_key, same_code_same_price):
            self.assertTrue(self._errs(m), m.__name__)

    def test_request_rendering_order(self):
        reqs = ca.render_requests(self.plan)
        self.assertEqual(reqs[0][0:2], ("POST", "/api/admin/campaigns/"))
        kinds = [r[1].rsplit("/", 2)[-2] for r in reqs[1:]]
        self.assertEqual(kinds, ["packages"] * 4 + ["shipping-methods"] + ["offers"] * 4)
        self.assertEqual(reqs[0][2]["available_payment_methods"], ["bankcard"])

    def test_package_image_accepts_an_https_src(self):
        def ok(p): p["packages"][0]["image"] = {"src": "https://cdn.example.com/a.png", "file_name": "hero"}
        self.assertEqual(self._errs(ok), [])

    def test_package_image_null_is_absent(self):
        def none(p): p["packages"][0]["image"] = None
        self.assertEqual(self._errs(none), [])
        self.assertIsNone(ca.image_body({"image": None}))
        self.assertIsNone(ca.image_body({}))

    def test_package_image_rejects_bad_src(self):
        bad = [
            {"src": "http://cdn.example.com/a.png"},
            {"src": "data:image/png;base64,AAAA"},
            {"src": "//cdn.example.com/a.png"},
            {"src": "/local/a.png"},
            {"src": "https://user:pw@" + "cdn.example.com/a.png"},
            {"src": "https://cdn.example.com/a b.png"},
            {"src": "https://cdn.example.com/a.png\n"},
            {"src": ""},
            {"src": 5},
            {},
            "https://cdn.example.com/a.png",
            [],
        ]
        for img in bad:
            def mutate(p, img=img): p["packages"][0]["image"] = img
            errs = [e for e in self._errs(mutate) if "image" in e]
            self.assertTrue(errs, f"expected a rejection for {img!r}")

    def test_package_image_rejects_attachment(self):
        for img in ({"attachment": "AAAA"}, {"src": "https://cdn.example.com/a.png", "attachment": "AAAA"}):
            def mutate(p, img=img): p["packages"][0]["image"] = img
            self.assertTrue([e for e in self._errs(mutate) if "attachment" in e], img)

    def test_image_rejects_unknown_fields(self):
        def mutate(p): p["packages"][0]["image"] = {"src": "https://cdn.example.com/a.png", "alt": "x"}
        self.assertTrue([e for e in self._errs(mutate) if "'alt'" in e or "alt" in e])

    def test_package_image_rejects_bad_file_name(self):
        for fn in ("../../etc/passwd", "a/b", "a\\b", "..", ".", "", "   ", "x" * 101, 7):
            def mutate(p, fn=fn):
                p["packages"][0]["image"] = {"src": "https://cdn.example.com/a.png", "file_name": fn}
            self.assertTrue([e for e in self._errs(mutate) if "file_name" in e], repr(fn))

    def test_image_body_shape(self):
        src = "https://cdn.example.com/a.png"
        self.assertEqual(ca.image_body({"image": {"src": src, "file_name": "hero"}}),
                         {"src": src, "file_name": "hero"})
        self.assertEqual(ca.image_body({"image": {"src": src}}), {"src": src})
    def test_bad_port_says_so_instead_of_blaming_the_scheme(self):
        """A perfectly https URL with a broken port must not be told it is not https."""
        for src in ("https://example.com:notaport/a.png", "https://example.com:99999/a.png"):
            def mutate(p, src=src): p["packages"][0]["image"] = {"src": src}
            errs = [e for e in self._errs(mutate) if "image.src" in e]
            self.assertTrue(errs, src)
            self.assertIn("not a parseable URL", errs[0], src)

    def test_malformed_src_is_an_error_not_a_traceback(self):
        """urlsplit raises on a bad authority, and a netloc can exist with no hostname."""
        for src in ("https://[::1", "https://:8080/a.png", "https://[::1]:notaport/a.png"):
            def mutate(p, src=src): p["packages"][0]["image"] = {"src": src}
            self.assertTrue([e for e in self._errs(mutate) if "image.src" in e], src)


CODE_LIST_FIELDS = ("available_payment_methods", "available_express_payment_methods",
                    "available_shipping_countries")


def _offer_conflict(state, cid, body, exclude_id=None):
    """(status, body) when an offer write would duplicate a name or voucher code on
    this campaign, else None. The live API keeps both unique per campaign, which is
    what makes an offer rename sequence (a swap of two names) fail mid-run."""
    for other in state["offers"].get(cid, {}).values():
        if other.get("id") == exclude_id:
            continue
        if body.get("name") is not None and other.get("name") == body["name"]:
            return 400, {"name": [f"Offer with name '{body['name']}' already exists on this campaign."]}
        if body.get("code") and other.get("code") == body["code"]:
            return 400, {"code": [f"Offer with code '{body['code']}' already exists on this campaign."]}
    return None


def _offers_referencing(state, cid, pid):
    """Ids of the live offers whose scope names package `pid`."""
    return [o["id"] for o in state["offers"].get(cid, {}).values()
            if any(p.get("id") == pid for p in (o.get("condition") or {}).get("packages") or [])]


def offer_condition(state, cond):
    """An offer condition in the shape the live API returns it: package ids as
    objects, the `count` threshold as a decimal string ("2.00"), null on an `any`
    offer. A store whose read-back omits the threshold is
    state["offer_value_readable"] = False."""
    out = {"type": cond["type"], "all_packages": bool(cond.get("all_packages")),
           "packages": [{"id": i} for i in cond.get("package_ids") or []],
           "description": ""}
    if cond["type"] != "count":
        out["value"] = None
    elif state.get("offer_value_readable", True):
        out["value"] = "%.2f" % float(cond["value"])
    return out


def offer_benefit(benefit, previous=None):
    """An offer benefit as the live API returns it: `price_rounding` is always a
    key, `description` is ignored by everything that reads it. A PATCH body merges
    onto the benefit already there, so a field it does not carry is kept; a create
    body has no previous benefit and sets all of it."""
    out = {"type": None, "value": None, "price_rounding": None, "description": ""}
    for f in ("type", "value", "price_rounding"):
        if previous and f in previous:
            out[f] = previous[f]
        if f in benefit:
            out[f] = benefit[f]
    return out


def _live_name(state, bare, item):
    """A package name as the API stores it: the sent name with the variant suffix
    appended, unless the store is one that leaves it alone."""
    if not state.get("patch_appends_suffix", True):
        return bare
    suffix = item.get("product_variant_name") or ("v" + str(item.get("product_variant_id")))
    return f"{bare} - {suffix}"


def _recurring_fields(item, body):
    """The recurring trio as a package read-back carries it: `interval` is "" and
    `interval_count` null on a one-off package."""
    price_recurring = next((x.get("price_recurring") for x in item.get("prices") or []
                            if x.get("currency") == "USD"), None)
    item["is_recurring"] = bool(price_recurring)
    item["interval"] = body.get("interval", item.get("interval", "")) or ""
    item["interval_count"] = body.get("interval_count", item.get("interval_count"))
    if not price_recurring:
        item["interval"], item["interval_count"] = "", None


def _forex_price(price, currency, state=None):
    """`price` converted into `currency` at the fake store's fixed rate, or None when
    there is no rate for it. Only `recalculate_prices: true` reaches this."""
    rate = ((state or {}).get("forex") or FOREX).get(currency)
    if rate is None or price is None:
        return None
    return ca.money(ca.D(price) * ca.D(rate))


def _merge_prices(previous, sent, fields, recalculate, state):
    """Per-currency merge, as the published PATCH contract describes `prices`:
    "Currencies not included are left unchanged". `recalculate_prices: true` converts
    the currencies the body does not name from the campaign-currency price it does
    send, by forex, instead of leaving them alone."""
    out = [dict(x) for x in previous or []]
    by_currency = {x.get("currency"): x for x in out}
    named = set()
    for s in sent:
        currency = s.get("currency", BASE_CURRENCY)
        named.add(currency)
        row = by_currency.get(currency)
        if row is None:
            row = {"currency": currency}
            out.append(row)
            by_currency[currency] = row
        for f in fields:
            row[f] = s.get(f)
    if recalculate and BASE_CURRENCY in named:
        src = by_currency[BASE_CURRENCY]
        for row in out:
            if row.get("currency") in named:
                continue
            for f in fields:
                row[f] = _forex_price(src.get(f), row.get("currency"), state)
    return out


def _package_prices(body, previous=None, state=None):
    """A package `prices` list in the read-back shape, from a create or PATCH body.
    Create sends `price` (plus `price_recurring` on a subscription package); a PATCH
    sends `prices`, which is merged per currency."""
    if "prices" in body:
        return _merge_prices(previous, body["prices"] or [], ("price", "price_recurring"),
                             body.get("recalculate_prices"), state)
    if "price" in body:
        return [{"currency": BASE_CURRENCY, "price": body["price"],
                 "price_recurring": body.get("price_recurring")}]
    return previous or []


def _shipping_prices(body, previous=None, state=None):
    """A campaign shipping method's `prices` list in the read-back shape. A PATCH
    merges per currency, the way the package endpoint does."""
    if "prices" in body:
        return _merge_prices(previous, body["prices"] or [], ("price",),
                             body.get("recalculate_prices"), state)
    if "price" in body:
        return [{"currency": BASE_CURRENCY, "price": body["price"]}]
    return previous or []


def store_routes(disc, state):
    """A tiny fake Admin API backed by `state` dict; enough for apply/teardown/verify."""
    def list_campaigns(path, body):
        return 200, {"results": list(state["campaigns"].values()), "next": None}

    def create_campaign(path, body):
        state["seq"] += 1
        c = dict(body, id=state["seq"], api_key="KEY-" + "x" * 20 + "1234", created_at="2026-09-02T12:00:05+02:00",
                 available_payment_methods=[{"code": m} for m in body.get("available_payment_methods", [])],
                 available_express_payment_methods=[{"code": m} for m in body.get("available_express_payment_methods", [])],
                 available_shipping_countries=[{"code": m} for m in body.get("available_shipping_countries", [])],
                 additional_currencies=body.get("additional_currencies", []),
                 statement_descriptor=body.get("statement_descriptor"))
        state["campaigns"][c["id"]] = c
        return 201, c

    def child_create(kind):
        def h(path, body):
            cid = int(path.split("/")[4])
            if kind == "offers":
                clash = _offer_conflict(state, cid, body)
                if clash:
                    return clash
            state["seq"] += 1
            item = dict(body, id=state["seq"])
            if kind == "packages":
                item["product_variant_id"] = body["product_variant_ids"][0]
                item["product_variant_name"] = "v" + str(item["product_variant_id"])
                item["name"] = _live_name(state, body["name"], item)  # API appends the variant
                item["prices"] = _package_prices(body)
                _recurring_fields(item, body)
                item["product_purchase_availability"] = "available"
                # The real PackageCreateSerializer fetches the catalogue product/variant
                # image and attaches it at create time, so a created package normally
                # already carries one. Mirror that, or every verify PASS assertion breaks.
                item["image"] = state.get("catalogue_image", CATALOGUE_IMAGE)
            if kind == "offers":
                item["condition"] = offer_condition(state, body["condition"])
                item["benefit"] = offer_benefit(body["benefit"])
                item["available"] = body.get("available", True)
                item["code"] = body.get("code")
            if kind == "shipping-methods":
                item["prices"] = _shipping_prices(body)
            state[kind].setdefault(cid, {})[item["id"]] = item
            return 201, item
        return h

    def child_list(kind):
        return lambda path, body: (200, {"results": list(state[kind].get(int(path.split("/")[4]), {}).values()), "next": None})

    routes = {("GET", "/api/admin/campaigns/"): list_campaigns, ("POST", "/api/admin/campaigns/"): create_campaign}
    return routes, child_create, child_list


class DynamicTransport(FakeTransport):
    """Resolves /campaigns/{id}/... paths against a state dict."""

    def __init__(self, disc, state, fail_on=None, image_url=OVERRIDE_IMAGE):
        super().__init__()
        self.state = state
        self.fail_on = fail_on or {}
        self.image_url = image_url
        # One-shot knobs for the edit tests, keyed like fail_on by (method, path):
        # `before` runs a callable just ahead of the request (a dashboard change
        # landing mid-run); `lose` performs the request and then answers 502, the
        # shape of a response lost on the way back; `ignore_patch` answers a
        # PATCH 200 without changing anything.
        self.before = {}
        self.lose = set()
        self.ignore_patch = set()
        self.routes, self.child_create, self.child_list = store_routes(disc, state)

    def _patch(self, cid, kind, item, body):
        """PATCH semantics per section, as the Admin API documents them: a package
        merges `prices` per currency and re-suffixes a sent name, a shipping method
        merges prices the same way (and refuses a code another method on the campaign
        uses), an offer's `condition` is replaced wholly and its `benefit` merges
        field by field."""
        if kind == "packages":
            item["prices"] = _package_prices(body, item.get("prices"), self.state)
            if "name" in body:
                item["name"] = _live_name(self.state, body["name"], item)
            for f, v in body.items():
                if f not in ("prices", "price", "price_recurring", "recalculate_prices",
                             "name", "interval", "interval_count"):
                    item[f] = v
            _recurring_fields(item, body)
            return 200, {}, json.dumps(item)
        if kind == "shipping-methods":
            code = body.get("shipping_method")
            if code and any(x.get("shipping_method") == code and x.get("id") != item["id"]
                            for x in self.state[kind].get(cid, {}).values()):
                return 400, {}, json.dumps({"shipping_method": [
                    f"Shipping method code '{code}' already exists on this campaign. "
                    "Use an offer to charge a different price."]})
            item["prices"] = _shipping_prices(body, item.get("prices"), self.state)
            for f, v in body.items():
                if f not in ("prices", "price", "recalculate_prices"):
                    item[f] = v
            return 200, {}, json.dumps(item)
        if kind == "offers":
            clash = _offer_conflict(self.state, cid, body, exclude_id=item["id"])
            if clash:
                return clash[0], {}, json.dumps(clash[1])
            for f, v in body.items():
                if f == "condition":
                    item["condition"] = offer_condition(self.state, v)
                elif f == "benefit":
                    item["benefit"] = offer_benefit(v, item.get("benefit"))
                else:
                    item[f] = v
            return 200, {}, json.dumps(item)
        return 405, {}, json.dumps({"detail": f"PATCH not supported on {kind}"})

    def __call__(self, req, timeout):
        method = req.get_method()
        path = req.full_url.split(".29next.store", 1)[1]
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((method, path, body, dict(req.headers)))
        base = path.split("?")[0]
        parts = base.strip("/").split("/")
        key = (method, base)
        if key in self.before:
            self.before.pop(key)()
        if key in self.fail_on:
            st = self.fail_on.pop(key)
            return st, {}, json.dumps({"detail": "injected"})
        if key in self.lose:
            self.lose.discard(key)
            self._handle(method, path, base, parts, body)
            return 502, {}, json.dumps({"detail": "response lost"})
        return self._handle(method, path, base, parts, body)

    def _handle(self, method, path, base, parts, body):
        key = (method, base)
        if key in self.routes:
            st, resp = self.routes[key](path, body)
            return st, {}, json.dumps(resp)
        if len(parts) >= 4 and parts[2] == "campaigns":
            cid = int(parts[3])
            if len(parts) == 4:
                if method == "GET":
                    c = self.state["campaigns"].get(cid)
                    if c:
                        # echo the SAME instant in a different offset, to exercise same_instant()
                        from datetime import datetime, timezone, timedelta
                        try:
                            dt = datetime.fromisoformat(str(c.get("created_at", "")).replace("Z", "+00:00"))
                            c = dict(c, created_at=dt.astimezone(timezone(timedelta(hours=-7))).isoformat())
                        except ValueError:
                            pass
                    return (200, {}, json.dumps(c)) if c else (404, {}, "{}")
                if method == "PATCH":
                    c = self.state["campaigns"].get(cid)
                    if c is None:
                        return 404, {}, "{}"
                    for f, v in (body or {}).items():
                        # the code lists go back out expanded, exactly as a GET returns
                        # them, and a clearing null is stored as sent
                        c[f] = [{"code": x} for x in v or []] if f in CODE_LIST_FIELDS else v
                    return 200, {}, json.dumps(c)
                if method == "DELETE":
                    self.state["campaigns"].pop(cid, None)
                    return 204, {}, ""
            kind = parts[4]
            if len(parts) == 5:
                if method == "GET":
                    st, resp = self.child_list(kind)(path, body)
                    return st, {}, json.dumps(resp)
                if method == "POST":
                    st, resp = self.child_create(kind)(path, body)
                    if kind == "packages" and st == 201:
                        resp = [resp]  # the real API answers package create with a one-element list
                    return st, {}, json.dumps(resp)
            if len(parts) == 6:
                iid = int(parts[5])
                item = self.state[kind].get(cid, {}).get(iid)
                if method == "GET":
                    return (200, {}, json.dumps(item)) if item else (404, {}, "{}")
                if method == "PATCH":
                    if item is None:
                        return 404, {}, "{}"
                    if key in self.ignore_patch:
                        return 200, {}, json.dumps(item)
                    return self._patch(cid, kind, item, body or {})
                if method == "DELETE":
                    if kind == "packages" and not self.state.get("cascade_on_package_delete"):
                        # A campaign offer scoping a package pins it: the live API
                        # refuses the delete rather than silently narrowing the offer.
                        refs = _offers_referencing(self.state, cid, iid)
                        if refs:
                            return 400, {}, json.dumps(
                                {"detail": f"package {iid} is referenced by offer(s) {refs}; "
                                           "remove it from the offer first"})
                    self.state[kind].get(cid, {}).pop(iid, None)
                    return 204, {}, ""
            if len(parts) == 7 and kind == "packages" and parts[6] == "image" and method == "PUT":
                item = self.state[kind].get(cid, {}).get(int(parts[5]))
                if item is None:
                    return 404, {}, "{}"
                item["image"] = self.image_url
                # The real endpoint answers with the package DETAIL serializer: a bare
                # object, never the one-element list that package create returns.
                return 200, {}, json.dumps(item)
        return 404, {}, json.dumps({"detail": f"no route {method} {path}"})


def fresh_state():
    """The fake store's state. Optional keys change how it behaves:
    `catalogue_image` (image a package create inherits), `offer_value_readable`
    (False hides a count offer's threshold on read-back, default True),
    `patch_appends_suffix` (False leaves a sent package name bare, default True),
    `cascade_on_package_delete` (True lets a package an offer scopes be deleted,
    default False: the live API answers 400)."""
    return {"seq": 100, "campaigns": {}, "packages": {}, "shipping-methods": {}, "offers": {}}


def seed_live_campaign(state, *, all_packages=False, value_readable=True, recurring=False,
                       extra_currency=None):
    """Seed a campaign nobody's run created: 2 packages, 1 shipping method, and 2
    offers (a count discount on the hero and free shipping), written straight into
    `state` in the shapes the live API reads back. Returns the campaign id.

    `extra_currency` makes it a campaign that prices in a second currency: the code
    lands in `additional_currencies` and every package and shipping method carries a
    converted row beside the USD one, the way a campaign created with additional
    currencies reads back."""
    state["offer_value_readable"] = value_readable
    state["seq"] += 1
    cid = state["seq"]
    state["campaigns"][cid] = {
        "id": cid, "name": "Dashboard Bracelet", "currency": "USD", "language": "en",
        "payment_gateway_group_id": 1, "api_key": "KEY-" + "x" * 20 + "9999",
        "created_at": "2026-08-01T09:00:00+00:00", "statement_descriptor": "",
        "paypal_account_id": None,
        "additional_currencies": [extra_currency] if extra_currency else [],
        "available_payment_methods": [{"code": "card"}],
        "available_express_payment_methods": [],
        "available_shipping_countries": [{"code": "US"}],
    }

    def priced(price, recurs=False):
        rows = [{"currency": BASE_CURRENCY, "price": price,
                 "price_recurring": price if recurs else None}]
        if extra_currency:
            other = _forex_price(price, extra_currency, state)
            rows.append({"currency": extra_currency, "price": other,
                         "price_recurring": other if recurs else None})
        return rows

    pkg_ids = []
    for title, vid, price in (("Photo Bracelet", 801, "24.95"), ("Charm Add-on", 802, "12.95")):
        state["seq"] += 1
        recurs = recurring and not pkg_ids  # the hero only, so one of each shape is seeded
        item = {"id": state["seq"], "product_id": 22, "product_variant_id": vid,
                "product_variant_name": "v" + str(vid), "name": f"{title} - v{vid}",
                "prices": priced(price, recurs),
                "is_recurring": recurs, "interval": "month" if recurs else "",
                "interval_count": 1 if recurs else None,
                "product_purchase_availability": "available",
                "image": state.get("catalogue_image", CATALOGUE_IMAGE)}
        state["packages"].setdefault(cid, {})[item["id"]] = item
        pkg_ids.append(item["id"])
    state["seq"] += 1
    state["shipping-methods"].setdefault(cid, {})[state["seq"]] = {
        "id": state["seq"], "shipping_method": "standard",
        "prices": [{k: v for k, v in row.items() if k != "price_recurring"}
                   for row in priced("6.95")]}
    for name, cond, ben in (
            ("Dashboard Bracelet - Buy 2",
             {"type": "count", "value": 2, "package_ids": [] if all_packages else [pkg_ids[0]],
              "all_packages": all_packages},
             {"type": "package_percentage", "value": "55.00", "price_rounding": None}),
            ("Dashboard Bracelet - Free Shipping",
             {"type": "any", "package_ids": [pkg_ids[0]]},
             {"type": "shipping_percentage", "value": "100.00", "price_rounding": None})):
        state["seq"] += 1
        state["offers"].setdefault(cid, {})[state["seq"]] = {
            "id": state["seq"], "name": name, "offer_type": "offer", "code": None,
            "available": True, "condition": offer_condition(state, cond),
            "benefit": offer_benefit(ben)}
    return cid


class FakeApiContract(unittest.TestCase):
    """The fake store answers the way the live Admin API does. Every refusal the
    update path has to survive (a rejected name, a pinned package) is here, so a
    test that passes against the fake means something."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.state = fresh_state()
        self.t = DynamicTransport(self.disc, self.state)
        self.client = make_client(self.t)

    def seed(self, **kw):
        cid = seed_live_campaign(self.state, **kw)
        return cid, list(self.state["packages"][cid]), list(self.state["offers"][cid]), \
            list(self.state["shipping-methods"][cid])

    def test_seeded_campaign_reads_back_live_shaped(self):
        cid, pids, oids, sids = self.seed()
        camp = self.client.get_ok(f"/api/admin/campaigns/{cid}/")
        self.assertEqual((camp["statement_descriptor"], camp["paypal_account_id"],
                          camp["additional_currencies"]), ("", None, []))
        self.assertEqual([m["code"] for m in camp["available_shipping_countries"]], ["US"])
        pkg = self.client.get_ok(f"/api/admin/campaigns/{cid}/packages/{pids[0]}/")
        self.assertEqual((pkg["product_id"], pkg["product_variant_id"], pkg["product_variant_name"]),
                         (22, 801, "v801"))
        self.assertEqual(pkg["name"], "Photo Bracelet - v801")
        self.assertEqual((pkg["is_recurring"], pkg["interval"], pkg["interval_count"]), (False, "", None))
        self.assertEqual(pkg["prices"][0], {"currency": "USD", "price": "24.95", "price_recurring": None})
        ship = self.client.get_ok(f"/api/admin/campaigns/{cid}/shipping-methods/{sids[0]}/")
        self.assertEqual((ship["shipping_method"], ship["prices"][0]["price"]), ("standard", "6.95"))
        count, free = (self.client.get_ok(f"/api/admin/campaigns/{cid}/offers/{i}/") for i in oids)
        self.assertEqual(count["condition"]["value"], "2.00")  # a string, as the API returns it
        self.assertEqual(count["condition"]["packages"], [{"id": pids[0]}])
        self.assertFalse(count["condition"]["all_packages"])
        self.assertEqual((count["available"], count["code"]), (True, None))
        self.assertIsNone(free["condition"]["value"])
        self.assertIsNone(free["benefit"]["price_rounding"])

    def test_seed_toggles_hide_the_threshold_and_make_the_hero_recurring(self):
        cid, pids, oids, _ = self.seed(value_readable=False, recurring=True, all_packages=True)
        count = self.client.get_ok(f"/api/admin/campaigns/{cid}/offers/{oids[0]}/")
        self.assertNotIn("value", count["condition"])
        self.assertTrue(count["condition"]["all_packages"])
        self.assertEqual(count["condition"]["packages"], [])
        hero = self.client.get_ok(f"/api/admin/campaigns/{cid}/packages/{pids[0]}/")
        self.assertEqual((hero["is_recurring"], hero["interval"], hero["interval_count"]),
                         (True, "month", 1))
        self.assertEqual(hero["prices"][0]["price_recurring"], "24.95")
        bump = self.client.get_ok(f"/api/admin/campaigns/{cid}/packages/{pids[1]}/")
        self.assertEqual((bump["is_recurring"], bump["interval"], bump["interval_count"]),
                         (False, "", None))

    def test_created_package_and_offer_read_back_live_shaped(self):
        _, camp = self.client.request("POST", "/api/admin/campaigns/",
                                      {"name": "Created", "currency": "USD", "language": "en",
                                       "payment_gateway_group_id": 1})
        cid = camp["id"]
        st, resp = self.client.request("POST", f"/api/admin/campaigns/{cid}/packages/",
                                       {"name": "Sub", "product_id": 22, "price": "19.95",
                                        "product_variant_ids": [901], "price_recurring": "17.95",
                                        "interval": "month", "interval_count": 1})
        pkg = resp[0]
        self.assertEqual((st, pkg["name"], pkg["product_variant_name"]), (201, "Sub - v901", "v901"))
        self.assertEqual((pkg["is_recurring"], pkg["interval"], pkg["interval_count"]), (True, "month", 1))
        self.assertEqual(pkg["prices"][0]["price_recurring"], "17.95")
        st, offer = self.client.request("POST", f"/api/admin/campaigns/{cid}/offers/",
                                        {"name": "Buy 2", "offer_type": "offer", "available": False,
                                         "condition": {"type": "count", "value": 2, "all_packages": False,
                                                       "package_ids": [pkg["id"]]},
                                         "benefit": {"type": "package_percentage", "value": "55.00"}})
        self.assertEqual((st, offer["available"], offer["condition"]["value"]), (201, False, "2.00"))
        self.assertIsNone(offer["benefit"]["price_rounding"])

    def test_campaign_patch_merges_and_re_expands_the_code_lists(self):
        cid, _, _, _ = self.seed()
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/",
                                       {"name": "Renamed", "available_shipping_countries": ["US", "CA"],
                                        "available_payment_methods": [],
                                        "additional_currencies": None, "statement_descriptor": None})
        self.assertEqual(st, 200)
        live = self.client.get_ok(f"/api/admin/campaigns/{cid}/")
        self.assertEqual(live["name"], "Renamed")
        self.assertEqual([x["code"] for x in live["available_shipping_countries"]], ["US", "CA"])
        self.assertEqual(live["available_payment_methods"], [])
        self.assertIsNone(live["additional_currencies"])
        self.assertIsNone(live["statement_descriptor"])
        self.assertEqual(live["currency"], "USD")  # untouched fields survive the merge

    def test_package_patch_takes_a_prices_list_and_re_suffixes_the_name(self):
        cid, pids, _, _ = self.seed()
        body = {"name": "Photo Bracelet XL",
                "prices": [{"currency": "USD", "price": "29.95", "price_recurring": None}]}
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/", body)
        self.assertEqual((st, resp["name"]), (200, "Photo Bracelet XL - v801"))
        self.assertEqual(resp["prices"], [{"currency": "USD", "price": "29.95", "price_recurring": None}])
        self.state["patch_appends_suffix"] = False
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/",
                                       {"name": "Bare"})
        self.assertEqual((st, resp["name"]), (200, "Bare"))
        self.assertEqual(resp["prices"][0]["price"], "29.95")  # a name-only PATCH keeps the price

    def test_a_price_patch_leaves_the_currencies_it_does_not_name_alone(self):
        # The published PATCH contract for packages and shipping methods says of
        # `prices`: currencies not included are left unchanged, and
        # `recalculate_prices` is what converts them instead.
        cid, pids, _, sids = self.seed(extra_currency="EUR")
        pkg = f"/api/admin/campaigns/{cid}/packages/{pids[0]}/"
        self.assertEqual([x["price"] for x in self.client.get_ok(pkg)["prices"]],
                         ["24.95", "22.46"])
        st, resp = self.client.request("PATCH", pkg,
                                       {"prices": [{"currency": "USD", "price": "29.95",
                                                    "price_recurring": None}]})
        self.assertEqual(st, 200)
        self.assertEqual([(x["currency"], x["price"]) for x in resp["prices"]],
                         [("USD", "29.95"), ("EUR", "22.46")])
        ship = f"/api/admin/campaigns/{cid}/shipping-methods/{sids[0]}/"
        st, resp = self.client.request("PATCH", ship,
                                       {"prices": [{"currency": "USD", "price": "8.95"}]})
        self.assertEqual((st, [(x["currency"], x["price"]) for x in resp["prices"]]),
                         (200, [("USD", "8.95"), ("EUR", "6.26")]))

    def test_recalculate_prices_converts_the_currencies_the_body_omits(self):
        cid, pids, _, sids = self.seed(extra_currency="EUR")
        st, resp = self.client.request(
            "PATCH", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/",
            {"prices": [{"currency": "USD", "price": "29.95", "price_recurring": None}],
             "recalculate_prices": True})
        self.assertEqual((st, [(x["currency"], x["price"]) for x in resp["prices"]]),
                         (200, [("USD", "29.95"), ("EUR", "26.96")]))
        st, resp = self.client.request(
            "PATCH", f"/api/admin/campaigns/{cid}/shipping-methods/{sids[0]}/",
            {"prices": [{"currency": "USD", "price": "8.95"}], "recalculate_prices": True})
        self.assertEqual((st, [(x["currency"], x["price"]) for x in resp["prices"]]),
                         (200, [("USD", "8.95"), ("EUR", "8.06")]))

    def test_a_price_patch_in_another_currency_adds_that_row(self):
        cid, pids, _, _ = self.seed()
        st, resp = self.client.request(
            "PATCH", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/",
            {"prices": [{"currency": "GBP", "price": "19.95", "price_recurring": None}]})
        self.assertEqual((st, [(x["currency"], x["price"]) for x in resp["prices"]]),
                         (200, [("USD", "24.95"), ("GBP", "19.95")]))

    def test_shipping_patch_sets_prices_and_refuses_another_methods_code(self):
        cid, _, _, sids = self.seed()
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/shipping-methods/{sids[0]}/",
                                       {"prices": [{"currency": "USD", "price": "9.95"}]})
        self.assertEqual((st, resp["prices"][0]["price"]), (200, "9.95"))
        _, second = self.client.request("POST", f"/api/admin/campaigns/{cid}/shipping-methods/",
                                        {"shipping_method": "express", "price": "14.95"})
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/shipping-methods/{second['id']}/",
                                       {"shipping_method": "standard"})
        self.assertEqual(st, 400)
        self.assertIn("shipping_method", resp)

    def test_offer_patch_replaces_condition_and_benefit_wholly(self):
        cid, pids, oids, _ = self.seed()
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/offers/{oids[0]}/",
                                       {"available": False,
                                        "condition": {"type": "any", "all_packages": False,
                                                      "package_ids": pids},
                                        "benefit": {"type": "package_percentage", "value": "60.00",
                                                    "price_rounding": "0.95"}})
        self.assertEqual(st, 200)
        live = self.client.get_ok(f"/api/admin/campaigns/{cid}/offers/{oids[0]}/")
        self.assertFalse(live["available"])
        self.assertIsNone(live["condition"]["value"])  # the count threshold is gone, not merged
        self.assertEqual([p["id"] for p in live["condition"]["packages"]], pids)
        self.assertEqual(live["benefit"], {"type": "package_percentage", "value": "60.00",
                                           "price_rounding": "0.95", "description": ""})

    def test_duplicate_offer_name_and_voucher_code_are_400s(self):
        cid, pids, oids, _ = self.seed()
        scope = {"type": "any", "all_packages": False, "package_ids": [pids[0]]}
        ben = {"type": "package_percentage", "value": "10.00"}
        st, resp = self.client.request("POST", f"/api/admin/campaigns/{cid}/offers/",
                                       {"name": "Dashboard Bracelet - Buy 2", "offer_type": "offer",
                                        "condition": scope, "benefit": ben})
        self.assertEqual(st, 400)
        self.assertIn("name", resp)
        st, _ = self.client.request("POST", f"/api/admin/campaigns/{cid}/offers/",
                                    {"name": "Exit", "offer_type": "voucher", "code": "SAVE10",
                                     "condition": scope, "benefit": ben})
        self.assertEqual(st, 201)
        st, resp = self.client.request("POST", f"/api/admin/campaigns/{cid}/offers/",
                                       {"name": "Exit again", "offer_type": "voucher", "code": "SAVE10",
                                        "condition": scope, "benefit": ben})
        self.assertEqual(st, 400)
        self.assertIn("code", resp)
        # the same rule on a rename: a swap of two live names cannot be done in one PATCH each
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/offers/{oids[1]}/",
                                       {"name": "Dashboard Bracelet - Buy 2"})
        self.assertEqual(st, 400)
        st, resp = self.client.request("PATCH", f"/api/admin/campaigns/{cid}/offers/{oids[1]}/",
                                       {"name": "Dashboard Bracelet - Free Shipping"})
        self.assertEqual(st, 200)  # its own name is not a conflict

    def test_package_delete_is_refused_while_an_offer_scopes_it(self):
        cid, pids, oids, _ = self.seed()
        st, resp = self.client.request("DELETE", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/")
        self.assertEqual(st, 400)
        self.assertIn(str(oids[0]), str(resp))
        self.assertIn(pids[0], self.state["packages"][cid])
        st, _ = self.client.request("DELETE", f"/api/admin/campaigns/{cid}/packages/{pids[1]}/")
        self.assertEqual(st, 204)  # nothing scopes the second package
        self.state["cascade_on_package_delete"] = True
        st, _ = self.client.request("DELETE", f"/api/admin/campaigns/{cid}/packages/{pids[0]}/")
        self.assertEqual((st, pids[0] in self.state["packages"][cid]), (204, False))


class ApplyJournal(unittest.TestCase):
    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns(name="Bracelet - test", exit_code="BRACELET10"))
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.tmp.name) / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.manifest = Path(self.tmp.name) / "run-manifest.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_gate_exit_2_sends_nothing(self):
        calls = []
        orig = ca._client_for
        ca._client_for = lambda slug: calls.append(slug)
        try:
            rc = ca.main(["apply", "--plan", str(self.plan_path)])
            self.assertEqual(rc, 2)
            rc = ca.main(["apply", "--plan", str(self.plan_path), "--yes", "--plan-sha256", "deadbeef"])
            self.assertEqual(rc, 2)
        finally:
            ca._client_for = orig
        self.assertEqual(calls, [])

    def test_full_apply_and_order(self):
        state = fresh_state()
        t = DynamicTransport(self.disc, state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        self.assertEqual(man.data["campaign"]["status"], "created")
        self.assertEqual([e["status"] for e in man.data["packages"]], ["created"] * 4)
        self.assertEqual([e["status"] for e in man.data["offers"]], ["created"] * 4)
        posts = [c[1].split("?")[0] for c in t.calls if c[0] == "POST"]
        self.assertEqual(posts[0], "/api/admin/campaigns/")
        self.assertEqual([p.rsplit("/", 2)[-2] for p in posts[1:]], ["packages"] * 4 + ["shipping-methods"] + ["offers"] * 4)
        offer_bodies = [c[2] for c in t.calls if c[0] == "POST" and c[1].endswith("/offers/")]
        pkg_ids = [e["id"] for e in man.data["packages"]]
        self.assertEqual(sorted(offer_bodies[0]["condition"]["package_ids"]), sorted(pkg_ids))
        self.assertFalse(offer_bodies[0]["condition"]["all_packages"])
        self.assertEqual(oct(self.manifest.stat().st_mode & 0o777), "0o600")

    def test_refuses_same_named_existing_campaign(self):
        state = fresh_state()
        state["campaigns"][5] = {"id": 5, "name": "Bracelet - test", "created_at": "2026-01-01T00:00:00Z"}
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(DynamicTransport(self.disc, state)), self.plan, self.sha, self.manifest, None)
        self.assertFalse(self.manifest.exists())

    def test_third_package_fails_then_resume(self):
        state = fresh_state()
        cid = 101
        t = DynamicTransport(self.disc, state, fail_on={("POST", f"/api/admin/campaigns/{cid}/packages/"): 500})
        # first two package POSTs succeed because fail_on pops after first hit? No: inject on 3rd call.
        hits = {"n": 0}
        real = t.child_create

        def flaky(kind):
            h = real(kind)

            def w(path, body):
                if kind == "packages":
                    hits["n"] += 1
                    if hits["n"] == 3:
                        return 500, {"detail": "boom"}
                return h(path, body)
            return w
        t.child_create = flaky
        t.fail_on = {}
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        self.assertEqual(man["campaign"]["status"], "created")
        self.assertEqual([e["status"] for e in man["packages"]], ["created", "created", "pending"])
        # resume: the pending one is absent remotely -> recreated; nothing duplicated
        man2 = ca.apply(make_client(t), self.plan, self.sha, self.manifest, self.manifest)
        self.assertEqual([e["status"] for e in man2.data["packages"]], ["created"] * 4)
        self.assertEqual(len(state["packages"][101]), 4)
        self.assertEqual(len(state["campaigns"]), 1)

    def test_resume_reconciles_lost_campaign_response(self):
        state = fresh_state()
        t = DynamicTransport(self.disc, state)
        man = ca.Manifest.new(self.manifest, self.plan, self.sha)
        man.data["started_at"] = "2026-09-02T10:00:00Z"
        man.save()
        # the POST "happened" remotely but the response was lost
        state["campaigns"][101] = {"id": 101, "name": "Bracelet - test", "api_key": "RECOVERED-KEY-0001",
                                   "created_at": "2026-09-02T10:00:05Z", "currency": "USD", "language": "en"}
        state["seq"] = 101
        man2 = ca.apply(make_client(t), self.plan, self.sha, self.manifest, self.manifest)
        self.assertEqual(man2.data["campaign"]["id"], 101)
        self.assertEqual(man2.data["campaign"]["api_key"], "RECOVERED-KEY-0001")
        self.assertTrue(man2.data["campaign"].get("reconciled"))
        self.assertEqual(len(state["campaigns"]), 1)

    def test_resume_stops_on_ambiguous_campaign(self):
        state = fresh_state()
        man = ca.Manifest.new(self.manifest, self.plan, self.sha)
        man.data["started_at"] = "2026-09-02T10:00:00Z"
        man.save()
        for i in (101, 102):
            state["campaigns"][i] = {"id": i, "name": "Bracelet - test", "created_at": "2026-09-02T10:00:05Z"}
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(DynamicTransport(self.disc, state)), self.plan, self.sha, self.manifest, self.manifest)

    def test_resume_refuses_mismatches(self):
        state = fresh_state()
        t = DynamicTransport(self.disc, state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, "0" * 64, self.manifest, self.manifest)
        other = dict(self.plan, store_slug="other", store_origin="https://other.29next.store")
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), other, self.sha, self.manifest, self.manifest)
        state["campaigns"][man.data["campaign"]["id"]]["created_at"] = "2000-01-01T00:00:00Z"
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, self.manifest)

    def test_offers_unsupported_stops_with_journal(self):
        state = fresh_state()
        t = DynamicTransport(self.disc, state)
        real = t.child_create
        t.child_create = lambda kind: (lambda p, b: (404, {"detail": "nope"})) if kind == "offers" else real(kind)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        self.assertIn("dashboard", str(cm.exception))
        man = json.loads(self.manifest.read_text())
        self.assertEqual([e["status"] for e in man["packages"]], ["created"] * 4)
        self.assertEqual(man["offers"][0]["status"], "unsupported")

    def test_blockers_refuse_apply(self):
        plan = dict(self.plan, blockers=["metadata missing"])
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(DynamicTransport(self.disc, fresh_state())), plan, self.sha, self.manifest, None)


class PackageImages(unittest.TestCase):
    """Package image overrides: PUT /campaigns/{id}/packages/{id}/image/ (issue #568)."""

    SRC = "https://cdn.example.com/hero.png"

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns(name="Bracelet - test", exit_code="BRACELET10"))
        # One package carries an override, the rest do not: that asymmetry is what
        # proves the PUT is emitted per package rather than per run.
        self.plan["packages"][0]["image"] = {"src": self.SRC, "file_name": "hero"}
        self.key = self.plan["packages"][0]["key"]
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.tmp.name) / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.manifest = Path(self.tmp.name) / "run-manifest.json"
        self.state = fresh_state()

    def tearDown(self):
        self.tmp.cleanup()

    def _entry(self, man):
        return next(e for e in man.data["packages"] if e["key"] == self.key)

    def _cart(self):
        from decimal import Decimal

        def calc(path, body):
            qty = sum(l["quantity"] for l in body["lines"])
            unit = ca.landed_unit(Decimal("49.95"), Decimal({1: 50, 2: 55, 3: 60}[qty]), None)
            if body.get("vouchers"):
                unit = ca.landed_unit(unit, Decimal(10), None)
            total = unit * qty + Decimal("6.95")
            return 200, {"total": str(total), "subtotal": str(unit * qty), "lines": []}
        return ca.Client(ca.CART_API_ORIGIN, "campaignkey", auth_scheme="raw", send_version_header=False,
                         transport=FakeTransport({("POST", "/api/v1/carts/calculate/"): calc}),
                         clock=FakeClock(), sleep=lambda s: None)

    def test_put_follows_its_own_package_create(self):
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        puts = [c for c in t.calls if c[0] == "PUT"]
        self.assertEqual(len(puts), 1)
        self.assertEqual(puts[0][2], {"src": self.SRC, "file_name": "hero"})
        e = self._entry(man)
        self.assertEqual(puts[0][1], f"/api/admin/campaigns/{man.data['campaign']['id']}/packages/{e['id']}/image/")
        # the PUT is the next mutating call after that package's POST
        mutating = [(c[0], c[1]) for c in t.calls if c[0] in ("POST", "PUT")]
        i = mutating.index(("PUT", puts[0][1]))
        self.assertEqual(mutating[i - 1][0], "POST")
        self.assertTrue(mutating[i - 1][1].endswith("/packages/"))
        self.assertEqual(e["image_status"], "set")
        self.assertEqual(e["image"], OVERRIDE_IMAGE)
        self.assertEqual(e["image_at_create"], CATALOGUE_IMAGE)
        self.assertIsNone(e["image_intent"])

    def test_plan_without_images_sends_no_put(self):
        plan = ca.recommend(self.disc, ns(name="No images", exit_code="BRACELET10"))
        path = Path(self.tmp.name) / "p2.json"
        ca.atomic_write_json(path, plan)
        t = DynamicTransport(self.disc, self.state)
        ca.apply(make_client(t), plan, ca.sha256_file(path), Path(self.tmp.name) / "m2.json", None)
        self.assertFalse([c for c in t.calls if c[0] == "PUT"])

    def test_rendered_requests_match_what_apply_sends(self):
        """The approval gate is the printed request list, so it must not drift."""
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        ids = {e["key"]: e["id"] for e in man.data["packages"]}
        cid = man.data["campaign"]["id"]
        rendered = []
        for method, path, body in ca.render_requests(self.plan):
            path = path.replace("{campaign_id}", str(cid))
            blob = json.dumps(body)
            for k, v in ids.items():
                path = path.replace(f"<{k}>", str(v))
                blob = blob.replace(f'"<{k}>"', str(v))
            rendered.append((method, path, json.loads(blob)))
        sent = [(c[0], c[1], c[2]) for c in t.calls
                if c[0] in ("POST", "PUT") and c[1].startswith("/api/admin/campaigns")]
        self.assertEqual([(m, p) for m, p, _ in sent], [(m, p) for m, p, _ in rendered])
        self.assertEqual([b for _, _, b in sent], [b for _, _, b in rendered])

    def test_404_marks_unsupported_and_run_still_completes(self):
        class NoImages(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    return 404, {}, json.dumps({"detail": "not found"})
                return super().__call__(req, timeout)

        # every package gets an override, so the store-wide short-circuit is observable
        for p in self.plan["packages"]:
            p["image"] = {"src": self.SRC}
        ca.atomic_write_json(self.plan_path, self.plan)
        sha = ca.sha256_file(self.plan_path)
        t = NoImages(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), self.plan, sha, self.manifest, None)
        self.assertIn("package image endpoint", str(cm.exception))
        man = json.loads(self.manifest.read_text())
        self.assertTrue(man.get("completed_at"))
        self.assertTrue(all(e["image_status"] == "unsupported" for e in man["packages"]))
        self.assertEqual(len([c for c in t.calls if c[0] == "PUT"]), 1)
        self.assertTrue(all(e["status"] == "created" for e in man["offers"]))

    def test_400_marks_failed_and_is_terminal_on_resume(self):
        t = DynamicTransport(self.disc, self.state)
        pid_holder = {}

        class Rejects(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    pid_holder["hit"] = True
                    return 400, {}, json.dumps({"detail": "Unable to retrieve the image from this URL"})
                return super().__call__(req, timeout)

        t = Rejects(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        self.assertIn("did not land", str(cm.exception))
        self.assertIn("terminal", str(cm.exception))
        man = json.loads(self.manifest.read_text())
        e = next(x for x in man["packages"] if x["key"] == self.key)
        self.assertEqual(e["image_status"], "failed")
        self.assertEqual(e["status"], "created")
        self.assertIn("Unable to retrieve", e["image_error"])
        self.assertTrue(man["shipping_methods"] and man["offers"])

        # a resume must not retry a rejected src: the plan hash pins it, so it would fail identically
        t2 = DynamicTransport(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t2), self.plan, self.sha, self.manifest, self.manifest)
        self.assertFalse([c for c in t2.calls if c[0] == "PUT"])
        self.assertFalse([c for c in t2.calls if c[0] == "POST" and c[1].endswith("/packages/")])

    def test_200_without_image_fails(self):
        class Empty(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    return 200, {}, json.dumps({"id": 1, "image": None})
                return super().__call__(req, timeout)

        t = Empty(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        self.assertEqual(next(x for x in man["packages"] if x["key"] == self.key)["image_status"], "failed")

    def test_transient_answer_stays_pending_and_resume_completes_it(self):
        class Flaky(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    return 503, {}, json.dumps({"detail": "unavailable"})
                return super().__call__(req, timeout)

        t = Flaky(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        e = next(x for x in man["packages"] if x["key"] == self.key)
        self.assertEqual(e["image_status"], "pending")

        t2 = DynamicTransport(self.disc, self.state)
        man2 = ca.apply(make_client(t2), self.plan, self.sha, self.manifest, self.manifest)
        self.assertEqual(len([c for c in t2.calls if c[0] == "PUT"]), 1)
        e2 = self._entry(man2)
        self.assertEqual(e2["image_status"], "set")
        self.assertIsNone(e2["image_error"])

    def test_resume_lands_the_image_on_an_already_created_package(self):
        """The create-guard must not short-circuit the image phase."""
        t = DynamicTransport(self.disc, self.state)
        ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        for e in man["packages"]:
            e.pop("image_status", None)
            e.pop("image", None)
        self.manifest.write_text(json.dumps(man))

        t2 = DynamicTransport(self.disc, self.state)
        man2 = ca.apply(make_client(t2), self.plan, self.sha, self.manifest, self.manifest)
        self.assertFalse([c for c in t2.calls if c[0] == "POST" and c[1].endswith("/packages/")])
        self.assertEqual(len([c for c in t2.calls if c[0] == "PUT"]), 1)
        self.assertEqual(self._entry(man2)["image_status"], "set")

    def test_verify_reports_images(self):
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        rows = {c["check"]: c for c in ca.verify(make_client(t), self._cart(), man, self.plan, self.sha)["admin_checks"]}
        self.assertEqual(rows[f"package {self.key} image"]["result"], "PASS")
        self.assertEqual(rows[f"package {self.key} image override"]["result"], "PASS")
        other = next(p["key"] for p in self.plan["packages"] if p["key"] != self.key)
        self.assertEqual(rows[f"package {other} image"]["result"], "PASS")
        self.assertNotIn(f"package {other} image override", rows)

    def test_verify_fails_a_package_with_no_image(self):
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        cid = man.data["campaign"]["id"]
        victim = next(e for e in man.data["packages"] if e["key"] != self.key)
        self.state["packages"][cid][victim["id"]]["image"] = None
        report = ca.verify(make_client(t), self._cart(), man, self.plan, self.sha)
        rows = {c["check"]: c for c in report["admin_checks"]}
        self.assertEqual(rows[f"package {victim['key']} image"]["result"], "FAIL")
        self.assertEqual(report["result"], "FAIL")

    def test_verify_override_row_follows_the_journal(self):
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        # a dashboard repair leaves a catalogue image present but no receipt from this run
        self._entry(man)["image_status"] = "failed"
        report = ca.verify(make_client(t), self._cart(), man, self.plan, self.sha)
        rows = {c["check"]: c for c in report["admin_checks"]}
        self.assertEqual(rows[f"package {self.key} image"]["result"], "PASS")
        self.assertEqual(rows[f"package {self.key} image override"]["result"], "FAIL")

    def test_verify_reports_a_missing_journal_entry_instead_of_crashing(self):
        t = DynamicTransport(self.disc, self.state)
        man = ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        # the offers reference this package, which is what used to reach the KeyError
        dropped = man.data["packages"][0]["key"]
        man.data["packages"] = [e for e in man.data["packages"] if e["key"] != dropped]
        report = ca.verify(make_client(t), self._cart(), man, self.plan, self.sha)
        rows = {c["check"]: c for c in report["admin_checks"]}
        self.assertEqual(rows[f"package {dropped} journalled"]["result"], "FAIL")
        self.assertEqual(report["result"], "FAIL")
        scope = [v for k, v in rows.items() if k.endswith(" scope")]
        self.assertTrue(any(r["result"] == "FAIL" for r in scope))

    def test_a_404_after_a_success_does_not_suppress_later_images(self):
        """404 on this route is 'no route' OR 'no such package'. Once one PUT has landed
        the route plainly exists, so a later 404 must not mark the rest unsupported."""
        class SecondPackageMissing(DynamicTransport):
            puts = 0

            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.puts += 1
                    if self.puts == 2:      # first one succeeds, so the route demonstrably exists
                        self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                        return 404, {}, json.dumps({"detail": "Not found."})
                return super().__call__(req, timeout)

        for p in self.plan["packages"]:
            p["image"] = {"src": self.SRC}
        ca.atomic_write_json(self.plan_path, self.plan)
        sha = ca.sha256_file(self.plan_path)
        t = SecondPackageMissing(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        statuses = [e["image_status"] for e in man["packages"]]
        # `failed`, not `unsupported`: another package's image landed, so the endpoint
        # demonstrably exists and this is one missing package id, not a store without
        # the feature. Both are terminal, but only one of them is true.
        self.assertEqual(statuses.count("failed"), 1, statuses)
        self.assertNotIn("unsupported", statuses)
        self.assertEqual(statuses.count("set"), len(statuses) - 1, statuses)
        self.assertEqual(len([c for c in t.calls if c[0] == "PUT"]), len(self.plan["packages"]))
        failed = next(e for e in man["packages"] if e["image_status"] == "failed")
        self.assertIn("package id is likely gone", failed["image_error"])

    def test_a_404_on_the_first_image_is_treated_as_store_wide(self):
        """Nothing has proved the route exists yet, so stop after one attempt."""
        class NoRoute(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    return 404, {}, json.dumps({"detail": "Not found."})
                return super().__call__(req, timeout)

        for p in self.plan["packages"]:
            p["image"] = {"src": self.SRC}
        ca.atomic_write_json(self.plan_path, self.plan)
        sha = ca.sha256_file(self.plan_path)
        t = NoRoute(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        self.assertTrue(all(e["image_status"] == "unsupported" for e in man["packages"]))
        self.assertEqual(len([c for c in t.calls if c[0] == "PUT"]), 1)

    def test_any_5xx_stays_pending(self):
        class Gone(DynamicTransport):
            def __call__(self, req, timeout):
                if req.get_method() == "PUT" and req.full_url.endswith("/image/"):
                    self.calls.append(("PUT", req.full_url.split(".29next.store", 1)[1], None, {}))
                    return 507, {}, json.dumps({"detail": "insufficient storage"})
                return super().__call__(req, timeout)

        t = Gone(self.disc, self.state)
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), self.plan, self.sha, self.manifest, None)
        man = json.loads(self.manifest.read_text())
        self.assertEqual(next(x for x in man["packages"] if x["key"] == self.key)["image_status"], "pending")

class TeardownAndVerify(unittest.TestCase):
    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns(name="Bracelet - test", exit_code="BRACELET10"))
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.tmp.name) / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.manifest = Path(self.tmp.name) / "run-manifest.json"
        self.state = fresh_state()
        self.t = DynamicTransport(self.disc, self.state)
        self.man = ca.apply(make_client(self.t), self.plan, self.sha, self.manifest, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_teardown_order_and_journal(self):
        ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        deletes = [c[1] for c in self.t.calls if c[0] == "DELETE"]
        kinds = [d.strip("/").split("/")[4] if len(d.strip("/").split("/")) > 4 else "campaign" for d in deletes]
        self.assertEqual(kinds, ["offers"] * 4 + ["shipping-methods"] + ["packages"] * 4 + ["campaign"])
        self.assertEqual(self.state["campaigns"], {})
        self.assertEqual(self.man.data["campaign"]["status"], "deleted")

    def test_teardown_refuses_without_confirmation_and_on_mismatch(self):
        with self.assertRaises(ca.CampaignAdminError):
            ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: False)
        self.assertFalse([c for c in self.t.calls if c[0] == "DELETE"])
        pid = self.man.data["packages"][0]["id"]
        self.state["packages"][self.man.data["campaign"]["id"]][pid]["name"] = "Someone else's package"
        with self.assertRaises(ca.CampaignAdminError):
            ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        self.assertFalse([c for c in self.t.calls if c[0] == "DELETE"])
        with self.assertRaises(ca.CampaignAdminError):
            ca.teardown(make_client(self.t), self.man, self.plan, "0" * 64, lambda: True)

    def test_teardown_rerun_treats_404_as_deleted(self):
        oid = self.man.data["offers"][0]["id"]
        self.man.mark("offers", self.man.data["offers"][0]["key"], status="deleting")
        del self.state["offers"][self.man.data["campaign"]["id"]][oid]
        ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        deletes = [c[1] for c in self.t.calls if c[0] == "DELETE"]
        self.assertFalse(any(d.endswith(f"/offers/{oid}/") for d in deletes))
        self.assertEqual(self.man.data["offers"][0]["status"], "deleted")

    def _cart(self, drift="0.00"):
        from decimal import Decimal

        def calc(path, body):
            total = Decimal("0")
            qty = sum(l["quantity"] for l in body["lines"])
            pct = {1: 50, 2: 55, 3: 60}[qty]
            unit = ca.landed_unit(Decimal("49.95"), Decimal(pct), None)
            if body.get("vouchers"):
                unit = ca.landed_unit(unit, Decimal(10), None)
            total = unit * qty + Decimal("6.95") + Decimal(drift)
            return 200, {"total": str(total), "subtotal": str(unit * qty), "lines": []}
        t = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
        t_client = ca.Client(ca.CART_API_ORIGIN, "campaignkey", auth_scheme="raw", send_version_header=False,
                             transport=t, clock=FakeClock(), sleep=lambda s: None)
        return t, t_client

    def test_verify_passes_and_uses_raw_auth(self):
        t, cart = self._cart()
        report = ca.verify(make_client(self.t), cart, self.man, self.plan, self.sha)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        names = [c["case"] for c in report["calculate_cases"]]
        self.assertIn("Buy 3 mixed variants", names)
        self.assertIn("Buy 2 + exit voucher", names)
        self.assertEqual(t.calls[0][3].get("Authorization"), "campaignkey")
        self.assertNotIn("X-29next-api-version", t.calls[0][3])

    def test_verify_fails_on_two_cent_drift_and_hash_mismatch(self):
        _, cart = self._cart(drift="0.02")
        report = ca.verify(make_client(self.t), cart, self.man, self.plan, self.sha)
        self.assertEqual(report["result"], "FAIL")
        with self.assertRaises(ca.CampaignAdminError):
            ca.verify(make_client(self.t), cart, self.man, self.plan, "0" * 64)


class RegressionsFromReview(unittest.TestCase):
    """Regressions from an adversarial review round."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_decimal_percentages_rejected(self):
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(tiers="50.5,55,60"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(exit="10.5"))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50.5"]))

    def test_metadata_audit_failure_is_a_blocker(self):
        d = json.loads(json.dumps(self.disc))
        d["metadata_checked"] = False
        d["metadata_missing"] = []
        plan = ca.recommend(d, ns())
        self.assertTrue(any("could not be audited" in b for b in plan["blockers"]))

    def test_at_or_after_is_instant_aware(self):
        self.assertTrue(ca._at_or_after("2026-09-02T10:00:05Z", "2026-09-02T12:00:00+02:00"))  # equal instant
        self.assertTrue(ca._at_or_after("2026-09-02T10:00:06Z", "2026-09-02T10:00:05Z"))
        self.assertFalse(ca._at_or_after("2026-09-02T09:59:59Z", "2026-09-02T10:00:00Z"))
        self.assertFalse(ca._at_or_after(None, "2026-09-02T10:00:00Z"))

    def test_resume_writes_manifest_to_gitignored_outdir_not_resume_path(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            out_manifest = Path(d) / "out" / "run-manifest.json"
            stray = Path(d) / "tracked" / "run-manifest.json"  # simulates a resume path outside out/
            state = fresh_state()
            t = DynamicTransport(self.disc, state)
            man = ca.apply(make_client(t), plan, sha, out_manifest, None)
            # move the manifest to a "tracked" location and resume from there
            stray.parent.mkdir(parents=True, exist_ok=True)
            import shutil; shutil.copy(man.path, stray)
            out_manifest.unlink()
            man2 = ca.apply(make_client(t), plan, sha, out_manifest, stray)
            self.assertEqual(man2.path, out_manifest)         # writes went to the out-dir path
            self.assertTrue(out_manifest.exists())

    def _cart_free(self):
        from decimal import Decimal

        def calc(path, body):
            qty = sum(l["quantity"] for l in body["lines"])
            pct = {1: 50, 2: 55, 3: 60}[qty]
            unit = ca.landed_unit(Decimal("49.95"), Decimal(pct), None)
            if body.get("vouchers"):
                unit = ca.landed_unit(unit, Decimal(10), None)
            return 200, {"total": str(unit * qty), "subtotal": str(unit * qty), "lines": []}  # shipping free
        tt = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
        return ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=tt, clock=FakeClock(), sleep=lambda s: None)

    def test_free_shipping_verify_expects_zero_shipping(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10", free_shipping=True))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            state = fresh_state(); t = DynamicTransport(self.disc, state)
            man = ca.apply(make_client(t), plan, sha, Path(d) / "run-manifest.json", None)
            report = ca.verify(make_client(t), self._cart_free(), man, plan, sha)
            self.assertEqual(report["result"], "PASS", json.dumps([x for x in report["calculate_cases"] if x["result"]=="FAIL"], indent=1))


class RegressionsRoundThree(unittest.TestCase):
    """Regressions from a later review round."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    # CRITICAL: --resume after an unsupported offers run must not loop forever
    def test_resume_after_unsupported_offers_is_terminal(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            mani = Path(d) / "run-manifest.json"
            state = fresh_state(); t = DynamicTransport(self.disc, state)
            real = t.child_create
            t.child_create = lambda kind: (lambda p, b: (404, {"detail": "nope"})) if kind == "offers" else real(kind)
            with self.assertRaises(ca.CampaignAdminError):
                ca.apply(make_client(t), plan, sha, mani, None)
            man = json.loads(mani.read_text())
            self.assertTrue(all(e["status"] == "unsupported" for e in man["offers"]))
            offer_posts_1 = sum(1 for c in t.calls if c[0] == "POST" and c[1].endswith("/offers/"))
            # resume must NOT re-POST the unsupported offers
            t.calls.clear()
            with self.assertRaises(ca.CampaignAdminError):
                ca.apply(make_client(t), plan, sha, mani, mani)
            offer_posts_2 = sum(1 for c in t.calls if c[0] == "POST" and c[1].endswith("/offers/"))
            self.assertEqual(offer_posts_2, 0, "resume re-POSTed unsupported offers (infinite loop)")

    # CRITICAL: offer_body raises CampaignAdminError, not KeyError, for an unresolved key
    def test_offer_body_unresolved_package_key(self):
        o = {"key": "x", "name": "X", "offer_type": "offer",
             "condition": {"type": "any", "package_keys": ["missing"]},
             "benefit": {"type": "package_percentage", "value": "10.00", "price_rounding": None}}
        with self.assertRaises(ca.CampaignAdminError):
            ca.offer_body(o, {"hero-1": 5})

    # CRITICAL: a hostile Retry-After never reaches sleep as a negative/NaN
    def test_retry_after_hostile_values_do_not_crash(self):
        for bad in ("-5", "nan", "inf", "not-a-number"):
            sleeps, n = [], {"i": 0}
            def flaky(path, body, bad=bad, n=n):
                n["i"] += 1
                return (429, ({}, {"Retry-After": bad})) if n["i"] == 1 else (200, {"ok": True})
            t = FakeTransport({("GET", "/api/admin/store/"): flaky})
            c = ca.Client(ORIGIN, "tok", transport=t, clock=FakeClock(), sleep=sleeps.append)
            status, body = c.request("GET", "/api/admin/store/")
            self.assertEqual(status, 200)
            self.assertTrue(all(x >= 0 for x in sleeps), f"negative sleep for {bad}: {sleeps}")

    # WARNING: GET retries on a network error (status None)
    def test_get_retries_on_network_error(self):
        n = {"i": 0}
        def flaky(path, body):
            n["i"] += 1
            if n["i"] == 1:
                raise urllib.error.URLError("dns blip")
            return 200, {"ok": True}
        t = FakeTransport({("GET", "/api/admin/store/"): flaky})
        c = ca.Client(ORIGIN, "tok", transport=t, clock=FakeClock(), sleep=lambda s: None)
        self.assertEqual(c.request("GET", "/api/admin/store/"), (200, {"ok": True}))

    # WARNING: naive vs aware timestamps do not raise
    def test_timestamp_helpers_handle_naive(self):
        self.assertTrue(ca.same_instant("2026-09-03T02:00:00", "2026-09-03T02:00:00+00:00"))
        self.assertTrue(ca._at_or_after("2026-09-03T02:00:01", "2026-09-03T02:00:00+00:00"))
        self.assertFalse(ca._at_or_after("2026-09-03T01:59:59", "2026-09-03T02:00:00Z"))

    # WARNING: anchor 0 and non-decimal rejected with the operator-facing message
    def test_anchor_price_zero_and_nan_rejected(self):
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(anchor_price="0"))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(anchor_price="abc"))
        self.assertIn("anchor-price", str(cm.exception))

    # WARNING: bump/upsell on an unavailable variant rejected at plan time
    def test_unavailable_bump_variant_rejected(self):
        d = json.loads(json.dumps(self.disc))
        for p in d["products"]:
            for v in p["variants"]:
                if v["id"] == 7:
                    v["purchase_availability"] = "unavailable"
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(d, ns(hero=10, ctc="high", anchor_price="189.95", bump=["7:9.95"]))

    # WARNING: --exit 0.00 disables the voucher (no error, no exit offer)
    def test_exit_zero_variants_disable(self):
        for val in ("0", "0.0", "0.00", "00"):
            plan = ca.recommend(self.disc, ns(exit=val))
            self.assertFalse(any(o["key"] == "exit-pop" for o in plan["offers"]), val)

    # SUGGESTION: booleans rejected for integer fields
    def test_boolean_ints_rejected(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        plan["campaign"]["payment_gateway_group_id"] = True
        self.assertTrue(any("payment_gateway_group_id" in e for e in ca.validate_plan(plan)))

    # WARNING: metadata audit failure signals unchecked -> blocker
    def test_metadata_audit_unavailable_blocks(self):
        d = json.loads(json.dumps(self.disc)); d["metadata_checked"] = False; d["metadata_missing"] = []
        plan = ca.recommend(d, ns())
        self.assertTrue(any("could not be audited" in b for b in plan["blockers"]))

    # SUGGESTION: pricing report records the per-case delta
    def test_verify_report_records_delta(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            state = fresh_state(); t = DynamicTransport(self.disc, state)
            man = ca.apply(make_client(t), plan, sha, Path(d) / "run-manifest.json", None)
            from decimal import Decimal
            def calc(path, body):
                qty = sum(l["quantity"] for l in body["lines"])
                pct = {1: 50, 2: 55, 3: 60}[qty]
                unit = ca.landed_unit(Decimal("49.95"), Decimal(pct), None)
                if body.get("vouchers"):
                    unit = ca.landed_unit(unit, Decimal(10), None)
                return 200, {"total": str(unit * qty + Decimal("6.95")), "lines": []}
            tt = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
            cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                             transport=tt, clock=FakeClock(), sleep=lambda s: None)
            report = ca.verify(make_client(t), cart, man, plan, sha)
            self.assertTrue(all("delta" in c for c in report["calculate_cases"]))


class StructuredLandedPrices(unittest.TestCase):
    """landed_prices rows carry keys, and verify builds cart cases by lookup."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_rows_carry_keys_and_validate(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        kinds = [l["kind"] for l in plan["landed_prices"]]
        self.assertEqual(kinds, ["single", "upsell"])
        up = plan["landed_prices"][1]
        self.assertEqual(up["offer_key"], "upsell-15-50")
        self.assertEqual(up["package_keys"], ["upsell-16"])
        self.assertEqual(ca.validate_plan(plan), [])
        bad = json.loads(json.dumps(plan))
        bad["landed_prices"][0]["package_keys"] = ["nope"]
        self.assertTrue(any("landed_prices[0]" in e for e in ca.validate_plan(bad)))
        bad2 = json.loads(json.dumps(plan))
        bad2["landed_prices"][1]["offer_key"] = "missing"
        self.assertTrue(any("offer_key" in e for e in ca.validate_plan(bad2)))
        bad3 = json.loads(json.dumps(plan))
        bad3["landed_prices"][0]["kind"] = "Buyback"
        self.assertTrue(any("kind" in e for e in ca.validate_plan(bad3)))

    def test_cart_cases_built_by_lookup(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        ids = {p["key"]: 100 + i for i, p in enumerate(plan["packages"])}
        names = [c[0] for c in ca._cart_cases_from_plan(plan, ids)]
        self.assertIn("Buy 3 mixed variants", names)
        self.assertIn("Buy 2 + exit voucher", names)
        # a row whose packages were not created is skipped, not crashed on
        self.assertEqual(ca._cart_cases_from_plan(plan, {}), [])
        # upsell row -> voucher case via offer_key lookup
        plan2 = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        ids2 = {p["key"]: 200 + i for i, p in enumerate(plan2["packages"])}
        cases = ca._cart_cases_from_plan(plan2, ids2)
        up = [c for c in cases if c[0].endswith("voucher") and "Upsell" in c[0]]
        self.assertEqual(len(up), 1)
        self.assertEqual(up[0][2], ["PHOTOMAGNET50"])

    def test_reconcile_skips_pagination_when_nothing_pending(self):
        plan = ca.recommend(self.disc, ns(name="B", exit_code="B10"))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            state = fresh_state(); t = DynamicTransport(self.disc, state)
            man = ca.apply(make_client(t), plan, sha, Path(d) / "run-manifest.json", None)
            t.calls.clear()
            ca.reconcile(make_client(t), man, plan)
            child_lists = [c for c in t.calls if c[0] == "GET" and c[1].rstrip("/").endswith(("packages", "shipping-methods", "offers"))]
            self.assertEqual(child_lists, [])



class RunDirectories(unittest.TestCase):
    """One directory per campaign run: files follow their inputs, and nothing
    overwrites or writes through another run's manifest."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        if ca._repo_marker(self.root):
            self.skipTest("the temp directory sits inside a git repository")
        self.run_a = self.root / "runs" / "teststore"
        self.disc_path = self.run_a / "discovery.json"
        ca.atomic_write_json(self.disc_path, self.disc)

    def recommend_argv(self, *extra):
        return ["recommend", "--discovery", str(self.disc_path), "--hero", "22", "--ctc", "low",
                "--anchor-price", "49.95", "--shipping", "standard:6.95", *extra]

    def test_run_dir_follows_input_file(self):
        self.assertEqual(ca.main(self.recommend_argv("--name", "Dispatch test", "--exit-code", "DT10")), 0)
        plan_path = self.run_a / "campaign-plan.json"
        self.assertTrue(plan_path.exists())
        self.assertEqual(ca.run_dir_for(plan_path), self.run_a)
        t = DynamicTransport(self.disc, fresh_state())
        with mock.patch.object(ca, "_client_for", lambda slug: make_client(t)):
            rc = ca.main(["apply", "--plan", str(plan_path), "--yes", "--plan-sha256", ca.sha256_file(plan_path)])
        self.assertEqual(rc, 0)
        self.assertTrue((self.run_a / ca.MANIFEST_NAME).exists())

    def test_recommend_refuses_run_dir(self):
        (self.run_a / ca.MANIFEST_NAME).write_text("{}")
        self.assertEqual(ca.main(self.recommend_argv()), 1)
        self.assertFalse((self.run_a / "campaign-plan.json").exists())
        second = self.root / "runs" / "second"
        self.assertEqual(ca.main(self.recommend_argv("--out", str(second))), 0)
        self.assertTrue((second / "campaign-plan.json").exists())

    def test_resume_keeps_other_run_intact(self):
        plan_a = ca.recommend(self.disc, ns(name="Run A", exit_code="RA10"))
        plan_b = ca.recommend(self.disc, ns(name="Run B", exit_code="RB10"))
        dir_a, dir_b = self.root / "a", self.root / "b"
        pa, pb = dir_a / "campaign-plan.json", dir_b / "campaign-plan.json"
        ca.atomic_write_json(pa, plan_a)
        ca.atomic_write_json(pb, plan_b)
        t = DynamicTransport(self.disc, fresh_state())
        ca.apply(make_client(t), plan_a, ca.sha256_file(pa), dir_a / ca.MANIFEST_NAME, None)
        ca.apply(make_client(t), plan_b, ca.sha256_file(pb), dir_b / ca.MANIFEST_NAME, None)
        before = (dir_b / ca.MANIFEST_NAME).read_bytes()
        t.calls.clear()
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), plan_a, ca.sha256_file(pa), dir_b / ca.MANIFEST_NAME, dir_a / ca.MANIFEST_NAME)
        self.assertIn("different run", str(cm.exception))
        self.assertEqual(t.calls, [])
        self.assertEqual((dir_b / ca.MANIFEST_NAME).read_bytes(), before)
        man = ca.apply(make_client(t), plan_a, ca.sha256_file(pa), dir_a / ca.MANIFEST_NAME, dir_a / ca.MANIFEST_NAME)
        self.assertEqual(man.data["campaign"]["status"], "created")

    def _applied_run(self, where: Path):
        plan = ca.recommend(self.disc, ns(name="Teardown guard", exit_code="TG10"))
        pp = where / "campaign-plan.json"
        ca.atomic_write_json(pp, plan)
        t = DynamicTransport(self.disc, fresh_state())
        ca.apply(make_client(t), plan, ca.sha256_file(pp), where / ca.MANIFEST_NAME, None)
        return pp, where / ca.MANIFEST_NAME, t

    def _teardown(self, pp, mp, t):
        with mock.patch.object(ca, "_client_for", lambda slug: make_client(t)):
            return ca.main(["teardown", "--manifest", str(mp), "--plan", str(pp), "--yes"])

    def test_teardown_refuses_unignored_manifest(self):
        repo = self.root / "repo"
        repo.mkdir()
        git_init(repo)
        pp, mp, t = self._applied_run(repo / "run")
        before = mp.read_bytes()
        t.calls.clear()
        self.assertEqual(self._teardown(pp, mp, t), 1)
        self.assertEqual(t.calls, [])
        self.assertEqual(mp.read_bytes(), before)
        (repo / ".gitignore").write_text("run/\n")
        self.assertEqual(self._teardown(pp, mp, t), 0)

    def test_teardown_refuses_symlinked_manifest(self):
        pp, mp, t = self._applied_run(self.root / "real")
        link = self.root / "real" / "link.json"
        link.symlink_to(mp)
        t.calls.clear()
        self.assertEqual(self._teardown(pp, link, t), 1)
        self.assertEqual(t.calls, [])

    def test_teardown_custom_name_is_checked(self):
        repo = self.root / "repo"
        repo.mkdir()
        git_init(repo)
        (repo / ".gitignore").write_text("run/run-manifest.json\n")
        pp, mp, t = self._applied_run(repo / "run")
        custom = repo / "run" / "mine.json"
        custom.write_bytes(mp.read_bytes())
        t.calls.clear()
        self.assertEqual(self._teardown(pp, custom, t), 1)
        self.assertEqual(t.calls, [])


META_KEYS = ("metadata_checked", "metadata_missing", "metadata_conflicts", "metadata_error")


def metadata_defs(skip=(), override=None):
    override = override or {}
    return [{"key": f.key, "object": override.get(f.key, f.object), "name": f.name}
            for f in ca.METADATA_FIELDS if f.key not in skip]


class MetadataProvisioning(unittest.TestCase):
    def store(self, defs, post_status=201, list_status=200, list_body=None):
        """A fake store with the discover reads plus a stateful metadata list/create."""
        state = {"defs": list(defs)}

        def list_defs(path, body):
            if list_status != 200:
                return list_status, list_body if list_body is not None else {}
            return 200, {"results": state["defs"], "next": None}

        def create_def(path, body):
            if post_status in (200, 201):
                state["defs"].append({"key": body["key"], "object": body["object"], "name": body["name"]})
            return post_status, dict(body, id=len(state["defs"]))

        return FakeTransport({
            ("GET", "/api/admin/store/"): (200, {"name": "Test", "available_currencies": [{"code": "USD"}],
                                                "available_languages": [{"code": "en"}]}),
            ("GET", "/api/admin/gateway-groups/"): (200, []),
            ("GET", "/api/admin/shipping-methods/"): (200, []),
            ("GET", "/api/admin/products/"): (200, []),
            ("GET", "/api/admin/campaigns/"): (200, []),
            ("GET", ca.METADATA_PATH): list_defs,
            ("POST", ca.METADATA_PATH): create_def,
        })

    def captured(self, fn, *args):
        out = []
        with mock.patch.object(ca, "print", lambda *a, **k: out.append(" ".join(map(str, a)))):
            rc = fn(*args)
        return rc, "\n".join(out)

    def test_audit_dedupes_by_key(self):
        audit = ca.metadata_audit(make_client(self.store(metadata_defs())))
        self.assertEqual((audit["missing"], audit["conflicts"]), ([], []))

    def test_audit_reports_conflicts(self):
        audit = ca.metadata_audit(make_client(self.store(metadata_defs(override={"sdk_version": "order"}))))
        self.assertEqual(audit["missing"], [])
        self.assertEqual(audit["conflicts"], [{"key": "sdk_version", "defined_on": "order", "needs": "attribution"}])

    def test_dry_run_posts_nothing(self):
        t = self.store(metadata_defs(skip=("device", "referrer")))
        self.assertEqual(ca.metadata_provision(make_client(t), "teststore", False), 0)
        self.assertFalse([c for c in t.calls if c[0] == "POST"])

    def test_apply_omits_version_header(self):
        t = self.store(metadata_defs(skip=("device", "referrer")))
        self.assertEqual(ca.metadata_provision(make_client(t), "teststore", True), 0)
        gets = [c for c in t.calls if c[0] == "GET"]
        posts = [c for c in t.calls if c[0] == "POST"]
        self.assertEqual(sorted(c[2]["key"] for c in posts), ["device", "referrer"])
        self.assertTrue(gets and all(c[3].get("X-29next-api-version") == ca.API_VERSION for c in gets))
        self.assertTrue(all("X-29next-api-version" not in c[3] for c in posts))
        referrer = next(c[2] for c in posts if c[2]["key"] == "referrer")
        self.assertEqual(referrer["validations"], [{"type": "maximum_length", "value": 2000}])
        self.assertTrue(referrer["enable_export"])

    def test_apply_reports_failures(self):
        t = self.store(metadata_defs(skip=("device",)), post_status=400)
        self.assertEqual(ca.metadata_provision(make_client(t), "teststore", True), 1)

    def test_apply_conflict_returns_one(self):
        t = self.store(metadata_defs(override={"device": "order"}))
        rc, out = self.captured(ca.metadata_provision, make_client(t), "teststore", True)
        self.assertEqual(rc, 1)
        self.assertFalse([c for c in t.calls if c[0] == "POST"])
        self.assertIn("Settings > Metadata", out)

    def test_apply_success_says_rerun(self):
        t = self.store(metadata_defs(skip=("device",)))
        rc, out = self.captured(ca.metadata_provision, make_client(t), "teststore", True)
        self.assertEqual(rc, 0)
        self.assertIn("discover --store teststore", out)

    def test_metadata_checked_on_ok(self):
        disc = ca.discover(make_client(self.store(metadata_defs())), "teststore")
        self.assertEqual(tuple(disc[k] for k in META_KEYS), (True, [], [], None))

    def test_discover_records_conflicts(self):
        disc = ca.discover(make_client(self.store(metadata_defs(override={"sdk_version": "order"}))), "teststore")
        self.assertTrue(disc["metadata_checked"])
        self.assertEqual(disc["metadata_missing"], [])
        self.assertEqual([c["key"] for c in disc["metadata_conflicts"]], ["sdk_version"])

    def test_recommend_blocks_on_conflict(self):
        d = dict(load_fixture("discovery.json"),
                 metadata_conflicts=[{"key": "sdk_version", "defined_on": "order", "needs": "attribution"}])
        blockers = ca.recommend(d, ns())["blockers"]
        self.assertTrue(any("sdk_version" in b and "Settings > Metadata" in b for b in blockers))

    def test_metadata_error_is_structured(self):
        secret = "SYNTHETIC" + "SECRETVALUE"
        for status in (403, 500):
            t = self.store([], list_status=status, list_body={"detail": f"bad key {secret}"})
            disc = ca.discover(make_client(t), "teststore")
            self.assertFalse(disc["metadata_checked"])
            self.assertEqual(disc["metadata_error"]["status"], status)
            self.assertEqual(disc["metadata_error"]["reason"],
                             ca.METADATA_ERROR_REASONS.get(status, f"HTTP {status}"))
            merged = dict(load_fixture("discovery.json"), **{k: disc[k] for k in META_KEYS})
            blockers = ca.recommend(merged, ns())["blockers"]
            _, printed = self.captured(ca.print_discovery, disc)
            for text in (json.dumps(disc), " ".join(blockers), printed):
                self.assertNotIn(secret, text)
            if status == 403:
                self.assertTrue(any("metadata:read" in b for b in blockers))

    def test_print_discovery_shows_reason(self):
        disc = ca.discover(make_client(self.store([], list_status=403)), "teststore")
        _, printed = self.captured(ca.print_discovery, disc)
        self.assertIn("status 403", printed)
        self.assertIn(ca.METADATA_ERROR_REASONS[403], printed)
        disc = ca.discover(make_client(self.store(metadata_defs(override={"device": "order"}))), "teststore")
        _, printed = self.captured(ca.print_discovery, disc)
        self.assertIn("device is defined on order", printed)
        self.assertIn("Settings > Metadata", printed)

    def test_rediscover_clears_blocker(self):
        t = self.store(metadata_defs(skip=("nc_campaign_id",)))
        client = make_client(t)
        first = ca.discover(client, "teststore")
        self.assertEqual(first["metadata_missing"], ["nc_campaign_id"])
        self.assertEqual(ca.metadata_provision(client, "teststore", True), 0)
        second = ca.discover(client, "teststore")
        self.assertEqual(second["metadata_missing"], [])
        fixture = load_fixture("discovery.json")
        stale = dict(fixture, **{k: first[k] for k in META_KEYS})
        fresh = dict(fixture, **{k: second[k] for k in META_KEYS})
        self.assertTrue(any("nc_campaign_id" in b for b in ca.recommend(stale, ns())["blockers"]))
        self.assertEqual(ca.recommend(fresh, ns())["blockers"], [])



class PullRequestReviewFixes(unittest.TestCase):
    """Regressions for the findings raised on the public pull request."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def applied(self):
        plan = ca.recommend(self.disc, ns(name="Review fixes", exit_code="RF10"))
        pp = self.root / "campaign-plan.json"
        ca.atomic_write_json(pp, plan)
        sha = ca.sha256_file(pp)
        t = DynamicTransport(self.disc, fresh_state())
        man = ca.apply(make_client(t), plan, sha, self.root / ca.MANIFEST_NAME, None)
        return plan, sha, t, man

    def test_resume_refuses_torn_down_run(self):
        plan, sha, t, man = self.applied()
        ca.teardown(make_client(t), man, plan, sha, lambda: True)
        self.assertEqual(man.data["campaign"]["status"], "deleted")
        t.calls.clear()
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), plan, sha, man.path, man.path)
        self.assertIn("torn down", str(cm.exception))
        self.assertEqual(t.calls, [])

    def test_resume_refuses_partial_teardown(self):
        plan, sha, t, man = self.applied()
        for section in ("packages", "shipping_methods", "offers"):
            man.mark(section, man.data[section][0]["key"], status="deleting")
            t.calls.clear()
            with self.assertRaises(ca.CampaignAdminError, msg=section):
                ca.apply(make_client(t), plan, sha, man.path, man.path)
            self.assertEqual(t.calls, [], section)
            man.mark(section, man.data[section][0]["key"], status="created")
        self.assertEqual(ca.torn_down_entries(man), [])

    def test_default_name_collision_blocks(self):
        d = json.loads(json.dumps(self.disc))
        d["campaigns"].append({"id": 7, "name": "Photo Bracelet", "currency": "USD", "language": "en",
                               "created_at": "2026-08-01T00:00:00Z"})
        blockers = ca.recommend(d, ns())["blockers"]
        self.assertTrue(any("'Photo Bracelet' already exists" in b for b in blockers))
        self.assertFalse(any("already exists" in b for b in ca.recommend(d, ns(name="Other"))["blockers"]))

    def test_malformed_fields_are_errors(self):
        good = ca.recommend(self.disc, ns(exit_code="BRACELET10"))
        cases = [
            lambda p: p["campaign"].__setitem__("statement_descriptor", 123),
            lambda p: p["packages"][0].__setitem__("name", 5),
            lambda p: p["packages"][0].__setitem__("product_variant_ids", 7),
            lambda p: p["offers"][0].__setitem__("name", ["x"]),
            lambda p: p["offers"][-1].__setitem__("code", 10),
            lambda p: p["packages"].append("not an object"),
            lambda p: p.__setitem__("offers", [{"key": "o", "condition": [], "benefit": {}}]),
        ]
        for i, mutate in enumerate(cases):
            p = json.loads(json.dumps(good))
            mutate(p)
            errs = ca.validate_plan(p)
            self.assertTrue(errs and all(isinstance(e, str) for e in errs), f"case {i}: {errs}")
        self.assertEqual(ca.validate_plan([]), ["plan must be a JSON object"])

    def test_dotenv_unterminated_quote(self):
        dotenv = self.root / ".env"
        secret = "abc" + "123secret"
        dotenv.write_text(f'TESTSTORE_NEXT_ADMIN_API_TOKEN="{secret}\n')
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.load_token("teststore", env={}, dotenv=dotenv)
        self.assertIn("unterminated quote", str(cm.exception))
        self.assertNotIn(secret, str(cm.exception))

    def test_audit_duplicate_key_conflict(self):
        # The wrong object comes first, so a last-one-wins listing would hide it.
        defs = [{"key": "device", "object": "order", "name": "Device"}] + metadata_defs()
        t = FakeTransport({("GET", ca.METADATA_PATH): (200, defs)})
        audit = ca.metadata_audit(make_client(t))
        self.assertEqual(audit["missing"], [])
        self.assertEqual(audit["conflicts"], [{"key": "device", "defined_on": "order", "needs": "attribution"}])

    def test_default_write_mode_is_private(self):
        p = self.root / "plan.json"
        ca.atomic_write_json(p, {"a": 1})
        self.assertEqual(oct(p.stat().st_mode & 0o777), "0o600")


SHIP = Decimal("6.95")  # the fixture plan's shipping price (ns() default)
HERO = ["hero-23", "hero-24", "hero-25", "hero-26"]  # hero 22's variants in the fixture


class FreeShippingPerCase(unittest.TestCase):
    """A live campaign, 2026-09-11. A free-shipping offer hand-edited to
    count >= 2 made verify add the shipping price to every case, failing Buy 2,
    Buy 3 and their exit-voucher rows by exactly -9.99 while the engine was right.
    The same run posted the upsell probe with a checkout shipping id and without
    the Cart API's upsell mode."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    @staticmethod
    def _engine(plan, ids, free_from=None, free_keys=None, ship_prices=None):
        """A fake carts/calculate that prices from the plan the way the live engine
        does: the best automatic package offer whose condition the cart meets (none
        in upsell mode), then any entered voucher, then shipping. Shipping has its
        own knobs so a test can make the live rule disagree with the plan.
        ship_prices maps a created shipping method id to its price; without it
        every shipping method costs the plan's first price."""
        key_of = {v: k for k, v in ids.items()}
        price = {p["key"]: Decimal(p["price"]) for p in plan["packages"]}
        autos = [o for o in plan["offers"]
                 if o.get("offer_type") == "offer" and o["benefit"]["type"] == "package_percentage"]
        codes = {o["code"]: o for o in plan["offers"] if o.get("offer_type") == "voucher"}
        ship_scope = set(free_keys or [p["key"] for p in plan["packages"] if p["role"] == "hero"])
        ship_price = Decimal(plan["shipping_methods"][0]["price"])

        def units(line_keys, scope):
            return sum(q for k, q in line_keys if k in scope)

        def calc(path, body):
            upsell = path.endswith("?upsell=true")
            line_keys = [(key_of[l["package_id"]], l["quantity"]) for l in body["lines"]]
            total = Decimal(0)
            for k, q in line_keys:
                unit = price[k]
                met = [] if upsell else [
                    o for o in autos if k in o["condition"]["package_keys"]
                    and units(line_keys, o["condition"]["package_keys"])
                    >= (o["condition"]["value"] if o["condition"]["type"] == "count" else 1)]
                if met:
                    best = max(met, key=lambda o: Decimal(o["benefit"]["value"]))
                    unit = ca.landed_unit(unit, Decimal(best["benefit"]["value"]), best["benefit"].get("price_rounding"))
                for code in body.get("vouchers", []):
                    v = codes[code]
                    if k in v["condition"]["package_keys"]:
                        unit = ca.landed_unit(unit, Decimal(v["benefit"]["value"]), v["benefit"].get("price_rounding"))
                total += unit * q
            if "shipping_method" in body and (free_from is None or units(line_keys, ship_scope) < free_from):
                total += ship_prices[body["shipping_method"]] if ship_prices else ship_price
            return 200, {"total": str(total), "lines": []}
        return calc

    def _run(self, plan, after_apply=None, during_probes=None, ship_override=None,
             before_apply=None, **engine):
        """apply the plan to a fake store, then verify it against _engine.
        before_apply(state) sets the store's behaviour before anything is created;
        after_apply(state, man, campaign_id, transport) can change the store or the
        journal in between, the way a dashboard edit or an interrupted run would;
        during_probes(state, campaign_id) runs on every calculate call. The engine
        prices each created shipping method by id at its planned price, except the
        shipping keys in ship_override, which it charges at the given price."""
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            state = fresh_state()
            if before_apply:
                before_apply(state)
            t = DynamicTransport(self.disc, state)
            man = ca.apply(make_client(t), plan, sha, Path(d) / "run-manifest.json", None)
            cid = man.data["campaign"]["id"]
            planned = {ca.ship_key(s): Decimal(s["price"]) for s in plan["shipping_methods"]}
            planned.update({k: Decimal(v) for k, v in (ship_override or {}).items()})
            engine.setdefault("ship_prices", {e["id"]: planned[e["key"]] for e in man.data["shipping_methods"]})
            if after_apply:
                after_apply(state, man, cid, t)
            ids = {e["key"]: e["id"] for e in man.data["packages"]}
            engine_calc = self._engine(plan, ids, **engine)

            def calc(path, body):
                if during_probes:
                    during_probes(state, cid)
                return engine_calc(path, body)
            tt = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
            cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                             transport=tt, clock=FakeClock(), sleep=lambda s: None)
            return ca.verify(make_client(t), cart, man, plan, sha), tt

    @staticmethod
    def _gate(plan):
        """print_plan's output lines, captured."""
        out, old = [], ca.print
        ca.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
        try:
            ca.print_plan(plan, None)
        finally:
            ca.print = old
        return out

    @staticmethod
    def _row(lines, tier):
        return next(l for l in lines if l.strip().startswith(tier + " ") and " total " in l)

    @staticmethod
    def _fs(plan, key="free-shipping"):
        return next(o for o in plan["offers"] if o["key"] == key)

    @staticmethod
    def _cases(report):
        return {c["case"]: c for c in report["calculate_cases"]}

    @staticmethod
    def _checks(report):
        return {c["check"]: c for c in report["admin_checks"]}

    def test_recommend_min_qty_emits_count_offer(self):
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        self.assertEqual(ca.validate_plan(plan), [])
        fs = self._fs(plan)
        self.assertEqual(fs["name"], "Photo Bracelet - Free Shipping - Buy 2+")
        self.assertEqual((fs["offer_type"], fs["code"]), ("offer", None))
        self.assertEqual(fs["condition"], {"type": "count", "value": 2, "package_keys": HERO})
        self.assertEqual(fs["benefit"], {"type": "shipping_percentage", "value": "100.00", "price_rounding": None})
        self.assertTrue(any("ships free: Buy 2, Buy 3; pays shipping: Buy 1" in r for r in plan["rationale"]))
        # the CLI flag reaches recommend
        with tempfile.TemporaryDirectory() as d:
            disc = Path(d) / "discovery.json"; ca.atomic_write_json(disc, self.disc)
            rc = ca.main(["recommend", "--discovery", str(disc), "--hero", "22", "--ctc", "low",
                          "--anchor-price", "49.95", "--shipping", "standard:6.95",
                          "--free-shipping-min-qty", "2", "--out", d])
            self.assertEqual(rc, 0)
            written = json.loads((Path(d) / "campaign-plan.json").read_text())
            self.assertEqual(self._fs(written)["condition"]["value"], 2)

    def test_recommend_min_qty_rejects_bad_values(self):
        for bad in (0, 1, True):
            with self.assertRaises(ca.CampaignAdminError, msg=repr(bad)):
                ca.recommend(self.disc, ns(free_shipping_min_qty=bad))
        # the two modes are alternatives; both at once must not quietly pick one
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(free_shipping=True, free_shipping_min_qty=2))
        self.assertIn("not both", str(cm.exception))
        # past the top tier, and on a single-unit high-CTC plan, verify could never reach it
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(free_shipping_min_qty=4))
        self.assertIn("cannot be verified", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", free_shipping_min_qty=2))
        self.assertIn("cannot be verified", str(cm.exception))

    def test_any_condition_unchanged(self):
        plan = ca.recommend(self.disc, ns(free_shipping=True))
        fs = self._fs(plan)
        self.assertEqual(fs["name"], "Photo Bracelet - Free Shipping")
        self.assertEqual(fs["condition"], {"type": "any", "value": None, "package_keys": HERO})
        ids = {p["key"]: 100 + i for i, p in enumerate(plan["packages"])}
        self.assertEqual({c[4] for c in ca._cart_cases_from_plan(plan, ids)}, {"free"})
        report, _ = self._run(plan, free_from=1)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertEqual(self._checks(report)["offer free-shipping free-shipping coverage"]["result"], "PASS")
        self.assertTrue(all(c["expected_shipping"] == "0.00" for c in report["calculate_cases"]))
        self.assertTrue(all(self._row(self._gate(plan), t).endswith("shipping free")
                            for t in ("Buy 1", "Buy 2", "Buy 3")))

    def test_count_two_buy_1_pays_buy_2_and_3_free(self):
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        ids = {p["key"]: 100 + i for i, p in enumerate(plan["packages"])}
        ship = {c[0]: c[4] for c in ca._cart_cases_from_plan(plan, ids)}
        self.assertEqual({k for k, v in ship.items() if v == "paid"},
                         {"Buy 1 single variant", "Buy 1 + exit voucher"})
        self.assertEqual({k for k, v in ship.items() if v == "free"},
                         {f"Buy {q} {s}" for q in (2, 3) for s in ("single variant", "mixed variants", "+ exit voucher")})
        report, _ = self._run(plan, free_from=2)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        for name, c in self._cases(report).items():
            self.assertEqual(c["expected_shipping"], "6.95" if name.startswith("Buy 1") else "0.00", name)
        cov = self._checks(report)["offer free-shipping free-shipping coverage"]
        self.assertEqual((cov["result"], cov["detail"]), ("PASS", "cases at 1 and 2 in-scope units"))
        gate = self._gate(plan)
        self.assertTrue(self._row(gate, "Buy 1").endswith("shipping 6.95"))
        self.assertTrue(self._row(gate, "Buy 2").endswith("shipping free"))

    def test_hand_edited_count_condition_shape(self):
        # recommend's any-condition offer, then only the condition edited; landed_prices untouched
        plan = ca.recommend(self.disc, ns(free_shipping=True))
        self._fs(plan)["condition"] = {"type": "count", "value": 2, "package_keys": list(HERO)}
        self.assertEqual(ca.validate_plan(plan), [])
        report, _ = self._run(plan, free_from=2)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        # A live engine that charges shipping on every cart fails exactly the 2+ rows,
        # each by the shipping price: the expectation really is per case.
        report, _ = self._run(plan, free_from=None)
        failed = {n: c for n, c in self._cases(report).items() if c["result"] == "FAIL"}
        self.assertEqual(set(failed), {f"Buy {q} {s}" for q in (2, 3)
                                       for s in ("single variant", "mixed variants", "+ exit voucher")})
        self.assertTrue(all(c["delta"] == "6.95" for c in failed.values()))

    def test_coverage_needs_exactly_n_minus_1_and_n(self):
        def shaped(value, drop=()):
            plan = ca.recommend(self.disc, ns(free_shipping=True))
            self._fs(plan)["condition"] = {"type": "count", "value": value, "package_keys": list(HERO)}
            plan["landed_prices"] = [l for l in plan["landed_prices"] if l["tier"] not in drop]
            return plan

        # (a) a threshold no landed row reaches: every total matches, coverage fails
        report, _ = self._run(shaped(4), free_from=4)
        self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]))
        cov = self._checks(report)["offer free-shipping free-shipping coverage"]
        self.assertEqual(cov["result"], "FAIL")
        self.assertIn("no calculate case with exactly 4 in-scope units", cov["detail"])
        self.assertIn("cases cover 1, 2, 3", cov["detail"])
        self.assertEqual(report["result"], "FAIL")
        # (b) nothing just below the threshold
        report, _ = self._run(shaped(2, drop=("Buy 1",)), free_from=2)
        cov = self._checks(report)["offer free-shipping free-shipping coverage"]
        self.assertEqual(cov["result"], "FAIL")
        self.assertIn("exactly 1 in-scope unit;", cov["detail"])
        # (c) Buy 1 and Buy 3 straddle a count-3 offer but would also pass a live
        # offer that starts at 2; only an exact N-1 case pins it
        report, _ = self._run(shaped(3, drop=("Buy 2",)), free_from=3)
        self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]))
        cov = self._checks(report)["offer free-shipping free-shipping coverage"]
        self.assertEqual(cov["result"], "FAIL")
        self.assertIn("exactly 2 in-scope units", cov["detail"])
        # (d) the same gap on the other side: Buy 3 ships free under count-2, but so
        # would it under a live count-3, so a case above N is not a case at N
        report, _ = self._run(shaped(2, drop=("Buy 2",)), free_from=2)
        self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]))
        cov = self._checks(report)["offer free-shipping free-shipping coverage"]
        self.assertEqual(cov["result"], "FAIL")
        self.assertIn("no calculate case with exactly 2 in-scope units", cov["detail"])

    @staticmethod
    def _dashboard_offer(state, cid):
        """An offer that exists on the live campaign but not in the plan."""
        state["seq"] += 1
        state["offers"].setdefault(cid, {})[state["seq"]] = {
            "id": state["seq"], "name": "Free Shipping (dashboard)", "offer_type": "offer",
            "condition": {"type": "count", "all_packages": True, "packages": []},
            "benefit": {"type": "shipping_percentage", "value": "100.00"}}

    def test_unplanned_live_offer_fails_verify(self):
        # kilo-local Codex finding: the masking analysis only knows planned offers.
        # A dashboard offer freeing Buy 2+ makes every total match even with the
        # planned offer's threshold wrong, so the live offer set itself is checked.
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))

        def dashboard_offer(state, man, cid, t=None):
            self._dashboard_offer(state, cid)

        report, _ = self._run(plan, after_apply=dashboard_offer, free_from=2)
        self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]))
        row = self._checks(report)["campaign offers match the plan"]
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("Free Shipping (dashboard)", row["detail"])
        self.assertEqual(report["result"], "FAIL")
        # and the clean run says what it checked
        report, _ = self._run(plan, free_from=2)
        row = self._checks(report)["campaign offers match the plan"]
        self.assertEqual((row["result"], row["detail"]), ("PASS", "5 live, all created by this run"))

    def test_planned_offer_missing_from_the_journal_is_a_row(self):
        # apply journals offers one at a time, so a run that stopped partway leaves
        # later planned offers with no entry; that must fail a row, not vanish
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))

        def lose_entry(state, man, cid, t=None):
            man.data["offers"] = [e for e in man.data["offers"] if e["key"] != "free-shipping"]

        report, _ = self._run(plan, after_apply=lose_entry, free_from=2)
        self.assertEqual(self._checks(report)["offer free-shipping journalled"]["result"], "FAIL")
        self.assertEqual(report["result"], "FAIL")

    def test_offer_added_while_probes_run_is_caught(self):
        # the live set is read after the calculate probes, not before
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        added = []

        def mid_run(state, cid):
            if not added:
                self._dashboard_offer(state, cid)
                added.append(True)

        report, _ = self._run(plan, during_probes=mid_run, free_from=2)
        self.assertEqual(self._checks(report)["campaign offers match the plan"]["result"], "FAIL")

    def test_created_journal_entry_without_id_does_not_hide_unidentified_live_offers(self):
        # Kilobot: a created journal entry with no id put None in `ours`, so a
        # live offer whose id is also None would look planned.
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))

        def mess(state, man, cid, t):
            man.data["offers"][0].pop("id", None)
            state["offers"].setdefault(cid, {})["ghost"] = {
                "id": None, "name": "Ghost", "offer_type": "offer",
                "condition": {"type": "any", "all_packages": True, "packages": []},
                "benefit": {"type": "shipping_percentage", "value": "100.00"}}

        report, _ = self._run(plan, after_apply=mess, free_from=2)
        row = self._checks(report)["campaign offers match the plan"]
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("Ghost", row["detail"])

    def test_plan_with_no_offers_still_checks_the_live_set(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", exit="0"))
        self.assertEqual(plan["offers"], [])
        report, _ = self._run(plan, after_apply=lambda state, man, cid, t: self._dashboard_offer(state, cid))
        self.assertEqual(self._checks(report)["campaign offers match the plan"]["result"], "FAIL")
        report, _ = self._run(plan)
        self.assertEqual(self._checks(report)["campaign offers match the plan"]["result"], "PASS")
        # a store without the offers endpoint and a plan with none: nothing to
        # compare, and verify must not start failing those stores
        def no_offers_endpoint(state, man, cid, t):
            t.fail_on[("GET", f"/api/admin/campaigns/{cid}/offers/")] = 404
        report, _ = self._run(plan, after_apply=no_offers_endpoint)
        self.assertNotIn("campaign offers match the plan", self._checks(report))
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))

    def test_shipping_too_cheap_to_tell_proves_nothing(self):
        # a free-shipping threshold on a 0.00 shipping method, or one within
        # calculate's per-unit rounding tolerance, prices the same either way
        for price in ("0.00", "0.02"):
            plan = ca.recommend(self.disc, ns(shipping=[f"standard:{price}"], free_shipping_min_qty=2))
            report, _ = self._run(plan, free_from=2)
            self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]), price)
            cov = self._checks(report)["offer free-shipping free-shipping coverage"]
            self.assertEqual(cov["result"], "FAIL", price)
            self.assertIn("rounding tolerance", cov["detail"], price)
        # 0.02 clears a 1-unit cart's tolerance but not a 2-unit one, so the N-1
        # side counts and the N side does not
        self.assertIn("no calculate case with exactly 2 in-scope units", cov["detail"])

    def test_partial_shipping_offer_is_flagged_at_the_gate(self):
        plan = ca.recommend(self.disc, ns(free_shipping=True))
        self._fs(plan)["benefit"]["value"] = "50.00"
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertTrue(self._row(self._gate(plan), "Buy 1").endswith("shipping partly discounted (not modelled; prove by hand)"))

    def test_code_without_offer_type_is_rejected_not_guessed(self):
        # apply sends a missing offer_type as "offer" and drops the code, so a
        # voucher that forgot the field would fire on every cart. validate_plan
        # refuses it instead of the gate guessing either way.
        plan = ca.recommend(self.disc, ns())
        plan["offers"].append({
            "key": "ship-voucher", "name": "Half off shipping", "code": "SHIP50",
            "condition": {"type": "any", "value": None, "package_keys": list(HERO)},
            "benefit": {"type": "shipping_percentage", "value": "50.00"},
        })
        self.assertTrue(any("has a code but no offer_type" in e for e in ca.validate_plan(plan)))

    def test_partial_shipping_offer_missing_offer_type_is_flagged_like_apply_sends_it(self):
        # no code and no offer_type: validate_plan and offer_body both treat it as
        # an automatic offer, so the gate must label the rows it touches
        plan = ca.recommend(self.disc, ns())
        plan["offers"].append({
            "key": "ship-half", "name": "Half off shipping",
            "condition": {"type": "any", "value": None, "package_keys": list(HERO)},
            "benefit": {"type": "shipping_percentage", "value": "50.00"},
        })
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertTrue(self._row(self._gate(plan), "Buy 1").endswith("shipping partly discounted (not modelled; prove by hand)"))

    def test_boolean_count_value_is_rejected(self):
        # bool is an int subclass: True would pass an isinstance check, be sent to
        # the API, and be ignored by verify's free-shipping reading
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        self._fs(plan)["condition"]["value"] = True
        self.assertTrue(any("count condition needs an integer value" in e for e in ca.validate_plan(plan)))

    def test_created_offer_deleted_during_probes_fails(self):
        # the per-offer read-back ran before the probes; an offer removed after it
        # must not leave the final live-set check passing
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        deleted = []

        def delete_one(state, cid):
            offers = state["offers"].get(cid, {})
            if offers and not deleted:
                k = next(iter(offers))
                deleted.append(offers.pop(k)["id"])

        report, _ = self._run(plan, during_probes=delete_one, free_from=2)
        row = self._checks(report)["campaign offers match the plan"]
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("no longer live", row["detail"])
        self.assertIn(str(deleted[0]), row["detail"])

    def test_live_offer_list_failure_is_not_a_pass_even_without_planned_offers(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", exit="0"))
        self.assertEqual(plan["offers"], [])

        def list_fails(state, man, cid, t):
            t.fail_on[("GET", f"/api/admin/campaigns/{cid}/offers/")] = 400

        report, _ = self._run(plan, after_apply=list_fails)
        row = self._checks(report)["campaign offers match the plan"]
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("could not list live offers", row["detail"])
        self.assertEqual(report["result"], "FAIL")

    def test_upsell_case_carries_no_shipping_and_uses_upsell_mode(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        report, tt = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        up = self._cases(report)["Upsell Music Photo Magnet voucher"]
        self.assertEqual(up["expected_total"], str(ca.landed_unit(Decimal("39.95"), Decimal(50), None)))
        self.assertIsNone(up["expected_shipping"])
        self.assertEqual(up["shipping"], "none")
        for _, path, body, _ in tt.calls:
            upsell = body.get("vouchers") == ["PHOTOMAGNET50"]
            self.assertEqual(path.endswith("?upsell=true"), upsell, path)
            self.assertEqual("shipping_method" in body, not upsell, body)

    def test_upsell_mode_shields_upsell_from_checkout_offers(self):
        # A hand-authored "one more" upsell on a hero package sits inside the tier
        # offers' scope. Only upsell mode keeps Buy 1's 50% off it; without the
        # query the engine would charge 12.48 (tier, then voucher) instead of 24.97.
        plan = ca.recommend(self.disc, ns())
        plan["offers"].append({"key": "upsell-more", "name": "Photo Bracelet - 50%",
                               "offer_type": "voucher", "code": "BRACELETMORE50",
                               "condition": {"type": "any", "value": None, "package_keys": ["hero-23"]},
                               "benefit": {"type": "package_percentage", "value": "50.00", "price_rounding": None}})
        unit = ca.money(ca.landed_unit(Decimal("49.95"), Decimal(50), None))
        plan["landed_prices"].append({"tier": "Upsell one more", "kind": "upsell", "qty": 1,
                                      "offer_key": "upsell-more", "package_keys": ["hero-23"],
                                      "anchor": "49.95", "pct": 50, "unit_after": unit, "order_total": unit})
        self.assertEqual(ca.validate_plan(plan), [])
        report, tt = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertEqual(self._cases(report)["Upsell one more voucher"]["got_total"], "24.97")
        self.assertTrue(any(p.endswith("?upsell=true") and b.get("vouchers") == ["BRACELETMORE50"]
                            for _, p, b, _ in tt.calls))

    def test_subset_scope_non_first_key(self):
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        self._fs(plan)["condition"]["package_keys"] = ["hero-24"]  # the rows start at hero-23
        ids = {p["key"]: 100 + i for i, p in enumerate(plan["packages"])}
        ship = {c[0]: c[4] for c in ca._cart_cases_from_plan(plan, ids)}
        self.assertEqual(ship["Buy 1 single variant hero-24"], "paid")
        self.assertEqual(ship["Buy 2 single variant hero-24"], "free")
        self.assertEqual(ship["Buy 3 single variant hero-24"], "free")
        self.assertEqual(ship["Buy 2 single variant"], "paid")    # hero-23 is out of scope
        self.assertEqual(ship["Buy 2 mixed variants"], "paid")    # one hero-24 unit
        report, _ = self._run(plan, free_from=2, free_keys=["hero-24"])
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertEqual(self._checks(report)["offer free-shipping free-shipping coverage"]["result"], "PASS")
        gate = self._gate(plan)
        self.assertTrue(self._row(gate, "Buy 1").endswith("shipping 6.95"))
        self.assertTrue(self._row(gate, "Buy 2").endswith("shipping depends on variant mix"))

    def test_unsupported_offers_keep_the_free_shipping_intent(self):
        d = json.loads(json.dumps(self.disc)); d["offers_supported"] = False
        plan = ca.recommend(d, ns(free_shipping_min_qty=2))
        self.assertEqual(plan["offers"], [])
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertTrue(any("Buy 2+" in h and "probes at 1 and 2 units" in h for h in plan["handoff"]))
        self.assertTrue(any("verify will expect paid shipping" in r for r in plan["rationale"]))
        plan = ca.recommend(d, ns(free_shipping=True))
        self.assertTrue(any("every order" in h and "probes at 1 unit" in h for h in plan["handoff"]))
        # the range check still runs, since the operator is about to build this by hand
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(d, ns(free_shipping_min_qty=5))

    def test_overlapping_offers_are_reported_not_assumed(self):
        # (a) a count-3 offer beside count-2 over the same packages: the Buy 2 cart
        # is freed by count-2 either way, so nothing can tell 3 from 4
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        extra = json.loads(json.dumps(self._fs(plan)))
        extra.update(key="free-shipping-3", name="Photo Bracelet - Free Shipping - Buy 3+")
        extra["condition"]["value"] = 3
        plan["offers"].append(extra)
        self.assertEqual(ca.validate_plan(plan), [])
        report, _ = self._run(plan, free_from=2)
        self.assertTrue(all(c["result"] == "PASS" for c in report["calculate_cases"]))
        checks = self._checks(report)
        self.assertEqual(checks["offer free-shipping free-shipping coverage"]["result"], "PASS")
        masked = checks["offer free-shipping-3 free-shipping coverage"]
        self.assertEqual(masked["result"], "FAIL")
        self.assertIn("masked by offer free-shipping", masked["detail"])
        self.assertEqual(report["result"], "FAIL")
        # (b) two subset offers that between them free every single-variant Buy 2
        # cart, but not every mix: the gate must not promise free shipping
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        self._fs(plan)["condition"]["package_keys"] = ["hero-23", "hero-24"]
        other = json.loads(json.dumps(self._fs(plan)))
        other.update(key="free-shipping-b", name="Photo Bracelet - Free Shipping - Buy 2+ B")
        other["condition"]["package_keys"] = ["hero-25", "hero-26"]
        plan["offers"].append(other)
        self.assertTrue(ca._ships_free(plan, [("hero-23", 2)]))
        self.assertFalse(ca._ships_free(plan, [("hero-23", 1), ("hero-25", 1)]))
        self.assertTrue(self._row(self._gate(plan), "Buy 2").endswith("shipping depends on variant mix"))
        # (c) masking at N, found by the kilo-local Codex pass: every cart at the
        # target's threshold is also freed by another offer, so a live target that
        # started at 3 would price the same. The N-1 side alone must not pass it.
        offers = [("target", 2, frozenset({"a", "b"})), ("mask-a", 1, frozenset({"a"})),
                  ("mask-b", 2, frozenset({"b"}))]
        cases = [("Buy 1 a", [], [], 0, "free", [("a", 1)]), ("Buy 1 b", [], [], 0, "paid", [("b", 1)]),
                 ("Buy 2 a", [], [], 0, "free", [("a", 2)]), ("Buy 2 b", [], [], 0, "free", [("b", 2)]),
                 ("Buy 2 mixed", [], [], 0, "free", [("a", 1), ("b", 1)])]
        ok, detail = ca._free_shipping_coverage(cases, offers, "target", 2, frozenset({"a", "b"}), lambda c: SHIP)
        self.assertFalse(ok)
        self.assertIn("masked by offer mask-a at 2 in-scope units", detail)
        # one unmasked cart at N is enough: lift mask-b to 3 and "Buy 2 b" is the target's alone
        offers[2] = ("mask-b", 3, frozenset({"b"}))
        self.assertTrue(ca._free_shipping_coverage(cases, offers, "target", 2, frozenset({"a", "b"}), lambda c: SHIP)[0])
        # (d) masking at N-1 only: the one-unit cart is freed by mask-a, so a live
        # target at 1 would price the same even though N itself is clean
        below_masked = [c for c in cases if c[0] != "Buy 1 b"]
        ok, detail = ca._free_shipping_coverage(below_masked, offers, "target", 2, frozenset({"a", "b"}), lambda c: SHIP)
        self.assertFalse(ok)
        self.assertIn("masked by offer mask-a at 1 in-scope unit;", detail)
        # no shipping method on the campaign: nothing a cart can show
        ok, detail = ca._free_shipping_coverage(cases, offers, "target", 2, frozenset({"a", "b"}), lambda c: None)
        self.assertFalse(ok)
        self.assertIn("no shipping method", detail)


LADDER = ["standard:9.99:ship-1", "tracked:12.99:ship-2", "priority:14.99:ship-3", "overnight:16.99:ship-4"]
LADDER_CODES = ["standard", "tracked", "priority", "overnight"]
RUNG = {"Buy 1": "9.99", "Buy 2": "12.99", "Buy 3": "14.99", "Buy 4": "16.99"}


def ladder_plan(disc, **kw):
    """Four tiers on four store codes, each tier row priced with its own method."""
    plan = ca.recommend(disc, ns(tiers="50,55,60,65", shipping=LADDER, **kw))
    for i, row in enumerate(r for r in plan["landed_prices"] if r["kind"] == "tier"):
        row["shipping_key"] = f"ship-{i + 1}"
    return plan


class TieredShippingLadder(unittest.TestCase):
    """Several campaign shipping methods, one per store code (the Campaigns API
    rejects a repeated code), identified by a plan-level key, each landed row
    priced with its own."""

    _engine = staticmethod(FreeShippingPerCase._engine)
    _run = FreeShippingPerCase._run
    _gate = staticmethod(FreeShippingPerCase._gate)
    _row = staticmethod(FreeShippingPerCase._row)
    _cases = staticmethod(FreeShippingPerCase._cases)
    _checks = staticmethod(FreeShippingPerCase._checks)

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _copy(self, plan):
        return json.loads(json.dumps(plan))

    def _applied(self, plan, transport=None):
        pp = Path(self.tmp.name) / "plan.json"
        ca.atomic_write_json(pp, plan)
        sha = ca.sha256_file(pp)
        state = fresh_state()
        t = transport(state) if transport else DynamicTransport(self.disc, state)
        return state, t, sha, Path(self.tmp.name) / "run-manifest.json"

    @staticmethod
    def _ship_posts(t):
        return [c for c in t.calls if c[0] == "POST" and c[1].endswith("/shipping-methods/")]

    # --- plan validation ----------------------------------------------------

    def test_one_method_per_code_validates(self):
        plan = ladder_plan(self.disc)
        self.assertEqual(ca.validate_plan(plan), [])
        ships = [r[2] for r in ca.render_requests(plan) if r[1].endswith("/shipping-methods/")]
        self.assertEqual(ships, [{"shipping_method": c, "price": p}
                                 for c, p in zip(LADDER_CODES, ("9.99", "12.99", "14.99", "16.99"))])

    def test_repeated_code_rejected_for_create_only(self):
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][1]["shipping_method"] = "standard"
        errs = ca.validate_plan(plan)
        self.assertTrue(any("both use store code 'standard'" in e for e in errs), errs)
        # a run created before the rule can still be verified
        self.assertEqual(ca.validate_plan(plan, for_create=False), [])
        plan["shipping_methods"][1]["price"] = "9.99"
        errs = ca.validate_plan(plan, for_create=False)
        self.assertTrue(any("duplicates code 'standard' at 9.99" in e for e in errs), errs)

    def test_duplicate_keys_reject(self):
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][1]["key"] = "ship-1"
        self.assertTrue(any("duplicate shipping key 'ship-1'" in e for e in ca.validate_plan(plan)))
        # a key equal to another entry's defaulted code collides as well
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][0].pop("key")
        plan["shipping_methods"][1]["key"] = "standard"
        plan["landed_prices"][0].pop("shipping_key")
        self.assertTrue(any("duplicate shipping key 'standard'" in e for e in ca.validate_plan(plan)))
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][0]["key"] = ""
        self.assertTrue(any("key must be a non-empty string" in e for e in ca.validate_plan(plan)))
        # two explicit keys that collide name both entries
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][2]["key"] = "ship-2"
        errs = [e for e in ca.validate_plan(plan) if "duplicate shipping key 'ship-2'" in e]
        self.assertTrue(errs and "at 12.99" in errs[0] and "at 14.99" in errs[0], errs)

    def test_row_ship_key_resolves(self):
        plan = ladder_plan(self.disc)
        plan["landed_prices"][0]["shipping_key"] = "ship-9"
        self.assertTrue(any("shipping_key 'ship-9' does not resolve" in e for e in ca.validate_plan(plan)))
        high = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        up = next(r for r in high["landed_prices"] if r["kind"] == "upsell")
        up["shipping_key"] = "standard"
        self.assertTrue(any("upsell rows carry no shipping method" in e for e in ca.validate_plan(high)))

    def test_recommend_shipping_key_segment(self):
        plan = ca.recommend(self.disc, ns(shipping=["standard:9.99:ship-1"]))
        self.assertEqual(plan["shipping_methods"], [{"shipping_method": "standard", "price": "9.99", "key": "ship-1"}])
        for twice in (["standard:9.99", "standard:12.99"], ["standard:9.99:ship-1", "standard:12.99:ship-2"]):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.recommend(self.disc, ns(shipping=twice))
            self.assertIn("one campaign shipping method per store code", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(shipping=["standard:9.99:ship-1", "tracked:12.99:ship-1"]))
        self.assertIn("shipping key 'ship-1' given twice", str(cm.exception))
        for bad in ("standard:9.99:", "standard:9.99:a:b", "standard"):
            with self.assertRaises(ca.CampaignAdminError, msg=bad):
                ca.recommend(self.disc, ns(shipping=[bad]))
        self.assertNotIn("key", ca.recommend(self.disc, ns())["shipping_methods"][0])

    # --- apply, resume, teardown --------------------------------------------

    def test_manifest_keys_by_shipping_key(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp = self._applied(plan)
        man = ca.apply(make_client(t), plan, sha, mp, None)
        self.assertEqual([e["key"] for e in man.data["shipping_methods"]], ["ship-1", "ship-2", "ship-3", "ship-4"])
        self.assertEqual([e["status"] for e in man.data["shipping_methods"]], ["created"] * 4)
        remote = list(state["shipping-methods"][man.data["campaign"]["id"]].values())
        self.assertEqual([(r["shipping_method"], r["price"]) for r in remote],
                         list(zip(LADDER_CODES, ("9.99", "12.99", "14.99", "16.99"))))
        self.assertEqual(len({e["id"] for e in man.data["shipping_methods"]}), 4)
        self.assertEqual([c[2]["shipping_method"] for c in self._ship_posts(t)], LADDER_CODES)

    def _interrupt_second_shipping_post(self, plan, lands):
        """apply with the second shipping POST answering 500; `lands` decides
        whether the row was written before the answer was lost."""
        state, t, sha, mp = self._applied(plan)
        real, hits = t.child_create, {"n": 0}

        def flaky(kind):
            h = real(kind)

            def w(path, body):
                if kind == "shipping-methods":
                    hits["n"] += 1
                    if hits["n"] == 2:
                        if lands:
                            h(path, body)
                        return 500, {"detail": "boom"}
                return h(path, body)
            return w
        t.child_create = flaky
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(make_client(t), plan, sha, mp, None)
        t.child_create = real
        man = json.loads(mp.read_text())
        self.assertEqual([e["status"] for e in man["shipping_methods"]], ["created", "pending"])
        return state, t, sha, mp, man

    def test_resume_reconciles_by_code_and_price(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp, before = self._interrupt_second_shipping_post(plan, lands=True)
        cid = before["campaign"]["id"]
        lost_id = next(r["id"] for r in state["shipping-methods"][cid].values() if r["price"] == "12.99")
        t.calls.clear()
        man = ca.apply(make_client(t), plan, sha, mp, mp)
        e2 = man.entry("shipping_methods", "ship-2")
        self.assertEqual((e2["status"], e2["id"], e2.get("reconciled")), ("created", lost_id, True))
        self.assertIsNone(e2.get("intent"))
        self.assertEqual(man.entry("shipping_methods", "ship-1")["id"], before["shipping_methods"][0]["id"])
        self.assertEqual([c[2]["price"] for c in self._ship_posts(t)], ["14.99", "16.99"])
        self.assertEqual(len(state["shipping-methods"][cid]), 4)

    def test_resume_after_a_shipping_post_that_never_landed(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp, before = self._interrupt_second_shipping_post(plan, lands=False)
        cid = before["campaign"]["id"]
        t.calls.clear()
        man = ca.apply(make_client(t), plan, sha, mp, mp)
        # the one live row on that code is ship-1's and must never be claimed for ship-2
        self.assertEqual(man.entry("shipping_methods", "ship-1")["id"], before["shipping_methods"][0]["id"])
        self.assertEqual([c[2]["price"] for c in self._ship_posts(t)], ["12.99", "14.99", "16.99"])
        ids = [e["id"] for e in man.data["shipping_methods"]]
        self.assertEqual(len(set(ids)), 4)
        self.assertEqual(sorted(state["shipping-methods"][cid]), sorted(ids))

    def _assert_resume_stops(self, plan, t, sha, mp, state, cid, needle):
        rows = len(state["shipping-methods"][cid])
        t.calls.clear()
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(t), plan, sha, mp, mp)
        self.assertIn(needle, str(cm.exception))
        self.assertEqual(self._ship_posts(t), [])
        self.assertEqual(len(state["shipping-methods"][cid]), rows)
        man = json.loads(mp.read_text())
        self.assertEqual(next(e for e in man["shipping_methods"] if e["key"] == "ship-2")["status"], "pending")

    def test_resume_stops_when_the_lost_rows_price_was_edited(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp, before = self._interrupt_second_shipping_post(plan, lands=True)
        cid = before["campaign"]["id"]
        lost = next(r for r in state["shipping-methods"][cid].values() if r["price"] == "12.99")
        lost["prices"] = [{"currency": "USD", "price": "14.99"}]
        self._assert_resume_stops(plan, t, sha, mp, state, cid, "matches 0 remote entries")

    def test_resume_stops_on_ambiguous_shipping_match(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp, before = self._interrupt_second_shipping_post(plan, lands=True)
        cid = before["campaign"]["id"]
        state["shipping-methods"][cid][999] = {"id": 999, "shipping_method": "tracked", "price": "12.99",
                                              "prices": [{"currency": "USD", "price": "12.99"}]}
        self._assert_resume_stops(plan, t, sha, mp, state, cid, "matches 2 remote entries")

    def test_resume_stops_when_a_same_code_row_has_no_readable_price(self):
        plan = ladder_plan(self.disc)
        for unreadable in ([], [{"currency": "EUR", "price": "12.99"}], [{"currency": "USD", "price": "NaN"}]):
            with self.subTest(prices=unreadable):
                self.tmp.cleanup()
                self.tmp = tempfile.TemporaryDirectory()
                state, t, sha, mp, before = self._interrupt_second_shipping_post(plan, lands=True)
                cid = before["campaign"]["id"]
                lost = next(r for r in state["shipping-methods"][cid].values() if r["price"] == "12.99")
                lost["prices"] = unreadable
                self._assert_resume_stops(plan, t, sha, mp, state, cid, "unreadable")

    def test_teardown_deletes_every_shipping_entry(self):
        plan = ladder_plan(self.disc)
        state, t, sha, mp = self._applied(plan)
        man = ca.apply(make_client(t), plan, sha, mp, None)
        cid = man.data["campaign"]["id"]
        # a price changed remotely on one entry: identity fails before any DELETE
        sid = man.entry("shipping_methods", "ship-3")["id"]
        state["shipping-methods"][cid][sid]["prices"] = [{"currency": "USD", "price": "15.99"}]
        with self.assertRaises(ca.CampaignAdminError):
            ca.teardown(make_client(t), man, plan, sha, lambda: True)
        self.assertFalse([c for c in t.calls if c[0] == "DELETE"])
        state["shipping-methods"][cid][sid]["prices"] = [{"currency": "USD", "price": "NaN"}]
        with self.assertRaises(ca.CampaignAdminError):
            ca.teardown(make_client(t), man, plan, sha, lambda: True)
        self.assertFalse([c for c in t.calls if c[0] == "DELETE"])
        state["shipping-methods"][cid][sid]["prices"] = [{"currency": "USD", "price": "14.99"}]
        ca.teardown(make_client(t), man, plan, sha, lambda: True)
        ship_deletes = [c[1] for c in t.calls if c[0] == "DELETE" and "/shipping-methods/" in c[1]]
        self.assertEqual(len(ship_deletes), 4)
        self.assertEqual(state["shipping-methods"][cid], {})
        self.assertEqual([e["status"] for e in man.data["shipping_methods"]], ["deleted"] * 4)

    # --- verify and the plan gate -------------------------------------------

    def test_verify_prices_each_row_with_its_shipping_key(self):
        plan = ladder_plan(self.disc)
        report, tt = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        cases = report["calculate_cases"]
        self.assertTrue(any(c["case"] == "Buy 4 mixed variants" for c in cases))
        self.assertTrue(any(c["case"] == "Buy 3 + exit voucher" for c in cases))
        for c in cases:
            tier = c["case"][:5]
            self.assertEqual(c["expected_shipping"], RUNG[tier], c["case"])
            self.assertEqual(c["shipping_key"], f"ship-{tier[-1]}", c["case"])
        # the four rungs really went out as four different method ids
        self.assertEqual(len({b["shipping_method"] for _, _, b, _ in tt.calls}), 4)
        # a live rung that charges the wrong price fails exactly that tier's rows
        report, _ = self._run(plan, ship_override={"ship-2": "9.99"})
        failed = sorted(c["case"] for c in report["calculate_cases"] if c["result"] == "FAIL")
        self.assertTrue(failed)
        self.assertTrue(all(name.startswith("Buy 2 ") for name in failed), failed)
        self.assertEqual(len(failed), sum(1 for c in cases if c["case"].startswith("Buy 2 ")))

    def test_uncreated_rung_fails(self):
        plan = ladder_plan(self.disc)

        def drop_ship_4(state, man, cid, t):
            man.data["shipping_methods"] = [e for e in man.data["shipping_methods"] if e["key"] != "ship-4"]
            man.save()
        report, tt = self._run(plan, after_apply=drop_ship_4)
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(self._checks(report)["shipping ship-4 journalled"]["result"], "FAIL")
        buy4 = [c for c in report["calculate_cases"] if c["case"].startswith("Buy 4 ")]
        self.assertTrue(buy4)
        self.assertTrue(all(c["result"] == "FAIL" and c["status"] is None and "was not created" in c["error"] for c in buy4))
        others = [c for c in report["calculate_cases"] if not c["case"].startswith("Buy 4 ")]
        self.assertTrue(all(c["result"] == "PASS" for c in others))
        self.assertEqual(len(tt.calls), len(others))
        out, old = [], ca.print
        ca.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
        try:
            ca.print_verify(report)
        finally:
            ca.print = old
        self.assertTrue(any("Buy 4 single variant" in l and "shipping method ship-4 not created" in l for l in out), out)

    def test_verify_checks_the_code_and_defaults_to_the_plans_first_method(self):
        plan = ladder_plan(self.disc)
        for row in plan["landed_prices"]:
            row.pop("shipping_key", None)

        def recode_ship_2(state, man, cid, t):
            sid = man.entry("shipping_methods", "ship-2")["id"]
            state["shipping-methods"][cid][sid]["shipping_method"] = "express"
        report, _ = self._run(plan, after_apply=recode_ship_2)
        checks = self._checks(report)
        self.assertEqual(checks["shipping ship-2 code"]["result"], "FAIL")

        def nan_ship_3(state, man, cid, t):
            sid = man.entry("shipping_methods", "ship-3")["id"]
            state["shipping-methods"][cid][sid]["prices"] = [{"currency": "USD", "price": "NaN"}]
        nan_checks = self._checks(self._run(plan, after_apply=nan_ship_3)[0])
        self.assertEqual(nan_checks["shipping ship-3 price"]["result"], "FAIL")
        self.assertEqual(nan_checks["shipping ship-3 price"]["detail"], "unreadable vs 14.99")
        self.assertEqual(checks["shipping ship-1 code"]["result"], "PASS")
        # rows without a key use ship-1, the plan's first method
        self.assertTrue(all(c["shipping_key"] == "ship-1" and c["expected_shipping"] == "9.99"
                            for c in report["calculate_cases"]))

        # the plan's first method was never created: keyless rows fail, they do not borrow ship-2
        def drop_ship_1(state, man, cid, t):
            man.data["shipping_methods"] = [e for e in man.data["shipping_methods"] if e["key"] != "ship-1"]
            man.save()
        report, tt = self._run(plan, after_apply=drop_ship_1)
        self.assertEqual(report["result"], "FAIL")
        self.assertTrue(all(c["result"] == "FAIL" and "'ship-1' was not created" in c["error"]
                            for c in report["calculate_cases"]))
        self.assertEqual(tt.calls, [])

    def test_ladder_with_free_shipping_threshold(self):
        plan = ladder_plan(self.disc, free_shipping_min_qty=2)
        self.assertEqual(ca.validate_plan(plan), [])
        report, _ = self._run(plan, free_from=2)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        for c in report["calculate_cases"]:
            want = "9.99" if c["case"].startswith("Buy 1 ") else "0.00"
            self.assertEqual(c["expected_shipping"], want, c["case"])
        self.assertEqual(self._checks(report)["offer free-shipping free-shipping coverage"]["result"], "PASS")

    def test_gate_labels_show_the_rows_shipping_price(self):
        lines = self._gate(ladder_plan(self.disc))
        for tier, price in RUNG.items():
            self.assertTrue(self._row(lines, tier).endswith(f"shipping {price}"), tier)
        self.assertIn("  standard  9.99  key ship-1", lines)
        self.assertIn("  overnight  16.99  key ship-4", lines)
        # the gate runs before validation: an unresolved key says so instead of borrowing a price
        plan = ladder_plan(self.disc)
        plan["landed_prices"][1]["shipping_key"] = "ship-9"
        self.assertTrue(self._row(self._gate(plan), "Buy 2").endswith("shipping ? (key 'ship-9' unresolved)"))

    def test_code_only_plan_unchanged(self):
        plan = ca.recommend(self.disc, ns())
        self.assertEqual(plan["shipping_methods"], [{"shipping_method": "standard", "price": "6.95"}])
        self.assertEqual(ca.validate_plan(plan), [])
        lines = self._gate(plan)
        self.assertIn("  standard  6.95", lines)
        self.assertFalse(any(" key " in l for l in lines))
        self.assertTrue(self._row(lines, "Buy 3").endswith("shipping 6.95"))
        report, tt = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        state, t, sha, mp = self._applied(plan)
        man = ca.apply(make_client(t), plan, sha, mp, None)
        self.assertEqual([e["key"] for e in man.data["shipping_methods"]], ["standard"])
        for c in report["calculate_cases"]:
            self.assertEqual((c["shipping_key"], c["expected_shipping"]), ("standard", "6.95"), c["case"])
        self.assertTrue(all("shipping_method" in b for _, _, b, _ in tt.calls))
        # an upsell carries no key, as before
        high = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        report, _ = self._run(high)
        up = next(c for c in report["calculate_cases"] if c["shipping"] == "none")
        self.assertEqual((up["shipping_key"], up["expected_shipping"]), (None, None))



# --------------------------------------------------------------------------- #
# 0.7.1: the permission list, upsell package reuse and one voucher per product
# --------------------------------------------------------------------------- #

class ScopeDiagnostics(unittest.TestCase):
    """A field run: a key built to the documented six scopes was rejected on the
    first request (GET /store/ needs store:read), and the error never said which
    scope was missing, so the operator granted everything."""

    def test_required_scopes_include_store_read(self):
        scopes = [x.strip() for x in ca.REQUIRED_SCOPES.split(",")]
        self.assertEqual(scopes[0], "store:read")
        self.assertEqual(len(scopes), 7)

    def test_store_403_names_store_read_and_every_scope(self):
        t = FakeTransport({("GET", "/api/admin/store/"): (403, {"detail": "no"})})
        with self.assertRaises(ca.HttpStatusError) as cm:
            make_client(t).get_ok("/api/admin/store/")
        msg = str(cm.exception)
        self.assertIn("needs the store:read permission", msg)
        self.assertIn(ca.REQUIRED_SCOPES, msg)
        self.assertEqual(cm.exception.status, 403)

    def test_scopeless_endpoint_blames_the_token(self):
        t = FakeTransport({("GET", "/api/admin/shipping-methods/"): (401, {})})
        with self.assertRaises(ca.HttpStatusError) as cm:
            make_client(t).get_ok("/api/admin/shipping-methods/")
        self.assertIn("needs no scope, so the token itself was rejected", str(cm.exception))

    def test_scope_for_absolute_pagination_url(self):
        self.assertEqual(ca.scope_for("GET", ORIGIN + "/api/admin/products/?cursor=abc"), "catalogue:read")
        self.assertEqual(ca.scope_for("GET", "/api/admin/campaigns/?page_size=100"), "campaigns:read")
        offer_path = "/api/admin/campaigns/{cid}/offers/{oid}/".format(cid=5, oid=9)
        self.assertEqual(ca.scope_for("DELETE", offer_path), "campaigns:write")
        self.assertEqual(ca.scope_for("POST", ca.METADATA_PATH), "metadata:write")
        self.assertEqual(ca.scope_for("GET", "/api/admin/shipping-methods/"), ca.NO_SCOPE)

    def test_unknown_path_makes_no_scope_claim(self):
        self.assertIsNone(ca.scope_for("GET", "/api/admin/orders/"))
        hint = ca.auth_hint(403, "GET", "/api/admin/orders/")
        self.assertNotIn("needs", hint)
        self.assertIn(ca.REQUIRED_SCOPES, hint)
        self.assertEqual(ca.auth_hint(500, "GET", "/api/admin/store/"), "")

    def test_forbidden_create_names_write_scope(self):
        t = FakeTransport({("POST", "/api/admin/campaigns/"): (403, {})})
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca._created(make_client(t), "POST", "/api/admin/campaigns/", {}, "create campaign")
        self.assertIn("needs the campaigns:write permission", str(cm.exception))

    def test_metadata_post_403_names_metadata_write(self):
        mp = MetadataProvisioning()
        t = mp.store(metadata_defs(skip=("device",)), post_status=403)
        rc, out = mp.captured(ca.metadata_provision, make_client(t), "teststore", True)
        self.assertEqual(rc, 1)
        self.assertIn("needs the metadata:write permission", out)

    def test_teardown_delete_403_names_campaigns_write(self):
        disc = load_fixture("discovery.json")
        plan = ca.recommend(disc, ns(name="Bracelet - scope", exit_code="BRACELET10"))
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            t = DynamicTransport(disc, fresh_state())
            man = ca.apply(make_client(t), plan, sha, Path(d) / "run-manifest.json", None)
            cid, oid = man.data["campaign"]["id"], man.data["offers"][0]["id"]
            t.fail_on[("DELETE", f"/api/admin/campaigns/{cid}/offers/{oid}/")] = 403
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.teardown(make_client(t), man, plan, sha, lambda: True)
        self.assertIn("needs the campaigns:write permission", str(cm.exception))

    def test_verify_offer_retrieve_403_names_campaigns_read(self):
        fs = UpsellVoucherVerify()
        fs.setUp()
        plan = ca.recommend(fs.disc, ns())

        def block_retrieve(state, man, cid, t):
            oid = man.data["offers"][0]["id"]
            t.fail_on[("GET", f"/api/admin/campaigns/{cid}/offers/{oid}/")] = 403
        report, _ = fs._run(plan, after_apply=block_retrieve)
        check = fs._checks(report)[f"offer {plan['offers'][0]['key']}"]
        self.assertEqual(check["result"], "FAIL")
        self.assertIn("needs the campaigns:read permission", check["detail"])

    def test_docs_and_catalog_list_every_scope(self):
        skill = (SKILL_DIR / "SKILL.md").read_text()
        entry = next(e for e in json.loads((SKILL_DIR.parent / "skills.json").read_text())["skills"]
                     if e["id"] == "next-campaigns-create")
        prereqs = " ".join(entry["prerequisites"])
        for scope in (x.strip() for x in ca.REQUIRED_SCOPES.split(",")):
            self.assertIn(f"| `{scope}` |", skill, scope)
            self.assertIn(scope, prereqs, scope)
        self.assertNotIn("all six", skill)


class UpsellPackagesAndVouchers(unittest.TestCase):
    """A field run: the hero product as an upsell at the same price got a second
    identical package, and two variants of one upsell product got two offers."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    @staticmethod
    def _upsell_offers(plan):
        return [o for o in plan["offers"] if o["key"].startswith("upsell-")]

    def test_hero_upsell_at_same_price_reuses_hero_package(self):
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual([p["key"] for p in plan["packages"]], HERO)
        (up,) = self._upsell_offers(plan)
        self.assertEqual((up["key"], up["offer_type"], up["condition"]["package_keys"]),
                         ("upsell-22-50", "voucher", ["hero-23"]))
        row = next(l for l in plan["landed_prices"] if l["kind"] == "upsell")
        self.assertEqual((row["tier"], row["package_keys"], row["offer_key"]),
                         ("Upsell Photo Bracelet", ["hero-23"], "upsell-22-50"))
        self.assertTrue(any("reuses package hero-23" in r for r in plan["rationale"]))
        self.assertTrue(any("also applies at checkout" in h and up["code"] in h for h in plan["handoff"]))

    def test_upsell_at_other_price_is_refused_with_a_percentage(self):
        # one package per variant: 39.95 at 50% off lands 19.97; on the 49.95 hero
        # package that is 60.02%, so the nearest whole 60% (19.98) is suggested
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(upsell=["23:39.95:50"]))
        msg = str(cm.exception)
        self.assertIn("already package hero-23 at 49.95", msg)
        self.assertIn("one package per variant", msg)
        self.assertIn("--upsell 23:49.95:60 (lands at 19.98)", msg)
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:60"]))
        self.assertEqual(next(l for l in plan["landed_prices"] if l["kind"] == "upsell")["unit_after"], "19.98")

    def test_standalone_upsell_gets_its_own_package(self):
        plan = ca.recommend(self.disc, ns(upsell=["16:39.95:50"]))
        self.assertEqual(next(p for p in plan["packages"] if p["key"] == "upsell-16")["price"], "39.95")
        self.assertFalse(any("also applies at checkout" in h for h in plan["handoff"]))

    def test_upsell_reuses_a_bump_package_at_its_price(self):
        plan = ca.recommend(self.disc, ns(bump=["7:9.95"], upsell=["7:9.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertNotIn("upsell-7", [p["key"] for p in plan["packages"]])
        (up,) = self._upsell_offers(plan)
        self.assertEqual(up["condition"]["package_keys"], ["bump-7"])
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(bump=["7:9.95"], upsell=["7:8.95:50"]))
        self.assertIn("already package bump-7", str(cm.exception))

    def test_bump_on_a_packaged_variant_is_refused(self):
        for kw in (dict(bump=["23:49.95"]), dict(bump=["7:9.95", "7:9.95"])):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.recommend(self.disc, ns(**kw))
            self.assertIn("one package per variant", str(cm.exception))

    def test_hand_edited_duplicate_variant_is_rejected_for_create_only(self):
        plan = ca.recommend(self.disc, ns(upsell=["16:39.95:50"]))
        plan["packages"].append(dict(next(p for p in plan["packages"] if p["key"] == "hero-23"), key="twin-23"))
        errs = ca.validate_plan(plan)
        self.assertTrue(any("hero-23 and twin-23 both use variant 23" in e for e in errs), errs)
        self.assertEqual(ca.validate_plan(plan, for_create=False), [])

    def test_same_upsell_variant_twice_is_refused(self):
        for kw in (dict(upsell=["23:49.95:50", "23:49.95:50"]),
                   dict(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50", "16:29.95:50"]),
                   dict(hero=10, ctc="high", anchor_price="189.95", bump=["16:9.95"],
                        upsell=["16:9.95:50", "16:9.95:40"])):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.recommend(self.disc, ns(**kw))
            self.assertIn("given twice", str(cm.exception))

    def test_variants_of_one_product_share_one_voucher(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          upsell=["16:39.95:50", "17:39.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        (up,) = self._upsell_offers(plan)
        self.assertEqual((up["key"], up["name"], up["code"], up["condition"]["package_keys"]),
                         ("upsell-15-50", "Music Photo Magnet - 50%", "PHOTOMAGNET50",
                          ["upsell-16", "upsell-17"]))
        rows = [l for l in plan["landed_prices"] if l["kind"] == "upsell"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["tier"] for r in rows}), 2)
        self.assertEqual({r["offer_key"] for r in rows}, {"upsell-15-50"})
        ids = {p["key"]: 300 + i for i, p in enumerate(plan["packages"])}
        cases = [c for c in ca._cart_cases_from_plan(plan, ids) if c[0].startswith("Upsell")]
        self.assertEqual(len(cases), 2)
        self.assertTrue(all(c[2] == ["PHOTOMAGNET50"] for c in cases))

    def test_same_product_two_percentages_two_vouchers(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          upsell=["16:39.95:50", "17:39.95:40"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(sorted(o["code"] for o in self._upsell_offers(plan)),
                         ["PHOTOMAGNET40", "PHOTOMAGNET50"])
        tiers = [l["tier"] for l in plan["landed_prices"] if l["kind"] == "upsell"]
        self.assertEqual(len(set(tiers)), 2, tiers)

    def test_partial_and_split_upsells_are_disclosed(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        self.assertTrue(any("covers 1 of its 2 purchasable variants" in r and "[17]" in r for r in plan["rationale"]))
        split = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                           upsell=["16:39.95:50", "17:39.95:40"]))
        self.assertTrue(any("split across 2 vouchers" in r for r in split["rationale"]))
        whole = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                           upsell=["16:39.95:50", "17:39.95:50"]))
        self.assertFalse(any("vouchers because" in r or "purchasable variants;" in r for r in whole["rationale"]))

    def test_two_products_with_one_title_get_distinct_labels(self):
        d = json.loads(json.dumps(self.disc))
        twin = next(p for p in d["products"] if p["id"] == 18)
        twin["title"] = "Music Photo Magnet"
        plan = ca.recommend(d, ns(hero=10, ctc="high", anchor_price="189.95", exit_code="SAVE10",
                                  upsell=["16:39.95:50", "19:29.95:40"], short_name=["18:BIGMAGNET"]))
        tiers = [l["tier"] for l in plan["landed_prices"] if l["kind"] == "upsell"]
        self.assertEqual(len(set(tiers)), 2, tiers)

    def test_two_products_with_one_title_stop_and_ask(self):
        d = json.loads(json.dumps(self.disc))
        next(p for p in d["products"] if p["id"] == 18)["title"] = "Music Photo Magnet"
        kw = dict(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50", "19:29.95:50"])
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(d, ns(**kw))
        self.assertIn("PHOTOMAGNET", str(cm.exception))
        self.assertIn("--short-name", str(cm.exception))
        plan = ca.recommend(d, ns(short_name=["18:bigmagnet"], **kw))
        self.assertEqual(ca.validate_plan(plan), [])
        ups = self._upsell_offers(plan)
        self.assertEqual(sorted(o["code"] for o in ups), ["BIGMAGNET50", "PHOTOMAGNET50"])
        self.assertEqual(len({o["name"] for o in ups}), 2)

    def test_titles_that_share_a_code_stem_stop_and_ask(self):
        d = json.loads(json.dumps(self.disc))
        next(p for p in d["products"] if p["id"] == 18)["title"] = "Music-Photo Magnet!"
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(d, ns(hero=10, ctc="high", anchor_price="189.95",
                               upsell=["16:39.95:50", "19:29.95:50"]))
        self.assertIn("--short-name", str(cm.exception))

    def test_distinct_stems_that_complete_to_one_code_stop_and_ask(self):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                       upsell=["16:39.95:5", "19:29.95:15"],
                                       short_name=["15:MODEL1", "18:MODEL"]))
        self.assertIn("MODEL15", str(cm.exception))
        self.assertIn("--short-name", str(cm.exception))

    def test_long_titles_shorten_from_the_front(self):
        d = json.loads(json.dumps(self.disc))
        long = "Personalized Music Photo Magnet With Custom Song Lyrics"
        for pid, suffix in ((15, " Small"), (18, " Large")):
            next(p for p in d["products"] if p["id"] == pid)["title"] = long + suffix
        plan = ca.recommend(d, ns(hero=10, ctc="high", anchor_price="189.95",
                                  upsell=["16:39.95:50", "19:29.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(sorted(o["code"] for o in self._upsell_offers(plan)),
                         ["LYRICSLARGE50", "LYRICSSMALL50"])

    def test_generic_upsell_title_without_offers_needs_no_code(self):
        d = json.loads(json.dumps(self.disc))
        d["offers_supported"] = False
        next(p for p in d["products"] if p["id"] == 15)["title"] = "Christmas Ornament"
        plan = ca.recommend(d, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        self.assertEqual(plan["voucher_codes"], [])
        self.assertFalse(any("None" in r for r in plan["rationale"] + plan["handoff"]))

    def test_generic_hero_title_is_fine_when_no_exit_code_is_generated(self):
        def generic_hero(offers=True):
            d = json.loads(json.dumps(self.disc))
            next(p for p in d["products"] if p["id"] == 22)["title"] = "Christmas Ornament"
            if not offers:
                d["offers_supported"] = False
            return d
        self.assertEqual(ca.recommend(generic_hero(), ns(exit="0"))["voucher_codes"], [])
        self.assertEqual(ca.recommend(generic_hero(offers=False), ns())["voucher_codes"], [])
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(generic_hero(), ns())
        self.assertIn("--short-name", str(cm.exception))

    def test_hero_and_upsell_sharing_a_stem_without_exit_do_not_collide(self):
        d = json.loads(json.dumps(self.disc))
        next(p for p in d["products"] if p["id"] == 15)["title"] = "Photo Bracelet Charm Bracelet"
        next(p for p in d["products"] if p["id"] == 22)["title"] = "Bracelet"
        plan = ca.recommend(d, ns(exit="0", upsell=["16:39.95:50"]))
        self.assertEqual([o["code"] for o in self._upsell_offers(plan)], ["BRACELET50"])
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(d, ns(upsell=["16:39.95:50"]))

    def test_explicit_exit_code_on_a_generic_hero(self):
        d = json.loads(json.dumps(self.disc))
        next(p for p in d["products"] if p["id"] == 22)["title"] = "Christmas Ornament"
        plan = ca.recommend(d, ns(exit_code="SAVE10"))
        (v,) = plan["voucher_codes"]
        self.assertEqual((v["offer_key"], v["short_name"], v["generated_code"], v["source"]),
                         ("exit-pop", None, "SAVE10", "exit-code"))
        out = self._preview(plan)
        self.assertIn("SAVE10", out)
        self.assertIn("source exit-code", out)

    def test_short_name_flag_is_validated(self):
        for bad in ("15:TOO-LONG", "15:ABCDEFGHIJKLM", "15:", "x:ABC", "999:ABC", "15", "15:1ALWAYS"):
            with self.assertRaises(ca.CampaignAdminError, msg=bad):
                ca.recommend(self.disc, ns(upsell=["16:39.95:50"], short_name=[bad]))
        with self.assertRaises(ca.CampaignAdminError):
            ca.recommend(self.disc, ns(upsell=["16:39.95:50"], short_name=["15:A", "15:B"]))

    def test_unused_short_name_is_disclosed(self):
        plan = ca.recommend(self.disc, ns(upsell=["16:39.95:50"], short_name=["18:FAMILY", "15:MAGNET"]))
        notes = [r for r in plan["rationale"] if "was not used" in r]
        self.assertEqual(len(notes), 1, notes)
        self.assertIn("18:FAMILY", notes[0])
        # An explicit --exit-code replaces the hero's generated code, so the hero's
        # short name contributes to no code and is reported as unused.
        plan = ca.recommend(self.disc, ns(short_name=["22:BAND"], exit_code="SAVE10"))
        self.assertEqual([o["code"] for o in plan["offers"] if o["offer_type"] == "voucher"], ["SAVE10"])
        self.assertTrue(any("22:BAND was not used" in r for r in plan["rationale"]), plan["rationale"])

    def _preview(self, plan):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "plan.json"
            path.write_text(json.dumps(plan))
            reloaded = json.loads(path.read_text())
            out, old = [], ca.print
            ca.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
            try:
                ca.print_plan(reloaded, path)
            finally:
                ca.print = old
            return "\n".join(out)

    def test_preview_lists_voucher_codes_after_a_round_trip(self):
        plan = ca.recommend(self.disc, ns(upsell=["16:39.95:50"], short_name=["22:BAND"]))
        out = self._preview(plan)
        self.assertIn("Voucher codes (override with --short-name", out)
        self.assertRegex(out, r"upsell-15-50\s+PHOTOMAGNET50  Music Photo Magnet \(15\)  short name PHOTOMAGNET  source generated")
        self.assertRegex(out, r"exit-pop\s+BAND10  Photo Bracelet \(22\)  short name BAND  source short-name")

    def test_preview_shows_the_code_that_will_be_sent(self):
        plan = json.loads(json.dumps(ca.recommend(self.disc, ns(upsell=["16:39.95:50"]))))
        up = next(o for o in plan["offers"] if o["key"] == "upsell-15-50")
        up["code"] = "MAGNET50"
        out = self._preview(plan)
        self.assertIn("MAGNET50", out)
        self.assertIn("source edited in plan", out)
        self.assertEqual(ca.offer_body(up, {k: 1 for k in up["condition"]["package_keys"]})["code"], "MAGNET50")
        del plan["voucher_codes"]
        self.assertIn("source unknown", self._preview(plan))
        for junk in (1, "x", {"a": 1}):
            plan["voucher_codes"] = junk
            self.assertIn("source unknown", self._preview(plan))

    def test_bump_and_upsell_flag_order_does_not_matter(self):
        # bumps and upsells are separate argparse lists; recommend builds every bump
        # first, so "--upsell ... --bump ..." still reuses the bump package.
        with mock.patch.object(ca, "_dispatch", lambda a: setattr(self, "_ns", a) or 0):
            ca.main(["recommend", "--discovery", "x", "--hero", "22", "--ctc", "low",
                     "--anchor-price", "49.95", "--shipping", "standard:6.95",
                     "--upsell", "7:9.95:50", "--bump", "7:9.95"])
        plan = ca.recommend(self.disc, self._ns)
        self.assertEqual([p["key"] for p in plan["packages"]].count("upsell-7"), 0)
        (up,) = self._upsell_offers(plan)
        self.assertEqual(up["condition"]["package_keys"], ["bump-7"])

    def test_non_finite_upsell_percentage_is_a_clean_error(self):
        for bad in ("nan", "inf", "-inf", "NaN"):
            with self.assertRaises(ca.CampaignAdminError, msg=bad):
                ca.recommend(self.disc, ns(upsell=[f"23:49.95:{bad}"]))

    def test_code_stem_examples(self):
        for title, stem in (("Always Near Ornament", "ALWAYSNEAR"),
                            ("Our Family Christmas Ornament", "OURFAMILY"),
                            ("Snapshot Ornament", "SNAPSHOT"),
                            ("American Legacy Coin Ornament", "LEGACYCOIN"),
                            ("2025 Always Near Ornament", "ALWAYSNEAR"),
                            ("#12 Snapshot Ornament", "SNAPSHOT"),
                            ("Snapshot Ornament 2025", "SNAPSHOT"),
                            ("3D Photo Crystal", "PHOTOCRYSTAL"),
                            ("Music Photo Magnet", "PHOTOMAGNET"),
                            ("Supercalifragilistic Ornament", "SUPERCALIFRA")):
            self.assertEqual(ca.code_stem(title), stem, title)
        for generic in ("Christmas Ornament", "2025 Christmas Calendar", "Christmas Ornament 2025"):
            with self.assertRaises(ca.CampaignAdminError, msg=generic):
                ca.code_stem(generic)

    def test_code_from_rounds_the_percentage_down(self):
        self.assertEqual(ca.code_from("ALWAYSNEAR", "57.5"), "ALWAYSNEAR57")
        self.assertEqual(ca.code_from("ALWAYSNEAR", 10), "ALWAYSNEAR10")

    def test_exit_code_collision_needs_exit_code(self):
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(upsell=["23:49.95:10"]))
        self.assertIn("--exit-code", str(cm.exception))
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:10"], exit_code="SAVE10"))
        self.assertEqual(ca.validate_plan(plan), [])


class UpsellVoucherVerify(unittest.TestCase):
    """Verify-level proof for the upsell changes, on FreeShippingPerCase's fake
    store and cart engine."""
    _engine = staticmethod(FreeShippingPerCase._engine)
    _run = FreeShippingPerCase._run
    _cases = staticmethod(FreeShippingPerCase._cases)
    _checks = staticmethod(FreeShippingPerCase._checks)

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_hero_reuse_verifies_in_upsell_mode(self):
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:50"]))
        report, tt = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        case = self._cases(report)["Upsell Photo Bracelet voucher"]
        self.assertEqual(case["got_total"], "24.97")
        self.assertTrue(any(p.endswith("?upsell=true") and b.get("vouchers") == ["BRACELET50"]
                            for _, p, b, _ in tt.calls))

    def test_hero_reuse_voucher_stacks_at_checkout(self):
        """The disclosed cost of reuse: entered at checkout the upsell code stacks on
        the Buy 1 tier (50% then 50%). The handoff and the doctrine say so."""
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:50"]))
        ids = {p["key"]: 400 + i for i, p in enumerate(plan["packages"])}
        calc = self._engine(plan, ids)
        _, upsell_page = calc("/api/v1/carts/calculate/?upsell=true",
                              {"lines": [{"package_id": ids["hero-23"], "quantity": 1}], "vouchers": ["BRACELET50"]})
        _, checkout = calc("/api/v1/carts/calculate/",
                           {"lines": [{"package_id": ids["hero-23"], "quantity": 1}], "vouchers": ["BRACELET50"]})
        self.assertEqual((upsell_page["total"], checkout["total"]), ("24.97", "12.48"))

    def test_bump_reuse_voucher_verifies_in_upsell_mode(self):
        """The upsell page adds a package by id, whichever page also sells it, so a
        voucher scoped to a reused bump package prices the upsell cart."""
        plan = ca.recommend(self.disc, ns(bump=["7:9.95"], upsell=["7:9.95:50"]))
        report, _ = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertEqual(self._cases(report)["Upsell Memorial Ornament voucher"]["got_total"], "4.97")

    def test_grouped_variants_each_verified(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          upsell=["16:39.95:50", "17:39.95:40"]))
        report, _ = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        upsell_cases = [k for k in self._cases(report) if k.startswith("Upsell")]
        self.assertEqual(len(upsell_cases), 2, upsell_cases)
        plan2 = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                           upsell=["16:39.95:50", "17:39.95:50"]))
        report2, _ = self._run(plan2)
        self.assertEqual(report2["result"], "PASS", json.dumps(report2, indent=1))
        self.assertEqual(len([k for k in self._cases(report2) if k.startswith("Upsell")]), 2)


class SchemaAdditions(unittest.TestCase):
    """The two additive fields: an offer's `available` and the plan's `origin`."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns())

    def _errs(self, mutate):
        plan = json.loads(json.dumps(self.plan))
        mutate(plan)
        return ca.validate_plan(plan)

    def test_available_accepted_as_a_bool_only(self):
        for value in (True, False):
            def mutate(p, v=value): p["offers"][0]["available"] = v
            self.assertEqual(self._errs(mutate), [], repr(value))
        for bad in ("yes", 1, 0, None, []):
            def mutate(p, v=bad): p["offers"][0]["available"] = v
            errs = self._errs(mutate)
            self.assertTrue(any("available must be true or false" in e for e in errs), repr(bad))

    def test_origin_accepted_for_created_and_adopted_only(self):
        for value in ("created", "adopted"):
            def mutate(p, v=value): p["origin"] = v
            self.assertEqual(self._errs(mutate), [], value)
        for bad in ("stolen", "", None, 1):
            def mutate(p, v=bad): p["origin"] = v
            errs = self._errs(mutate)
            self.assertTrue(any("plan origin" in e and "invalid" in e for e in errs), repr(bad))

    def test_a_plan_without_origin_is_still_valid(self):
        self.assertNotIn("origin", self.plan)
        self.assertEqual(ca.validate_plan(self.plan), [])

    def test_offer_body_sends_available_only_when_the_plan_carries_it(self):
        o = self.plan["offers"][0]
        ids = {k: 500 for k in o["condition"]["package_keys"]}
        self.assertNotIn("available", ca.offer_body(o, ids))
        o["available"] = False
        self.assertIs(ca.offer_body(o, ids)["available"], False)
        o["available"] = True
        self.assertIs(ca.offer_body(o, ids)["available"], True)

    def test_a_fresh_apply_refuses_a_plan_that_pauses_an_offer(self):
        """A create always lands live, so `available: false` can only come from a
        pause after the fact. A fresh run says so and sends nothing rather than
        leave the operator guessing whether the offer fires."""
        plan = ca.recommend(self.disc, ns(free_shipping=True))
        fs = next(o for o in plan["offers"] if o["key"] == "free-shipping")
        fs["available"] = False
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "campaign-plan.json"
            ca.atomic_write_json(pp, plan)
            t = DynamicTransport(self.disc, fresh_state())
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.apply(make_client(t), plan, ca.sha256_file(pp), Path(d) / "run-manifest.json", None)
            self.assertIn("creates every offer live", str(cm.exception))
            self.assertEqual([c for c in t.calls if c[0] != "GET"], [])


class AvailableFilter(unittest.TestCase):
    """An offer switched off fires on no cart, so every pricing gate skips it while
    the read-back still owns it."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_free_shipping_offer_switched_off_is_not_a_free_shipping_offer(self):
        plan = ca.recommend(self.disc, ns(free_shipping=True))
        fs = next(o for o in plan["offers"] if o["key"] == "free-shipping")
        self.assertEqual(len(ca._free_shipping_offers(plan)), 1)
        fs["available"] = False
        self.assertEqual(ca._free_shipping_offers(plan), [])
        ids = {p["key"]: 300 + i for i, p in enumerate(plan["packages"])}
        self.assertTrue(all(c.ship == "paid" for c in ca._cart_cases_from_plan(plan, ids)
                            if c.ship != "none"))

    def test_partial_shipping_offer_switched_off_is_not_flagged(self):
        plan = ca.recommend(self.disc, ns())
        plan["offers"].append({
            "key": "half-ship", "name": "Half off shipping", "offer_type": "offer",
            "condition": {"type": "any", "package_keys": list(HERO)},
            "benefit": {"type": "shipping_percentage", "value": "50.00", "price_rounding": None}})
        self.assertEqual(len(ca._partial_shipping_offers(plan)), 1)
        plan["offers"][-1]["available"] = False
        self.assertEqual(ca._partial_shipping_offers(plan), [])

    def test_exit_voucher_switched_off_builds_no_cart_case(self):
        plan = ca.recommend(self.disc, ns(exit_code="BRACELET10"))
        ids = {p["key"]: 300 + i for i, p in enumerate(plan["packages"])}
        self.assertTrue(any("exit voucher" in c.name for c in ca._cart_cases_from_plan(plan, ids)))
        next(o for o in plan["offers"] if o["key"] == "exit-pop")["available"] = False
        self.assertFalse(any("exit voucher" in c.name for c in ca._cart_cases_from_plan(plan, ids)))


class ApplyRecordsImageSrc(unittest.TestCase):
    """What apply writes into the journal for a later diff to compare against."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns())
        self.key = self.plan["packages"][0]["key"]
        self.plan["packages"][0]["image"] = {"src": "https://cdn.example/hero.png",
                                             "file_name": "hero.png"}
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.tmp.name) / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.state = fresh_state()
        self.t = DynamicTransport(self.disc, self.state)
        self.man = ca.apply(make_client(self.t), self.plan, self.sha,
                            Path(self.tmp.name) / "run-manifest.json", None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_image_src_recorded_beside_the_thumbnail_receipt(self):
        e = next(x for x in self.man.data["packages"] if x["key"] == self.key)
        self.assertEqual(e["image_status"], "set")
        self.assertEqual(e["image_src"], "https://cdn.example/hero.png")
        self.assertEqual(e["image"], OVERRIDE_IMAGE)  # the server-built thumbnail, not the src
        other = next(x for x in self.man.data["packages"] if x["key"] != self.key)
        self.assertNotIn("image_src", other)

    def test_shipping_entry_records_its_store_code(self):
        e = self.man.data["shipping_methods"][0]
        self.assertEqual((e["key"], e["shipping_method"]), ("standard", "standard"))

    def test_a_new_manifest_records_a_created_origin(self):
        self.assertEqual(self.man.data["origin"], "created")
        self.assertEqual(ca.manifest_origin(self.man), "created")

    def test_a_manifest_without_origin_reads_as_created(self):
        man = ca.Manifest(self.man.path, load_fixture("manifest-0.7.5.json"))
        self.assertNotIn("origin", man.data)
        self.assertEqual(ca.manifest_origin(man), "created")

    def test_reconcile_records_the_code_on_a_claimed_shipping_entry(self):
        self.man.mark("shipping_methods", "standard", status="pending",
                      shipping_method=None, id=None)
        ca.reconcile(make_client(self.t), self.man, self.plan)
        e = self.man.data["shipping_methods"][0]
        self.assertEqual((e["status"], e["shipping_method"]), ("created", "standard"))


class TeardownRefusesAdopted(unittest.TestCase):
    """An adopted campaign was not built by a run of this skill, so neither
    teardown nor a resume may treat it as this run's to delete or finish."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.plan = ca.recommend(self.disc, ns())
        self.tmp = tempfile.TemporaryDirectory()
        self.plan_path = Path(self.tmp.name) / "campaign-plan.json"
        ca.atomic_write_json(self.plan_path, self.plan)
        self.sha = ca.sha256_file(self.plan_path)
        self.manifest = Path(self.tmp.name) / "run-manifest.json"
        self.state = fresh_state()
        self.t = DynamicTransport(self.disc, self.state)
        self.man = ca.apply(make_client(self.t), self.plan, self.sha, self.manifest, None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_teardown_refuses_an_adopted_manifest_before_any_read(self):
        self.man.data["origin"] = "adopted"
        self.man.save()
        before = len(self.t.calls)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        self.assertIn("update --allow-delete", str(cm.exception))
        self.assertEqual(len(self.t.calls), before)

    def test_teardown_still_runs_on_a_created_manifest(self):
        ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        self.assertEqual(self.man.data["campaign"]["status"], "deleted")

    def test_teardown_runs_on_a_manifest_with_no_origin(self):
        del self.man.data["origin"]
        self.man.save()
        ca.teardown(make_client(self.t), self.man, self.plan, self.sha, lambda: True)
        self.assertEqual(self.man.data["campaign"]["status"], "deleted")

    def test_resume_refuses_an_adopted_manifest(self):
        self.man.data["origin"] = "adopted"
        self.man.save()
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(make_client(self.t), self.plan, self.sha, self.manifest, self.manifest)
        self.assertIn("adopted", str(cm.exception))
        self.assertIn("diff and update", str(cm.exception))


class VerifyReadBackAdditions(unittest.TestCase):
    """Verify reads back every field the updater can change, and says INFO or
    UNVERIFIED where it cannot prove one instead of failing the run."""
    _engine = staticmethod(FreeShippingPerCase._engine)
    _run = FreeShippingPerCase._run
    _cases = staticmethod(FreeShippingPerCase._cases)
    _checks = staticmethod(FreeShippingPerCase._checks)

    def setUp(self):
        self.disc = load_fixture("discovery.json")

    def test_a_created_campaign_passes_every_new_row(self):
        plan = ca.recommend(self.disc, ns(name="Bracelet - test", exit_code="BRACELET10"))
        report, _ = self._run(plan)
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        rows = self._checks(report)
        key, okey = plan["packages"][0]["key"], plan["offers"][0]["key"]
        for name in ("campaign.name", "campaign.paypal_account_id", f"package {key} name",
                     f"offer {okey} name", f"offer {okey} available", f"offer {okey} condition.type",
                     f"offer {okey} benefit.type", f"offer {okey} benefit.price_rounding"):
            self.assertEqual(rows[name]["result"], "PASS", name)
        counted = [o["key"] for o in plan["offers"] if o["condition"]["type"] == "count"]
        self.assertTrue(counted)
        for k in counted:
            self.assertEqual(rows[f"offer {k} condition.value"]["result"], "PASS", k)
        self.assertNotIn("pricing coverage unproven: no landed rows", rows)

    def test_a_dashboard_rename_and_a_paypal_id_are_caught(self):
        plan = ca.recommend(self.disc, ns())

        def edit(state, man, cid, t):
            state["campaigns"][cid]["name"] = "Renamed in the dashboard"
            state["campaigns"][cid]["paypal_account_id"] = "PP-99"

        report, _ = self._run(plan, after_apply=edit)
        rows = self._checks(report)
        self.assertEqual(rows["campaign.name"]["result"], "FAIL")
        self.assertEqual(rows["campaign.paypal_account_id"]["result"], "FAIL")
        self.assertEqual(report["result"], "FAIL")

    def test_null_additional_currencies_read_as_empty(self):
        plan = ca.recommend(self.disc, ns())

        def clear(state, man, cid, t):
            state["campaigns"][cid]["additional_currencies"] = None

        report, _ = self._run(plan, after_apply=clear)
        self.assertEqual(self._checks(report)["campaign.additional_currencies"]["result"], "PASS")

    def test_a_package_rename_is_caught_and_the_api_suffix_is_not(self):
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]
        report, _ = self._run(plan)
        self.assertEqual(self._checks(report)[f"package {key} name"]["result"], "PASS")

        def rename(state, man, cid, t):
            pid = next(e["id"] for e in man.data["packages"] if e["key"] == key)
            state["packages"][cid][pid]["name"] = "Something else entirely"

        report, _ = self._run(plan, after_apply=rename)
        row = self._checks(report)[f"package {key} name"]
        self.assertEqual(row["result"], "FAIL")
        self.assertIn("Something else entirely", row["detail"])

    def test_recurring_rows_appear_only_when_the_plan_sets_price_recurring(self):
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]
        self.assertNotIn(f"package {key} price_recurring", self._checks(self._run(plan)[0]))
        plan["packages"][0].update(price_recurring="19.95", interval="month", interval_count=1)
        report, _ = self._run(plan)
        rows = self._checks(report)
        for field in ("price_recurring", "interval", "interval_count"):
            self.assertEqual(rows[f"package {key} {field}"]["result"], "PASS", field)

        def drop(state, man, cid, t):
            pid = next(e["id"] for e in man.data["packages"] if e["key"] == key)
            state["packages"][cid][pid]["interval_count"] = 3

        report, _ = self._run(plan, after_apply=drop)
        self.assertEqual(self._checks(report)[f"package {key} interval_count"]["result"], "FAIL")

    def test_a_recurring_charge_on_a_package_the_plan_prices_once_fails(self):
        """The recurring rows above run only when the plan asked for a subscription.
        Verification has to make the opposite claim too, or a monthly charge nobody
        planned reads back as PASS on a supposedly one-off package."""
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]
        report, _ = self._run(plan)
        self.assertEqual(self._checks(report)[f"package {key} not_recurring"]["result"], "PASS")
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))

        def make_it_recurring(state, man, cid, t):
            pid = next(e["id"] for e in man.data["packages"] if e["key"] == key)
            pkg = state["packages"][cid][pid]
            pkg.update(is_recurring=True, interval="month", interval_count=1)
            pkg["prices"][0]["price_recurring"] = pkg["prices"][0]["price"]

        report, _ = self._run(plan, after_apply=make_it_recurring)
        row = self._checks(report)[f"package {key} not_recurring"]
        self.assertEqual(row["result"], "FAIL", json.dumps(report, indent=1))
        self.assertIn("price_recurring", row["detail"])
        self.assertEqual(report["result"], "FAIL")

    def test_a_recurring_price_alone_fails_even_with_the_flag_unset(self):
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]

        def price_only(state, man, cid, t):
            pid = next(e["id"] for e in man.data["packages"] if e["key"] == key)
            state["packages"][cid][pid]["prices"][0]["price_recurring"] = "9.95"

        report, _ = self._run(plan, after_apply=price_only)
        self.assertEqual(self._checks(report)[f"package {key} not_recurring"]["result"], "FAIL")

    def test_a_store_that_reports_neither_recurring_field_reads_as_one_off(self):
        """Absent is not recurring: a store whose package read-back carries no
        `is_recurring` and no `price_recurring` row passes this check rather than
        failing on a field it never reports."""
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]

        def strip(state, man, cid, t):
            pid = next(e["id"] for e in man.data["packages"] if e["key"] == key)
            pkg = state["packages"][cid][pid]
            pkg.pop("is_recurring", None)
            for row in pkg["prices"]:
                row.pop("price_recurring", None)

        report, _ = self._run(plan, after_apply=strip)
        self.assertEqual(self._checks(report)[f"package {key} not_recurring"]["result"], "PASS")
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))

    def test_a_subscription_package_gets_no_not_recurring_row(self):
        plan = ca.recommend(self.disc, ns())
        key = plan["packages"][0]["key"]
        plan["packages"][0].update(price_recurring="19.95", interval="month", interval_count=1)
        report, _ = self._run(plan)
        self.assertNotIn(f"package {key} not_recurring", self._checks(report))
        self.assertEqual(self._checks(report)[f"package {key} price_recurring"]["result"], "PASS")

    def test_same_percentage_shipping_instead_of_package_discount_is_caught(self):
        """The benefit value row alone passes this: 50% is 50%. Only the type row
        sees that the discount moved from the product to the shipping."""
        plan = ca.recommend(self.disc, ns())
        okey = plan["offers"][0]["key"]

        def swap(state, man, cid, t):
            oid = next(e["id"] for e in man.data["offers"] if e["key"] == okey)
            state["offers"][cid][oid]["benefit"]["type"] = "shipping_percentage"

        report, _ = self._run(plan, after_apply=swap)
        rows = self._checks(report)
        self.assertEqual(rows[f"offer {okey} benefit"]["result"], "PASS")
        self.assertEqual(rows[f"offer {okey} benefit.type"]["result"], "FAIL")
        self.assertEqual(report["result"], "FAIL")

    def test_price_rounding_treats_empty_string_and_null_as_the_same(self):
        plan = ca.recommend(self.disc, ns())
        okey = plan["offers"][0]["key"]

        def blank(state, man, cid, t):
            oid = next(e["id"] for e in man.data["offers"] if e["key"] == okey)
            state["offers"][cid][oid]["benefit"]["price_rounding"] = ""

        report, _ = self._run(plan, after_apply=blank)
        self.assertEqual(self._checks(report)[f"offer {okey} benefit.price_rounding"]["result"], "PASS")

    def test_a_count_threshold_the_store_hides_is_unverified_not_failed(self):
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        report, _ = self._run(plan, before_apply=lambda state: state.update(offer_value_readable=False),
                              free_from=2)
        row = self._checks(report)["offer free-shipping condition.value"]
        self.assertEqual(row["result"], "UNVERIFIED")
        self.assertIn("no threshold", row["detail"])
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))

    def test_a_wrong_count_threshold_fails(self):
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))

        def bump(state, man, cid, t):
            oid = next(e["id"] for e in man.data["offers"] if e["key"] == "free-shipping")
            state["offers"][cid][oid]["condition"]["value"] = "3.00"

        report, _ = self._run(plan, after_apply=bump, free_from=2)
        row = self._checks(report)["offer free-shipping condition.value"]
        self.assertEqual((row["result"], report["result"]), ("FAIL", "FAIL"))

    def test_an_offer_switched_off_in_the_dashboard_is_caught(self):
        plan = ca.recommend(self.disc, ns())
        okey = plan["offers"][0]["key"]

        def off(state, man, cid, t):
            oid = next(e["id"] for e in man.data["offers"] if e["key"] == okey)
            state["offers"][cid][oid]["available"] = False

        report, _ = self._run(plan, after_apply=off)
        self.assertEqual(self._checks(report)[f"offer {okey} available"]["result"], "FAIL")

    def test_the_hero_row_only_appears_when_the_plan_declares_a_hero(self):
        plan = ca.recommend(self.disc, ns())
        for p in plan["packages"]:
            p["role"] = "bump"
        report, _ = self._run(plan)
        self.assertNotIn("hero packages present", self._checks(report))
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))

        plan = ca.recommend(self.disc, ns())

        def forget(state, man, cid, t):
            heroes = {p["key"] for p in plan["packages"] if p["role"] == "hero"}
            man.data["packages"] = [e for e in man.data["packages"] if e["key"] not in heroes]

        report, _ = self._run(plan, after_apply=forget)
        self.assertEqual(self._checks(report)["hero packages present"]["result"], "FAIL")

    def test_no_landed_rows_yields_one_information_row_not_a_coverage_failure(self):
        """An adopted campaign: a count discount on the hero plus free shipping, and
        no landed rows to prove either with. The read-back rows still run."""
        plan = ca.recommend(self.disc, ns(free_shipping_min_qty=2))
        plan["landed_prices"] = []
        report, tt = self._run(plan)
        rows = self._checks(report)
        self.assertEqual(rows["pricing coverage unproven: no landed rows"]["result"], "INFO")
        self.assertFalse([k for k in rows if k.endswith("free-shipping coverage")])
        self.assertEqual(report["calculate_cases"], [])
        self.assertEqual(rows["offer free-shipping condition.value"]["result"], "PASS")
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertFalse(tt.calls)  # no cart was probed

    def test_manifest_entries_that_are_not_in_the_plan_are_fail_rows(self):
        plan = ca.recommend(self.disc, ns())

        def ghosts(state, man, cid, t):
            man.data["packages"].append({"key": "ghost-pkg", "status": "created", "id": 90001})
            man.data["offers"].append({"key": "ghost-offer", "status": "created", "id": 90002})

        report, _ = self._run(plan, after_apply=ghosts)
        rows = self._checks(report)
        self.assertEqual(rows["package ghost-pkg"], {"check": "package ghost-pkg", "result": "FAIL",
                                                     "detail": "manifest entry not in plan"})
        self.assertEqual(rows["offer ghost-offer"]["detail"], "manifest entry not in plan")
        self.assertEqual(report["result"], "FAIL")

    def test_print_verify_prints_the_unproven_rows_and_the_count(self):
        report = {"result": "PASS",
                  "admin_checks": [{"check": "a", "result": "INFO", "detail": "no landed rows"},
                                   {"check": "b", "result": "UNVERIFIED", "detail": "no threshold"},
                                   {"check": "c", "result": "PASS", "detail": ""}],
                  "calculate_cases": []}
        out, old = [], ca.print
        ca.print = lambda *a, **k: out.append(" ".join(str(x) for x in a))
        try:
            ca.print_verify(report)
        finally:
            ca.print = old
        self.assertIn("  INFO  a  no landed rows", out)
        self.assertIn("  UNVERIFIED  b  no threshold", out)
        self.assertEqual(out[-1], "VERIFY: PASS  (2 row(s) not proven: INFO, UNVERIFIED)")


class LegacyManifest(unittest.TestCase):
    """A run directory written by 0.7.5: no `origin`, no `shipping_method` on the
    shipping entry, no `image_src`. Both commands that read a manifest have to work
    on it unchanged, through the CLI."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.tmp = tempfile.TemporaryDirectory()
        d = Path(self.tmp.name)
        self.plan_path = d / "campaign-plan.json"
        self.plan_path.write_bytes((FIXTURES / "plan-0.7.5.json").read_bytes())
        self.plan = json.loads(self.plan_path.read_text())
        man = load_fixture("manifest-0.7.5.json")
        # The fixture carries a placeholder digest: a real one is a 64-character
        # high-entropy token, which the public-safety scanner flags on sight.
        man["plan_sha256"] = ca.sha256_file(self.plan_path)
        self.manifest_path = d / "run-manifest.json"
        ca.atomic_write_json(self.manifest_path, man)
        self.man = man
        self.state = self._live_state()
        self.t = DynamicTransport(self.disc, self.state)

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _live_state():
        """The live campaign that fixture manifest owns."""
        state = fresh_state()
        state["campaigns"][2001] = {
            "id": 2001, "name": "Legacy Bracelet", "currency": "USD", "language": "en",
            "payment_gateway_group_id": 1, "api_key": "test-key",
            "created_at": "2026-09-02T12:00:05+00:00", "statement_descriptor": "LEGACY",
            "paypal_account_id": None, "additional_currencies": [],
            "available_payment_methods": [{"code": "card"}],
            "available_express_payment_methods": [],
            "available_shipping_countries": [{"code": "US"}]}
        state["packages"][2001] = {2002: {
            "id": 2002, "product_id": 22, "product_variant_id": 801,
            "product_variant_name": "One size", "name": "Legacy Bracelet - One size",
            "prices": [{"currency": "USD", "price": "24.95", "price_recurring": None}],
            "is_recurring": False, "interval": "", "interval_count": None,
            "product_purchase_availability": "available", "image": CATALOGUE_IMAGE}}
        state["shipping-methods"][2001] = {2003: {
            "id": 2003, "shipping_method": "standard",
            "prices": [{"currency": "USD", "price": "6.95"}]}}
        state["offers"][2001] = {2004: {
            "id": 2004, "name": "Legacy Bracelet - Buy 2", "offer_type": "offer", "code": None,
            "available": True,
            "condition": offer_condition(state, {"type": "count", "value": 2, "package_ids": [2002]}),
            "benefit": offer_benefit({"type": "package_percentage", "value": "10.00",
                                      "price_rounding": None})}}
        return state

    def _cart(self):
        def calc(path, body):
            # the fixture's one landed row: 24.95 plus the 6.95 method the row carries
            return 200, {"total": "31.90", "lines": []}
        t = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
        return t, ca.Client(ca.CART_API_ORIGIN, "test-key", auth_scheme="raw",
                            send_version_header=False, transport=t, clock=FakeClock(),
                            sleep=lambda s: None)

    def _main(self, argv, cart=None):
        # the admin client is built before ca.Client is replaced: the patch is only
        # there so _dispatch's cart client is the fake one
        admin = make_client(self.t)
        with mock.patch.object(ca, "_client_for", lambda slug: admin):
            if cart is None:
                return ca.main(argv)
            with mock.patch.object(ca, "Client", lambda *a, **k: cart):
                return ca.main(argv)

    def test_verify_reads_the_legacy_run_back(self):
        cart_t, cart = self._cart()
        rc = self._main(["verify", "--manifest", str(self.manifest_path),
                         "--plan", str(self.plan_path)], cart=cart)
        report = json.loads((Path(self.tmp.name) / "verify-report.json").read_text())
        self.assertEqual((rc, report["result"]), (0, "PASS"), json.dumps(report, indent=1))
        rows = {x["check"]: x for x in report["admin_checks"]}
        # the shipping code came from the plan: the entry does not carry one
        self.assertNotIn("shipping_method", self.man["shipping_methods"][0])
        self.assertEqual(rows["shipping standard code"]["result"], "PASS")
        self.assertEqual(rows["package hero-801 name"]["result"], "PASS")
        self.assertEqual(rows["offer tier-2 condition.value"]["result"], "PASS")
        self.assertEqual([c["case"] for c in report["calculate_cases"]], ["Buy 1 single variant"])
        self.assertEqual(cart_t.calls[0][2]["shipping_method"], 2003)

    def test_diff_of_the_unchanged_legacy_plan_is_no_changes(self):
        nxt = Path(self.tmp.name) / "campaign-plan.next.json"
        nxt.write_bytes(self.plan_path.read_bytes())
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            rc = self._main(["diff", "--plan", str(nxt), "--manifest", str(self.manifest_path)])
        self.assertEqual((rc, "no changes" in "\n".join(lines)), (0, True), "\n".join(lines))
        self.assertEqual([(c[0], c[1]) for c in self.t.calls if c[0] != "GET"], [])
        # the shipping entry carries no code, so identity took it from the base plan
        self.assertNotIn("shipping_method", self.man["shipping_methods"][0])
        self.assertFalse((Path(self.tmp.name) / "change-set.json").exists())

    def test_diff_of_an_edited_legacy_plan_patches_the_package_price(self):
        cand = json.loads(self.plan_path.read_text())
        cand["packages"][0]["price"] = "27.95"
        nxt = Path(self.tmp.name) / "campaign-plan.next.json"
        nxt.write_text(json.dumps(cand, indent=2) + "\n")
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            rc = self._main(["diff", "--plan", str(nxt), "--manifest", str(self.manifest_path)])
        self.assertEqual(rc, 0, "\n".join(lines))
        cs = json.loads((Path(self.tmp.name) / "change-set.json").read_text())
        cid = self.man["campaign"]["id"]
        pid = self.man["packages"][0]["id"]
        self.assertEqual([(o["method"], o["section"], o["key"], o["id"], o["path"])
                          for o in cs["ops"]],
                         [("PATCH", "packages", "hero-801", pid,
                           f"/api/admin/campaigns/{cid}/packages/{pid}/")])
        self.assertEqual(cs["ops"][0]["body"],
                         {"prices": [{"currency": "USD", "price": "27.95",
                                      "price_recurring": None}]})
        self.assertEqual(cs["preserved"], [])
        self.assertEqual([(c[0], c[1]) for c in self.t.calls if c[0] != "GET"], [])

    def test_update_of_the_edited_legacy_plan_backfills_the_shipping_code(self):
        d = Path(self.tmp.name)
        cand = json.loads(self.plan_path.read_text())
        cand["packages"][0]["price"] = "27.95"
        nxt = d / "campaign-plan.next.json"
        nxt.write_text(json.dumps(cand, indent=2) + "\n")
        lines = []
        with mock.patch.object(ca, "print", lambda *a, **k: lines.append(" ".join(map(str, a)))):
            self.assertEqual(self._main(["diff", "--plan", str(nxt),
                                         "--manifest", str(self.manifest_path)]), 0,
                             "\n".join(lines))
            cs = json.loads((d / "change-set.json").read_text())
            rc = self._main(["update", "--plan", str(d / cs["merged_plan_file"]),
                             "--manifest", str(self.manifest_path),
                             "--change-set", str(d / "change-set.json"), "--yes",
                             "--change-set-sha256", ca.sha256_file(d / "change-set.json")])
        self.assertEqual(rc, 0, "\n".join(lines))
        cid, pid = self.man["campaign"]["id"], self.man["packages"][0]["id"]
        self.assertEqual([(c[0], c[1]) for c in self.t.calls if c[0] != "GET"],
                         [("PATCH", f"/api/admin/campaigns/{cid}/packages/{pid}/")])
        man = json.loads(self.manifest_path.read_text())
        # the entry 0.7.5 wrote carried no store code; a completed update backfills it
        self.assertEqual(man["shipping_methods"][0]["shipping_method"], "standard")
        self.assertNotIn("price", man["shipping_methods"][0])  # no field the run never had
        self.assertEqual(man["plan_sha256"], cs["merged_plan_sha256"])
        self.assertEqual(ca.sha256_file(self.plan_path), cs["merged_plan_sha256"])
        self.assertEqual(json.loads(self.plan_path.read_text())["packages"][0]["price"], "27.95")
        self.assertNotIn("origin", man)  # an update adds no field the run did not have
        self.assertNotIn("active_update", man)

    def test_teardown_deletes_the_legacy_run(self):
        rc = self._main(["teardown", "--manifest", str(self.manifest_path),
                         "--plan", str(self.plan_path), "--yes"])
        self.assertEqual(rc, 0)
        deletes = [c[1] for c in self.t.calls if c[0] == "DELETE"]
        cid = self.man["campaign"]["id"]
        oid, sid, pid = (self.man[s][0]["id"] for s in ("offers", "shipping_methods", "packages"))
        self.assertEqual(deletes, [f"/api/admin/campaigns/{cid}/offers/{oid}/",
                                   f"/api/admin/campaigns/{cid}/shipping-methods/{sid}/",
                                   f"/api/admin/campaigns/{cid}/packages/{pid}/",
                                   f"/api/admin/campaigns/{cid}/"])
        man = json.loads(self.manifest_path.read_text())
        self.assertEqual(man["campaign"]["status"], "deleted")
        self.assertNotIn("origin", man)  # teardown adds no field the run did not have

def seed_roles_campaign(state):
    """A live campaign with one package of each role `derive_role` can reach: a count
    package discount (hero), a 100% package offer (gift), a voucher and nothing else
    (upsell), and a package no offer touches (bump). Returns (campaign id, {role: id})."""
    state["seq"] += 1
    cid = state["seq"]
    state["campaigns"][cid] = {
        "id": cid, "name": "Roles", "currency": "USD", "language": "en",
        "payment_gateway_group_id": 1, "api_key": "KEY-" + "y" * 20 + "0001",
        "created_at": "2026-08-02T09:00:00+00:00", "statement_descriptor": "",
        "paypal_account_id": None, "additional_currencies": [],
        "available_payment_methods": [{"code": "card"}],
        "available_express_payment_methods": [],
        "available_shipping_countries": [{"code": "US"}]}
    pkgs = {}
    for role, vid, price in (("hero", 801, "24.95"), ("gift", 802, "9.95"),
                             ("upsell", 803, "19.95"), ("bump", 804, "4.95")):
        state["seq"] += 1
        pkgs[role] = state["seq"]
        state["packages"].setdefault(cid, {})[state["seq"]] = {
            "id": state["seq"], "product_id": 22, "product_variant_id": vid,
            "product_variant_name": "v" + str(vid), "name": f"{role.title()} item - v{vid}",
            "prices": [{"currency": "USD", "price": price, "price_recurring": None}],
            "is_recurring": False, "interval": "", "interval_count": None,
            "product_purchase_availability": "available", "image": CATALOGUE_IMAGE}
    state["seq"] += 1
    state["shipping-methods"].setdefault(cid, {})[state["seq"]] = {
        "id": state["seq"], "shipping_method": "standard",
        "prices": [{"currency": "USD", "price": "6.95"}]}
    for name, ot, code, cond, ben in (
            ("Roles - Buy 2", "offer", None,
             {"type": "count", "value": 2, "package_ids": [pkgs["hero"]]},
             {"type": "package_percentage", "value": "55.00", "price_rounding": None}),
            ("Roles - Gift free", "offer", None,
             {"type": "any", "package_ids": [pkgs["gift"]]},
             {"type": "package_percentage", "value": "100.00", "price_rounding": None}),
            ("Roles - Upsell", "voucher", "SAVE30",
             {"type": "any", "package_ids": [pkgs["upsell"]]},
             {"type": "package_percentage", "value": "30.00", "price_rounding": None})):
        state["seq"] += 1
        state["offers"].setdefault(cid, {})[state["seq"]] = {
            "id": state["seq"], "name": name, "offer_type": ot, "code": code, "available": True,
            "condition": offer_condition(state, cond), "benefit": offer_benefit(ben)}
    return cid, pkgs


class AdoptFromLive(unittest.TestCase):
    """`adopt` writes a plan whose desired state is exactly what is live, plus a
    manifest that says this run owns it. Anything the engine would have to guess at
    is a blocker instead, and a blocked campaign gets a plan and no manifest."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.state = fresh_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "run"
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        self.t = DynamicTransport(self.disc, self.state)
        admin = make_client(self.t)
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            return ca.main(argv)

    def adopt(self, cid, *extra, out=None):
        return self._run(["adopt", "--store", "teststore", "--campaign", str(cid),
                          "--out", str(out or self.dir), *extra])

    def out(self):
        return "\n".join(self.lines)

    def plan(self):
        return json.loads((self.dir / "campaign-plan.json").read_text())

    def manifest(self):
        return json.loads((self.dir / "run-manifest.json").read_text())

    def test_desired_state_equals_live(self):
        cid = seed_live_campaign(self.state)
        self.assertEqual(self.adopt(cid), 0, self.out())
        plan = self.plan()
        self.assertEqual(plan["origin"], "adopted")
        self.assertEqual((plan["adopted_from_campaign_id"], plan["store_slug"]), (cid, "teststore"))
        self.assertEqual(plan["campaign"], {
            "name": "Dashboard Bracelet", "currency": "USD", "language": "en",
            "payment_gateway_group_id": 1, "additional_currencies": [],
            "available_payment_methods": ["card"], "available_express_payment_methods": [],
            "available_shipping_countries": ["US"], "statement_descriptor": None,
            "paypal_account_id": None})
        self.assertEqual([(p["key"], p["name"], p["price"], p["product_variant_ids"])
                          for p in plan["packages"]],
                         [("pkg-801", "Photo Bracelet", "24.95", [801]),
                          ("pkg-802", "Charm Add-on", "12.95", [802])])
        self.assertEqual(plan["shipping_methods"], [{"shipping_method": "standard", "price": "6.95"}])
        oids = sorted(self.state["offers"][cid])
        self.assertEqual([o["key"] for o in plan["offers"]], [f"offer-{i}" for i in oids])
        count = plan["offers"][0]
        self.assertEqual(count["condition"], {"type": "count", "value": 2,
                                              "package_keys": ["pkg-801"]})
        self.assertEqual(count["benefit"], {"type": "package_percentage", "value": "55.00",
                                            "price_rounding": None})
        self.assertTrue(count["available"])
        self.assertIsNone(plan["offers"][1]["condition"]["value"])
        # no landed-price synthesis, no image override, nothing invented
        self.assertEqual((plan["landed_prices"], plan["rationale"], plan["voucher_codes"],
                          plan["handoff"], plan["blockers"], plan["waivers"]),
                         ([], [], [], [], [], []))
        self.assertTrue(all("image" not in p for p in plan["packages"]))
        self.assertEqual([c[0] for c in self.t.calls if c[0] != "GET"], [])
        self.assertNotIn("KEY-", self.out())

    def test_verify_passes_on_an_adopted_campaign(self):
        cid = seed_live_campaign(self.state)  # count hero discount plus free shipping
        self.assertEqual(self.adopt(cid), 0, self.out())

        def calc(path, body):
            return 200, {"total": "0.00", "lines": []}
        cart_t = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
        cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=cart_t, clock=FakeClock(), sleep=lambda s: None)
        admin = make_client(DynamicTransport(self.disc, self.state))
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "Client", lambda *a, **k: cart):
            rc = ca.main(["verify", "--manifest", str(self.dir / "run-manifest.json"),
                          "--plan", str(self.dir / "campaign-plan.json")])
        report = json.loads((self.dir / "verify-report.json").read_text())
        self.assertEqual((rc, report["result"]), (0, "PASS"), json.dumps(report, indent=1))
        rows = {x["check"]: x for x in report["admin_checks"]}
        # the free-shipping coverage gate is replaced by one information row, and the
        # offer read-back rows still run
        self.assertEqual(rows["pricing coverage unproven: no landed rows"]["result"], "INFO")
        self.assertNotIn("offer offer-%d free-shipping coverage" % sorted(self.state["offers"][cid])[1],
                         rows)
        oid = sorted(self.state["offers"][cid])[0]
        self.assertEqual(rows[f"offer offer-{oid} condition.value"]["result"], "PASS")
        self.assertEqual(cart_t.calls, [])  # no landed rows means no cart probes

    def test_all_four_roles_are_derived_and_printed(self):
        cid, pkgs = seed_roles_campaign(self.state)
        self.assertEqual(self.adopt(cid), 0, self.out())
        plan = self.plan()
        self.assertEqual({p["key"]: p["role"] for p in plan["packages"]},
                         {"pkg-801": "hero", "pkg-802": "gift", "pkg-803": "upsell",
                          "pkg-804": "bump"})
        self.assertIn("pkg-803", self.out())
        self.assertIn("upsell", self.out())

    def assertBlocked(self, cid, *extra, needle):
        rc = self.adopt(cid, *extra)
        self.assertEqual(rc, 1, self.out())
        self.assertFalse((self.dir / "run-manifest.json").exists())
        plan = self.plan()
        self.assertTrue(plan["blockers"], plan)
        self.assertIn(needle, " ".join(plan["blockers"]))
        self.assertIn(f"adopt --store teststore --campaign {cid}", self.out())
        return plan

    def test_two_packages_on_one_variant_block(self):
        cid = seed_live_campaign(self.state)
        first = self.state["packages"][cid][sorted(self.state["packages"][cid])[0]]
        self.state["seq"] += 1
        twin = dict(first, id=self.state["seq"], name="Photo Bracelet copy - v801")
        self.state["packages"][cid][twin["id"]] = twin
        self.assertBlocked(cid, needle="are both on product variant 801")

    def test_two_shipping_methods_on_one_code_block(self):
        cid = seed_live_campaign(self.state)
        self.state["seq"] += 1
        self.state["shipping-methods"][cid][self.state["seq"]] = {
            "id": self.state["seq"], "shipping_method": "standard",
            "prices": [{"currency": "USD", "price": "9.95"}]}
        self.assertBlocked(cid, needle="are both on store code 'standard'")

    def test_duplicate_offer_names_block(self):
        cid = seed_live_campaign(self.state)
        oids = sorted(self.state["offers"][cid])
        self.state["offers"][cid][oids[1]]["name"] = self.state["offers"][cid][oids[0]]["name"]
        self.assertBlocked(cid, needle="share the name")

    def test_a_count_offer_without_a_whole_threshold_blocks(self):
        cid = seed_live_campaign(self.state, value_readable=False)
        self.assertBlocked(cid, needle="count offer whose threshold reads back as None")
        # and a fractional one is refused the same way, never rounded
        self.state["offers"][cid][sorted(self.state["offers"][cid])[0]]["condition"]["value"] = "2.50"
        (self.dir / "run-manifest.json").unlink(missing_ok=True)
        self.assertBlocked(cid, needle="reads back as '2.50'")

    def test_an_all_packages_offer_blocks_and_names_the_convert_flag(self):
        cid = seed_live_campaign(self.state, all_packages=True)
        oid = sorted(self.state["offers"][cid])[0]
        plan = self.assertBlocked(cid, needle="is scoped to all_packages")
        self.assertIn(f"--convert-scope {oid}", self.out())
        # the plan still shows what the conversion would be, for review
        self.assertEqual(plan["offers"][0]["condition"]["package_keys"], ["pkg-801", "pkg-802"])

    def test_a_live_campaign_the_validator_rejects_blocks_with_its_message(self):
        cases = (
            ("quantity-style name", lambda state, cid: state["packages"][cid].__setitem__(
                sorted(state["packages"][cid])[0],
                dict(state["packages"][cid][sorted(state["packages"][cid])[0]],
                     name="2 x Photo Bracelet - v801"))),
            ("at least one shipping method is required",
             lambda state, cid: state["shipping-methods"].__setitem__(cid, {})),
            ("benefit.type", lambda state, cid: state["offers"][cid][
                sorted(state["offers"][cid])[0]]["benefit"].__setitem__("type", "order_amount")),
        )
        for i, (needle, break_it) in enumerate(cases):
            with self.subTest(needle=needle):
                self.state = fresh_state()
                self.dir = Path(self.tmp.name) / f"run{i}"
                cid = seed_live_campaign(self.state)
                break_it(self.state, cid)
                self.assertBlocked(cid, needle=needle)

    def test_convert_scope_records_the_waiver_and_the_pending_flag(self):
        cid = seed_live_campaign(self.state, all_packages=True)
        oid = sorted(self.state["offers"][cid])[0]
        self.assertEqual(self.adopt(cid, "--convert-scope", str(oid)), 0, self.out())
        plan = self.plan()
        self.assertEqual(plan["blockers"], [])
        self.assertEqual(len(plan["waivers"]), 1)
        self.assertIn(f"--convert-scope {oid}", plan["waivers"][0])
        self.assertEqual(plan["offers"][0]["condition"]["package_keys"], ["pkg-801", "pkg-802"])
        self.assertNotIn("converted_scopes", plan)
        entry = next(e for e in self.manifest()["offers"] if e["id"] == oid)
        self.assertTrue(entry["scope_conversion_pending"])
        other = next(e for e in self.manifest()["offers"] if e["id"] != oid)
        self.assertNotIn("scope_conversion_pending", other)

    def test_recurring_fields_survive(self):
        cid = seed_live_campaign(self.state, recurring=True)
        self.assertEqual(self.adopt(cid), 0, self.out())
        hero, bump = self.plan()["packages"]
        self.assertEqual((hero["price_recurring"], hero["interval"], hero["interval_count"]),
                         ("24.95", "month", 1))
        for f in ("price_recurring", "interval", "interval_count"):
            self.assertNotIn(f, bump)

    def test_the_manifest_records_identity_and_an_adopted_origin(self):
        cid = seed_live_campaign(self.state)
        self.assertEqual(self.adopt(cid), 0, self.out())
        man = self.manifest()
        self.assertEqual(man["origin"], "adopted")
        self.assertEqual(man["adopted_from_campaign_id"], cid)
        self.assertTrue(man["adopted_at"])
        self.assertEqual(man["plan_sha256"], ca.sha256_file(self.dir / "campaign-plan.json"))
        self.assertEqual(man["campaign"]["status"], "created")
        self.assertEqual(man["campaign"]["api_key"], self.state["campaigns"][cid]["api_key"])
        # the retrieve echoes the same moment in another offset; the manifest keeps
        # what it was told and later checks compare instants
        self.assertTrue(ca.same_instant(man["campaign"]["created_at"],
                                        self.state["campaigns"][cid]["created_at"]))
        pids = sorted(self.state["packages"][cid])
        self.assertEqual([(e["key"], e["status"], e["id"], e["name"], e["product_variant_id"])
                          for e in man["packages"]],
                         [("pkg-801", "created", pids[0], "Photo Bracelet - v801", 801),
                          ("pkg-802", "created", pids[1], "Charm Add-on - v802", 802)])
        sid = sorted(self.state["shipping-methods"][cid])[0]
        self.assertEqual(man["shipping_methods"],
                         [{"key": "standard", "status": "created", "id": sid,
                           "shipping_method": "standard", "price": "6.95", "intent": None}])
        self.assertEqual([(e["key"], e["id"], e["name"], e["code"]) for e in man["offers"]],
                         [(f"offer-{i}", i, self.state["offers"][cid][i]["name"], None)
                          for i in sorted(self.state["offers"][cid])])
        self.assertEqual(oct((self.dir / "run-manifest.json").stat().st_mode & 0o777), "0o600")
        self.assertEqual(ca.manifest_origin(ca.Manifest.load(self.dir / "run-manifest.json")),
                         "adopted")

    def test_refuses_an_out_dir_that_already_holds_a_manifest(self):
        cid = seed_live_campaign(self.state)
        self.assertEqual(self.adopt(cid), 0, self.out())
        before = (self.dir / "run-manifest.json").read_bytes()
        rc = self.adopt(cid)
        self.assertEqual(rc, 1)
        self.assertIn("already exists", self.out())
        self.assertEqual((self.dir / "run-manifest.json").read_bytes(), before)
        self.assertEqual(self.t.calls, [])  # refused before the first read

    def test_the_variant_suffix_is_stripped_and_the_plan_validates(self):
        cid = seed_live_campaign(self.state)
        self.assertEqual(self.adopt(cid), 0, self.out())
        plan = self.plan()
        self.assertEqual(ca.validate_plan(plan), [])
        hero = plan["packages"][0]
        self.assertEqual((hero["name"], hero["variant_title"]), ("Photo Bracelet", "v801"))
        # and the stripped name still reads back as a match against the live one
        self.assertTrue(ca._live_package_name_ok("Photo Bracelet - v801", hero))
        # a store that leaves the name alone needs no stripping
        self.state["patch_appends_suffix"] = False
        self.state["packages"][cid][sorted(self.state["packages"][cid])[0]]["name"] = "Bare Name"
        self.dir = Path(self.tmp.name) / "run2"
        self.assertEqual(self.adopt(cid), 0, self.out())
        self.assertEqual(self.plan()["packages"][0]["name"], "Bare Name")


CHANGE_SET = "change-set.json"


class _AdoptedRun(unittest.TestCase):
    """A run directory that owns a seeded live campaign, adopted so the base plan and
    the store start identical. Every diff case edits a copy of that plan, which is
    what an operator does, and every diff asserts that nothing was written to the
    store."""

    seed_kw = {}

    def seed(self, state):
        return seed_live_campaign(state, **self.seed_kw)

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.state = fresh_state()
        self.cid = self.seed(self.state)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "run"
        self.lines = []
        self.assertEqual(self._run(["adopt", "--store", "teststore", "--campaign", str(self.cid),
                                    "--out", str(self.dir)]), 0, self.out())
        self.base_path = self.dir / "campaign-plan.json"
        self.manifest_path = self.dir / "run-manifest.json"
        self.base = json.loads(self.base_path.read_text())
        self.offer_keys = [o["key"] for o in self.base["offers"]]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, argv):
        self.t = DynamicTransport(self.disc, self.state)
        admin = make_client(self.t)
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            return ca.main(argv)

    def out(self):
        return "\n".join(self.lines)

    def cand(self):
        return json.loads(self.base_path.read_text())

    def manifest(self):
        return json.loads(self.manifest_path.read_text())

    def ids(self, section):
        return {e["key"]: e["id"] for e in self.manifest()[section]}

    def diff(self, cand, *extra, plan_path=None):
        path = plan_path or (self.dir / "campaign-plan.next.json")
        if cand is not None:
            path.write_text(json.dumps(cand, indent=2) + "\n")
        rc = self._run(["diff", "--plan", str(path), "--manifest", str(self.manifest_path), *extra])
        self.assertEqual([(c[0], c[1]) for c in self.t.calls if c[0] != "GET"], [],
                         "diff must send no write to the store")
        return rc

    def refuse(self, cand, *extra, needle):
        cs = self.dir / CHANGE_SET
        if cs.exists():
            cs.unlink()
        self.assertEqual(self.diff(cand, *extra), 1, self.out())
        self.assertIn(needle, self.out())
        self.assertFalse((self.dir / CHANGE_SET).exists(), "a refused diff writes no change set")
        return self.out()

    def change_set(self):
        return json.loads((self.dir / CHANGE_SET).read_text())

    def ops(self):
        return [(o["method"], o["section"], o["key"]) for o in self.change_set()["ops"]]


class DiffMatrix(_AdoptedRun):
    """Per field and per key: what the three-way diff turns into an op, and what it
    refuses outright."""

    def test_apply_then_diff_of_the_unchanged_plan_is_no_changes(self):
        # a created run, not an adopted one: suffixed package names and the recurring
        # trio are the two things a false rename or price op would come from
        d = Path(self.tmp.name) / "created"
        d.mkdir()
        plan = ca.recommend(self.disc, ns(name="Round trip"))
        plan["packages"][0]["price_recurring"] = "19.95"
        plan["packages"][0]["interval"] = "month"
        plan["packages"][0]["interval_count"] = 1
        plan_path = d / "campaign-plan.json"
        ca.atomic_write_json(plan_path, plan)
        state = fresh_state()
        ca.apply(make_client(DynamicTransport(self.disc, state)), plan,
                 ca.sha256_file(plan_path), d / "run-manifest.json", None)
        nxt = d / "campaign-plan.next.json"
        nxt.write_bytes(plan_path.read_bytes())
        t = DynamicTransport(self.disc, state)
        admin = make_client(t)
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            self.lines = []
            rc = ca.main(["diff", "--plan", str(nxt), "--manifest", str(d / "run-manifest.json")])
        self.assertEqual(rc, 0, self.out())
        self.assertIn("no changes", self.out())
        self.assertFalse((d / CHANGE_SET).exists())
        self.assertEqual([(c[0], c[1]) for c in t.calls if c[0] != "GET"], [])

    def test_adopt_then_diff_of_the_unchanged_plan_is_no_changes(self):
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        self.assertIn("no changes", self.out())
        self.assertFalse((self.dir / CHANGE_SET).exists())

    def test_a_campaign_edit_is_one_patch_with_explicit_clearing_values(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed"
        cand["campaign"]["statement_descriptor"] = "NEWDESC"
        cand["campaign"]["available_shipping_countries"] = []
        cand["campaign"]["additional_currencies"] = []
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("PATCH", "campaign", "campaign")])
        op = cs["ops"][0]
        self.assertEqual(op["id"], self.cid)
        self.assertEqual(op["path"], f"/api/admin/campaigns/{self.cid}/")
        self.assertEqual(op["body"], {"name": "Renamed", "statement_descriptor": "NEWDESC",
                                      "available_shipping_countries": []})
        self.assertEqual(op["before"], {"name": "Dashboard Bracelet", "statement_descriptor": None,
                                        "available_shipping_countries": ["US"]})
        self.assertEqual(op["after"], {"name": "Renamed", "statement_descriptor": "NEWDESC",
                                       "available_shipping_countries": []})
        self.assertFalse(cs["has_deletes"])
        self.assertEqual(cs["merged_plan"]["campaign"]["available_shipping_countries"], [])
        # additional_currencies clears to null, the code lists to []
        cand["campaign"]["additional_currencies"] = ["EUR"]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.change_set()["ops"][0]["body"]["additional_currencies"], ["EUR"])
        self.state["campaigns"][self.cid]["additional_currencies"] = ["EUR"]
        cand["campaign"]["additional_currencies"] = []
        self.base["campaign"]["additional_currencies"] = ["EUR"]
        ca.atomic_write_json(self.base_path, self.base)
        man = self.manifest()
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertIsNone(self.change_set()["ops"][0]["body"]["additional_currencies"])

    def test_a_price_change_sends_the_whole_prices_list(self):
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        pid = self.ids("packages")["pkg-801"]
        self.assertEqual((op["method"], op["section"], op["key"], op["id"]),
                         ("PATCH", "packages", "pkg-801", pid))
        self.assertEqual(op["path"], f"/api/admin/campaigns/{self.cid}/packages/{pid}/")
        self.assertEqual(op["body"], {"prices": [{"currency": "USD", "price": "29.95",
                                                  "price_recurring": None}]})
        self.assertEqual((op["before"], op["after"]), ({"price": "24.95"}, {"price": "29.95"}))
        self.assertEqual(self.change_set()["merged_plan"]["packages"][0]["price"], "29.95")

    def test_a_new_package_is_a_post_then_its_image_put_and_an_offer_scoped_by_key(self):
        cand = self.cand()
        cand["packages"].append({"key": "pkg-903", "role": "bump", "name": "Extra",
                                 "variant_title": "v903", "product_id": 22,
                                 "product_variant_ids": [903], "price": "9.95",
                                 "image": {"src": "https://cdn.example/extra.png"}})
        cand["offers"].append({"key": "offer-extra", "name": "Extra free", "offer_type": "offer",
                               "code": None,
                               "condition": {"type": "any", "value": None,
                                             "package_keys": ["pkg-903"]},
                               "benefit": {"type": "package_percentage", "value": "100.00",
                                           "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("POST", "packages", "pkg-903"),
                                      ("PUT", "packages", "pkg-903"),
                                      ("POST", "offers", "offer-extra")])
        post, put, offer = cs["ops"]
        self.assertNotIn("id", post)
        self.assertEqual(post["path"], f"/api/admin/campaigns/{self.cid}/packages/")
        self.assertEqual(post["body"], {"name": "Extra", "product_id": 22, "price": "9.95",
                                        "product_variant_ids": [903]})
        # the PUT has no id: its route is rebuilt from the id the POST returns
        self.assertNotIn("id", put)
        self.assertEqual(put["depends_on"], post["n"])
        self.assertEqual(put["path"], f"/api/admin/campaigns/{self.cid}/packages/<pkg-903>/image/")
        self.assertEqual(put["body"], {"src": "https://cdn.example/extra.png"})
        # an offer scoped to a package created in this change set carries keys, not ids
        self.assertIsNone(offer["body"])
        self.assertEqual(offer["package_keys"], ["pkg-903"])
        self.assertEqual(offer["body_template"]["condition"]["package_ids"], ["<pkg-903>"])
        self.assertIn("<pkg-903>", self.out())

    def test_a_removed_package_is_a_delete_and_an_added_offer_body_carries_live_ids(self):
        cand = self.cand()
        cand["packages"] = [p for p in cand["packages"] if p["key"] != "pkg-802"]
        cand["offers"].append({"key": "offer-extra", "name": "Hero again", "offer_type": "voucher",
                               "code": "HERO20",
                               "condition": {"type": "any", "value": None,
                                             "package_keys": ["pkg-801"]},
                               "benefit": {"type": "package_percentage", "value": "20.00",
                                           "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("DELETE", "packages", "pkg-802"),
                                      ("POST", "offers", "offer-extra")])
        delete, offer = cs["ops"]
        pid = self.ids("packages")["pkg-802"]
        self.assertEqual((delete["id"], delete["path"]),
                         (pid, f"/api/admin/campaigns/{self.cid}/packages/{pid}/"))
        self.assertIsNone(delete["body"])
        self.assertEqual(delete["before"]["price"], "12.95")
        self.assertEqual(delete["after"], {})
        self.assertTrue(cs["has_deletes"])
        self.assertEqual(offer["body"]["condition"]["package_ids"],
                         [self.ids("packages")["pkg-801"]])
        self.assertEqual(offer["body"]["code"], "HERO20")
        self.assertNotIn("package_keys", offer)
        self.assertIn("--allow-delete", self.out())

    def test_shipping_patch_post_and_delete(self):
        cand = self.cand()
        cand["shipping_methods"][0]["price"] = "7.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        sid = self.ids("shipping_methods")["standard"]
        op = self.change_set()["ops"][0]
        self.assertEqual((op["method"], op["key"], op["id"]), ("PATCH", "standard", sid))
        self.assertEqual(op["path"], f"/api/admin/campaigns/{self.cid}/shipping-methods/{sid}/")
        self.assertEqual(op["body"], {"prices": [{"currency": "USD", "price": "7.95"}]})

        cand = self.cand()
        cand["shipping_methods"] = [{"shipping_method": "express", "price": "14.95"}]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "shipping_methods", "standard"),
                                      ("POST", "shipping_methods", "express")])
        post = self.change_set()["ops"][1]
        self.assertEqual(post["path"], f"/api/admin/campaigns/{self.cid}/shipping-methods/")
        self.assertEqual(post["body"], {"shipping_method": "express", "price": "14.95"})

    def test_offer_patch_sends_the_whole_condition_and_benefit(self):
        key = self.offer_keys[0]
        cand = self.cand()
        offer = next(o for o in cand["offers"] if o["key"] == key)
        offer["name"] = "Buy 3 instead"
        offer["condition"]["value"] = 3
        offer["available"] = False
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        oid = self.ids("offers")[key]
        self.assertEqual((op["method"], op["section"], op["key"], op["id"]),
                         ("PATCH", "offers", key, oid))
        self.assertEqual(op["path"], f"/api/admin/campaigns/{self.cid}/offers/{oid}/")
        self.assertEqual(op["body"], {
            "name": "Buy 3 instead", "available": False,
            "condition": {"type": "count", "all_packages": False,
                          "package_ids": [self.ids("packages")["pkg-801"]], "value": 3}})
        self.assertEqual(op["before"]["condition_value"], 2)
        self.assertEqual(op["after"]["name"], "Buy 3 instead")
        # the benefit goes whole when any part of it changes
        cand = self.cand()
        next(o for o in cand["offers"] if o["key"] == key)["benefit"]["price_rounding"] = "0.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.change_set()["ops"][0]["body"]["benefit"],
                         {"type": "package_percentage", "value": "55.00", "price_rounding": "0.95"})

    def test_a_deleted_offer_is_the_first_op(self):
        key = self.offer_keys[0]
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != key]
        cand["campaign"]["name"] = "Renamed"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PATCH", "campaign", "campaign"),
                                      ("DELETE", "offers", key)])

    def test_the_store_already_at_the_candidates_value_sends_nothing_but_is_written(self):
        """A value the candidate and the dashboard reached independently needs no
        request, and the plan still has to move: the base plan is the only record of
        what the campaign is, and leaving it behind makes every later diff re-report
        the same convergence."""
        self.state["packages"][self.cid][self.ids("packages")["pkg-801"]]["prices"][0]["price"] = "29.95"
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertNotIn("no changes", self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertEqual(cs["plan_only"], ['package pkg-801.price: "24.95" -> "29.95"'])
        self.assertIn("Plan only, nothing sent", self.out())
        self.assertEqual(cs["merged_plan"]["packages"][0]["price"], "29.95")

    def test_a_changed_image_src_is_a_put_and_a_removed_one_is_a_warning(self):
        man = self.manifest()
        man["packages"][0]["image_src"] = "https://cdn.example/old.png"
        ca.atomic_write_json(self.manifest_path, man)
        cand = self.cand()
        cand["packages"][0]["image"] = {"src": "https://cdn.example/new.png"}
        self.assertEqual(self.diff(cand), 0, self.out())
        pid = self.ids("packages")["pkg-801"]
        op = self.change_set()["ops"][0]
        self.assertEqual((op["method"], op["key"], op["id"]), ("PUT", "pkg-801", pid))
        self.assertEqual(op["path"], f"/api/admin/campaigns/{self.cid}/packages/{pid}/image/")
        self.assertEqual(op["before"], {"image": CATALOGUE_IMAGE,
                                        "image_src": "https://cdn.example/old.png"})
        self.assertEqual(op["after"], {"image_src": "https://cdn.example/new.png"})
        # dropping the field is a warning and no op: there is no delete-image route
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("PATCH", "campaign", "campaign")])
        self.assertIn("dropped image.src https://cdn.example/old.png", " ".join(cs["warnings"]))

    def test_convert_scope_adoption_yields_an_offer_patch_on_the_first_diff(self):
        state = fresh_state()
        cid = seed_live_campaign(state, all_packages=True)
        oid = sorted(state["offers"][cid])[0]
        d = Path(self.tmp.name) / "converted"
        self.state, self.cid = state, cid
        self.assertEqual(self._run(["adopt", "--store", "teststore", "--campaign", str(cid),
                                    "--out", str(d), "--convert-scope", str(oid)]), 0, self.out())
        self.dir, self.base_path = d, d / "campaign-plan.json"
        self.manifest_path = d / "run-manifest.json"
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("PATCH", "offers", f"offer-{oid}")])
        body = cs["ops"][0]["body"]
        self.assertFalse(body["condition"]["all_packages"])
        self.assertEqual(sorted(body["condition"]["package_ids"]),
                         sorted(self.ids("packages").values()))
        # all_packages is never preserved, so the conversion is an op, not a kept value
        self.assertEqual(cs["preserved"], [])

    def test_deleting_a_package_the_store_changed_refuses_until_delete_changed(self):
        pid = self.ids("packages")["pkg-802"]  # no offer scopes it
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "13.95"
        cand = self.cand()
        cand["packages"] = [p for p in cand["packages"] if p["key"] != "pkg-802"]
        msg = self.refuse(cand, needle="packages pkg-802 cannot be deleted: the store changed it")
        self.assertIn('price: base "12.95", store "13.95"', msg)
        self.assertIn("--delete-changed packages:pkg-802", msg)
        self.assertEqual(self.diff(cand, "--delete-changed", "packages:pkg-802"), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("DELETE", "packages", "pkg-802")])
        self.assertIn("authorised with --delete-changed packages:pkg-802", " ".join(cs["warnings"]))

    def test_deleting_a_shipping_method_or_an_offer_the_store_changed_refuses(self):
        sid = self.ids("shipping_methods")["standard"]
        self.state["shipping-methods"][self.cid][sid]["prices"][0]["price"] = "7.95"
        cand = self.cand()
        cand["shipping_methods"] = [{"shipping_method": "express", "price": "14.95"}]
        self.refuse(cand, needle="shipping_methods standard cannot be deleted")
        self.assertEqual(self.diff(cand, "--delete-changed", "shipping_methods:standard"), 0,
                         self.out())
        self.state["shipping-methods"][self.cid][sid]["prices"][0]["price"] = "6.95"

        key = self.offer_keys[1]  # the free-shipping offer
        oid = self.ids("offers")[key]
        self.state["offers"][self.cid][oid]["benefit"]["value"] = "50.00"
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != key]
        msg = self.refuse(cand, needle=f"offers {key} cannot be deleted")
        self.assertIn("benefit_value", msg)
        self.assertEqual(self.diff(cand, "--delete-changed", f"offers:{key}"), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "offers", key)])

    def test_swapping_two_offer_names_refuses_with_the_two_step_instruction(self):
        a, b = self.offer_keys
        cand = self.cand()
        first = next(o for o in cand["offers"] if o["key"] == a)
        second = next(o for o in cand["offers"] if o["key"] == b)
        first["name"], second["name"] = second["name"], first["name"]
        # the merged plan is perfectly valid: the names are still unique at the end
        self.assertEqual(ca.validate_plan(cand), [])
        msg = self.refuse(cand, needle="which live offer")
        self.assertIn("Do it as 2 updates through a temporary name", msg)

    def test_candidate_level_hard_refusals(self):
        cases = {
            "currency cannot change once the campaign exists":
                lambda c: c["campaign"].__setitem__("currency", "EUR"),
            "product_id and product_variant_ids cannot change":
                lambda c: c["packages"][0].__setitem__("product_variant_ids", [999]),
            "the store code cannot change on a campaign shipping method":
                lambda c: c.__setitem__("shipping_methods", [
                    {"key": "standard", "shipping_method": "express", "price": "6.95"}]),
            "all_packages is never allowed":
                lambda c: c["offers"][0]["condition"].__setitem__("all_packages", True),
            "candidate plan has blockers":
                lambda c: c.__setitem__("blockers", ["something the operator left in"]),
            "candidate plan is invalid":
                lambda c: c["packages"][0].__setitem__("price", "free"),
            "must all name one store":
                lambda c: c.__setitem__("store_slug", "otherstore"),
        }
        for needle, break_it in cases.items():
            with self.subTest(needle=needle):
                cand = self.cand()
                break_it(cand)
                if needle == "must all name one store":
                    cand["store_origin"] = ca.slug_to_origin("otherstore")
                self.refuse(cand, needle=needle)

    def test_an_unmanaged_live_object_refuses(self):
        self.state["seq"] += 1
        self.state["packages"][self.cid][self.state["seq"]] = {
            "id": self.state["seq"], "product_id": 22, "product_variant_id": 999,
            "product_variant_name": "v999", "name": "Dashboard extra - v999",
            "prices": [{"currency": "USD", "price": "5.00", "price_recurring": None}],
            "is_recurring": False, "interval": "", "interval_count": None,
            "product_purchase_availability": "available", "image": CATALOGUE_IMAGE}
        self.refuse(self.cand(), needle="does not own")

    def test_a_package_delete_a_surviving_offer_scopes_refuses(self):
        cand = self.cand()
        cand["packages"] = [p for p in cand["packages"] if p["key"] != "pkg-801"]
        for o in cand["offers"]:
            o["condition"]["package_keys"] = ["pkg-802"]
        # the plan is valid, but the live offers still scope the package being deleted
        self.refuse(cand, needle="cannot be deleted while live offer(s)")

    def test_a_pending_entry_and_an_active_update_both_refuse(self):
        man = self.manifest()
        man["packages"][0]["status"] = "pending"
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse(self.cand(), needle="this run never finished")
        man["packages"][0]["status"] = "created"
        man["active_update"] = {"change_set_sha256": "x", "merged_plan_sha256": "y",
                                "started_at": "2026-10-08T00:00:00Z"}
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse(self.cand(), needle="an update is in progress; finish it with update --resume")

    def test_an_identity_failure_refuses(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["product_variant_id"] = 888
        self.refuse(self.cand(), needle="ownership check failed")

    def test_an_edited_canonical_plan_refuses(self):
        base = self.cand()
        base["campaign"]["name"] = "Edited in place"
        ca.atomic_write_json(self.base_path, base)
        self.refuse(self.cand(), needle="does not hash to the manifest's plan_sha256")

    def test_a_base_plan_key_the_manifest_does_not_own_refuses(self):
        # a run that stopped before journalling a package: the key is in the plan, there
        # is no entry and nothing live, so there is nothing to diff that key against
        extra = {"key": "pkg-999", "role": "bump", "name": "Never created",
                 "variant_title": "v999", "product_id": 22, "product_variant_ids": [999],
                 "price": "5.00"}
        base = self.cand()
        base["packages"].append(extra)
        ca.atomic_write_json(self.base_path, base)
        man = self.manifest()
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse(self.cand(), needle="the base plan names object(s) the manifest does not own")

    def test_an_invalid_canonical_plan_refuses_instead_of_crashing(self):
        cand = self.cand()  # a perfectly good candidate; the base is the broken one
        base = self.cand()
        base["packages"][0]["price"] = "free"
        ca.atomic_write_json(self.base_path, base)
        man = self.manifest()
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse(cand, needle="the canonical plan in the run directory is invalid")

    def test_the_canonical_plan_cannot_be_the_candidate(self):
        self.assertEqual(self.diff(None, plan_path=self.base_path), 1, self.out())
        self.assertIn("diff compares an edited COPY", self.out())

    def test_zero_ops_with_preserved_values_is_still_written(self):
        self.state["campaigns"][self.cid]["name"] = "Renamed in the dashboard"
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertFalse(cs["has_deletes"])
        self.assertEqual(len(cs["preserved"]), 1)
        self.assertIn("campaign.name", cs["preserved"][0])
        self.assertEqual(cs["merged_plan"]["campaign"]["name"], "Renamed in the dashboard")
        self.assertIn("only records values preserved from the store", self.out())
        # the merged plan is written once, by diff, and the change set names those bytes
        merged = self.dir / cs["merged_plan_file"]
        self.assertTrue(merged.exists())
        self.assertEqual(ca.sha256_file(merged), cs["merged_plan_sha256"])
        self.assertEqual(cs["merged_plan_file"], f"campaign-plan.{cs['merged_plan_sha256'][:8]}.json")
        self.assertEqual(json.loads(merged.read_text()), cs["merged_plan"])
        self.assertEqual(ca.validate_plan(cs["merged_plan"]), [])
        self.assertEqual((cs["schema"], cs["run_id"], cs["store_slug"], cs["campaign_id"]),
                         (1, self.manifest()["run_id"], "teststore", self.cid))
        self.assertEqual(cs["base_plan_sha256"], self.manifest()["plan_sha256"])
        self.assertEqual(cs["baseline_sha256"], ca.baseline_sha256(cs["baseline"]))
        self.assertIn(f"--change-set-sha256 {ca.sha256_file(self.dir / CHANGE_SET)}", self.out())
        self.assertNotIn("--allow-delete", self.out())


class DiffMatrixRecurring(_AdoptedRun):
    seed_kw = {"recurring": True}

    def test_a_price_change_keeps_the_recurring_fields(self):
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        self.assertEqual(op["body"], {"prices": [{"currency": "USD", "price": "29.95",
                                                  "price_recurring": "24.95"}]})
        self.assertNotIn("interval", op["body"])
        merged = self.change_set()["merged_plan"]["packages"][0]
        self.assertEqual((merged["price_recurring"], merged["interval"], merged["interval_count"]),
                         ("24.95", "month", 1))

    def test_dropping_the_recurring_trio_clears_the_interval(self):
        cand = self.cand()
        for f in ("price_recurring", "interval", "interval_count"):
            cand["packages"][0].pop(f)
        self.assertEqual(self.diff(cand), 0, self.out())
        op = self.change_set()["ops"][0]
        self.assertEqual(op["body"], {"prices": [{"currency": "USD", "price": "24.95",
                                                  "price_recurring": None}],
                                      "interval": None, "interval_count": None})


class ThreeWayDiff(_AdoptedRun):
    """Base, candidate and live together: what the store changed and the candidate did
    not is kept, and what both changed is a conflict nobody guesses at."""

    def test_an_untouched_field_the_store_changed_is_preserved_into_the_merged_plan(self):
        self.state["campaigns"][self.cid]["available_shipping_countries"] = [{"code": "US"},
                                                                             {"code": "CA"}]
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(self.ops(), [("PATCH", "packages", "pkg-801")])
        self.assertEqual(len(cs["preserved"]), 1)
        self.assertIn("campaign.available_shipping_countries", cs["preserved"][0])
        self.assertEqual(cs["merged_plan"]["campaign"]["available_shipping_countries"],
                         ["CA", "US"])
        self.assertEqual(cs["merged_plan"]["packages"][0]["price"], "29.95")

    def test_a_dashboard_rename_with_an_untouched_candidate_is_preserved_everywhere(self):
        key = self.offer_keys[0]
        self.state["campaigns"][self.cid]["name"] = "Dashboard renamed"
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["name"] = "Renamed Bracelet - v801"
        self.state["offers"][self.cid][self.ids("offers")[key]]["name"] = "Renamed offer"
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertEqual(sorted(x.split(":")[0] for x in cs["preserved"]),
                         ["campaign.name", f"offer {key}.name", "package pkg-801.name"])
        merged = cs["merged_plan"]
        self.assertEqual(merged["campaign"]["name"], "Dashboard renamed")
        # the live suffix is stripped, so the merged plan keeps a bare package name
        self.assertEqual(merged["packages"][0]["name"], "Renamed Bracelet")
        self.assertEqual(next(o for o in merged["offers"] if o["key"] == key)["name"],
                         "Renamed offer")
        # a rename is not ownership: check_identity is silent about it
        snap = ca.snapshot_live(make_client(DynamicTransport(self.disc, self.state)), self.cid)
        self.assertEqual(ca.check_identity(ca.Manifest.load(self.manifest_path), self.base, snap),
                         [])

    def test_conflicting_edits_refuse_with_all_three_values(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "19.95"
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        msg = self.refuse(cand, needle="conflicting edits")
        self.assertIn('package pkg-801.price: base "24.95", candidate "29.95", store "19.95"', msg)
        self.assertIn("Decide which value wins", msg)

    def test_a_preserved_rename_that_collides_with_a_candidate_rename_refuses_at_the_merge(self):
        a, b = self.offer_keys
        # the dashboard renamed offer A to what the candidate renames offer B to
        self.state["offers"][self.cid][self.ids("offers")[a]]["name"] = "Shared name"
        cand = self.cand()
        next(o for o in cand["offers"] if o["key"] == b)["name"] = "Shared name"
        msg = self.refuse(cand, needle="the merged plan")
        self.assertIn("duplicate offer name 'Shared name'", msg)


class CheckIdentity(_AdoptedRun):
    """The read-only ownership pre-flight: only what cannot legitimately change."""

    def snap(self):
        return ca.snapshot_live(make_client(DynamicTransport(self.disc, self.state)), self.cid)

    def man(self):
        return ca.Manifest.load(self.manifest_path)

    def test_mutable_drift_is_ignored(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["campaigns"][self.cid]["name"] = "Renamed"
        self.state["campaigns"][self.cid]["statement_descriptor"] = "NEW"
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "99.95"
        self.state["packages"][self.cid][pid]["name"] = "Something else - v801"
        self.state["offers"][self.cid][self.ids("offers")[self.offer_keys[0]]]["name"] = "Other"
        self.assertEqual(ca.check_identity(self.man(), self.base, self.snap()), [])

    def test_a_changed_product_variant_id_refuses_and_a_changed_created_at_does_too(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["product_variant_id"] = 888
        problems = ca.check_identity(self.man(), self.base, self.snap())
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("is on product variant 888, not 801", problems[0])
        self.state["packages"][self.cid][pid]["product_variant_id"] = 801
        self.state["campaigns"][self.cid]["created_at"] = "2026-08-09T09:00:00+00:00"
        problems = ca.check_identity(self.man(), self.base, self.snap())
        self.assertIn("the id names another campaign", problems[0])

    def test_a_missing_object_is_reported_unless_a_delete_op_names_it(self):
        before = self.manifest_path.read_bytes()
        pid = self.ids("packages")["pkg-802"]
        self.state["packages"][self.cid].pop(pid)
        man, snap = self.man(), self.snap()
        problems = ca.check_identity(man, self.base, snap)
        self.assertEqual(problems, [f"packages pkg-802 (id {pid}) is no longer on the campaign"])
        ops = [{"n": 1, "method": "DELETE", "section": "packages", "key": "pkg-802", "id": pid}]
        self.assertEqual(ca.check_identity(man, self.base, snap, ops=ops), [])
        self.assertEqual(self.manifest_path.read_bytes(), before)  # never writes

    def test_the_shipping_code_comes_from_the_base_plan_when_the_entry_has_none(self):
        man = self.manifest()
        del man["shipping_methods"][0]["shipping_method"]
        ca.atomic_write_json(self.manifest_path, man)
        self.assertEqual(ca.check_identity(self.man(), self.base, self.snap()), [])
        sid = self.ids("shipping_methods")["standard"]
        self.state["shipping-methods"][self.cid][sid]["shipping_method"] = "express"
        problems = ca.check_identity(self.man(), self.base, self.snap())
        self.assertEqual(problems, [f"shipping standard (id {sid}) is on store code 'express', "
                                    "not 'standard'"])


def seed_voucher_campaign(state):
    """A live campaign with 2 vouchers, so a code swap can be tested the way a name
    swap is: valid as a final state, illegal at every intermediate step."""
    cid = seed_live_campaign(state)
    pid = sorted(state["packages"][cid])[0]
    for name, code, pct in (("Exit ten", "SAVE10", "10.00"), ("Exit twenty", "SAVE20", "20.00")):
        state["seq"] += 1
        state["offers"][cid][state["seq"]] = {
            "id": state["seq"], "name": name, "offer_type": "voucher", "code": code,
            "available": True,
            "condition": offer_condition(state, {"type": "any", "package_ids": [pid]}),
            "benefit": offer_benefit({"type": "package_percentage", "value": pct,
                                      "price_rounding": None})}
    return cid


class DiffMatrixVouchers(_AdoptedRun):
    def seed(self, state):
        return seed_voucher_campaign(state)

    def test_swapping_two_voucher_codes_refuses_at_the_step_that_would_collide(self):
        cand = self.cand()
        a = next(o for o in cand["offers"] if o.get("code") == "SAVE10")
        b = next(o for o in cand["offers"] if o.get("code") == "SAVE20")
        a["code"], b["code"] = b["code"], a["code"]
        self.assertEqual(ca.validate_plan(cand), [])  # unique at the end
        msg = self.refuse(cand, needle="would set the voucher code")
        self.assertIn("Do it as 2 updates through a temporary name", msg)


class _Interrupted(Exception):
    """The process dying mid-run: whatever the journal holds on disk is all that is
    left for a resume to reason about."""


class InterruptingTransport(DynamicTransport):
    """A store that stops at a chosen write. `stop_after=N` cuts the run before write
    N+1 leaves (so N writes landed); `lose_response_on={N}` lets write N land and then
    loses its answer, which is the one state a resume cannot read off the journal."""

    def __init__(self, disc, state, *, stop_after=None, lose_response_on=(), **kw):
        super().__init__(disc, state, **kw)
        self.stop_after, self.lose_response_on = stop_after, set(lose_response_on)
        self.writes = 0

    def __call__(self, req, timeout):
        if req.get_method() != "GET":
            self.writes += 1
            if self.stop_after is not None and self.writes > self.stop_after:
                raise _Interrupted(f"the run stopped before write {self.writes}")
            if self.writes in self.lose_response_on:
                super().__call__(req, timeout)  # the store applies it
                raise _Interrupted(f"the answer to write {self.writes} was lost")
        return super().__call__(req, timeout)


class _UpdatableRun(_AdoptedRun):
    """An adopted run plus what an update needs: the command the last diff printed, a
    read-back of everything that went to the store, and the run's files afterwards."""

    def _run_with(self, transport, argv):
        self.t = transport
        admin = make_client(self.t)
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            return ca.main(argv)

    def update_argv(self, *extra, plan=None, cs=None, sha=None):
        cs_path = Path(cs) if cs else (self.dir / CHANGE_SET)
        merged = Path(plan) if plan else self.dir / self.change_set()["merged_plan_file"]
        return ["update", "--plan", str(merged), "--manifest", str(self.manifest_path),
                "--change-set", str(cs_path), "--yes",
                "--change-set-sha256", sha or ca.sha256_file(cs_path), *extra]

    def update(self, *extra, transport=None, plan=None, cs=None, sha=None):
        argv = self.update_argv(*extra, plan=plan, cs=cs, sha=sha)
        return self._run_with(transport or DynamicTransport(self.disc, self.state), argv)

    def sent(self):
        return [(c[0], c[1]) for c in self.t.calls if c[0] != "GET"]

    def bodies(self):
        return [(c[0], c[1], c[2]) for c in self.t.calls if c[0] != "GET"]

    def plan_now(self):
        return json.loads(self.base_path.read_text())

    def tamper(self, mutate):
        cs = self.change_set()
        mutate(cs)
        ca.atomic_write_json(self.dir / CHANGE_SET, cs)
        return cs

    def refuse_update(self, *extra, needle, code=2, **kw):
        before = self.manifest_path.read_bytes()
        self.assertEqual(self.update(*extra, **kw), code, self.out())
        self.assertIn(needle, self.out())
        self.assertEqual(self.sent(), [], "a refused update must send no write")
        self.assertEqual(self.manifest_path.read_bytes(), before,
                         "a refused update must not touch the manifest")
        return self.out()

    def edited(self, **kw):
        """A candidate with one field per section changed, diffed and ready to apply."""
        cand = self.cand()
        cand["campaign"]["name"] = kw.get("name", "Edited campaign")
        cand["packages"][0]["price"] = kw.get("price", "29.95")
        cand["shipping_methods"][0]["price"] = kw.get("ship", "7.95")
        cand["offers"][0]["benefit"]["value"] = kw.get("pct", "60.00")
        self.assertEqual(self.diff(cand), 0, self.out())
        return cand


class UpdateGate(_UpdatableRun):
    """Everything update checks before its first request. Every case here must refuse
    with nothing sent to the store."""

    def setUp(self):
        super().setUp()
        self.edited()

    def test_without_the_flags_it_prints_the_change_set_and_builds_no_client(self):
        cs_path = self.dir / CHANGE_SET

        def no_client(slug):
            raise AssertionError("the gate must refuse before a client exists")
        self.lines = []
        with mock.patch.object(ca, "_client_for", no_client), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            rc = ca.main(["update", "--plan", str(self.dir / self.change_set()["merged_plan_file"]),
                          "--manifest", str(self.manifest_path), "--change-set", str(cs_path)])
        self.assertEqual(rc, 2, self.out())
        self.assertIn("Requests update will send", self.out())
        self.assertIn("NOT APPLIED", self.out())
        self.assertIn(f"--change-set-sha256 {ca.sha256_file(cs_path)}", self.out())

    def test_a_wrong_change_set_hash_refuses(self):
        self.refuse_update(needle="NOT APPLIED", sha="0" * 64)

    def test_a_plan_that_is_not_the_merged_plan_refuses(self):
        other = self.dir / "campaign-plan.other.json"
        other.write_text(json.dumps(self.cand(), indent=2) + "\n")
        self.refuse_update(needle="not the plan passed with --plan", plan=other)

    def test_a_plan_whose_bytes_were_re_indented_without_the_change_set_refuses(self):
        merged = self.dir / self.change_set()["merged_plan_file"]
        merged.write_text(json.dumps(json.loads(merged.read_text()), indent=4) + "\n")
        # the same plan, different bytes: the change set names the bytes diff wrote
        self.refuse_update(needle="diff is the only writer of that file")

    def test_baseline_drift_between_diff_and_update_refuses(self):
        self.state["campaigns"][self.cid]["language"] = "de"
        self.refuse_update(needle="campaign changed since you reviewed the diff; re-run diff")

    def test_a_dashboard_image_change_between_diff_and_update_is_drift(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["image"] = "https://cdn.test/media/other.webp"
        self.refuse_update(needle="campaign changed since you reviewed the diff")

    def test_deletes_without_allow_delete_refuse(self):
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != self.offer_keys[1]]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.refuse_update(needle="--allow-delete was not passed")
        self.assertEqual(self.update("--allow-delete"), 0, self.out())

    def test_a_change_set_from_another_run_or_campaign_refuses(self):
        self.tamper(lambda cs: cs.update(run_id="00000000-0000-4000-8000-000000000000"))
        self.refuse_update(needle="names run_id")
        self.tamper(lambda cs: cs.update(run_id=self.manifest()["run_id"], campaign_id=999))
        self.refuse_update(needle="names campaign_id 999")

    def test_a_change_set_from_another_store_refuses(self):
        self.tamper(lambda cs: cs.update(store_slug="otherstore"))
        self.refuse_update(needle="names store_slug 'otherstore'")

    def test_a_stale_base_plan_hash_refuses_after_an_intervening_update(self):
        stale = self.dir / "change-set.stale.json"
        shutil.copy(self.dir / CHANGE_SET, stale)
        stale_plan = self.dir / self.change_set()["merged_plan_file"]
        self.assertEqual(self.update(), 0, self.out())  # the intervening update
        self.assertEqual(self.update(cs=stale, plan=stale_plan), 2, self.out())
        self.assertIn("has already been applied", self.out())
        cs = json.loads(stale.read_text())
        cs["merged_plan_sha256"] = "1" * 64
        ca.atomic_write_json(stale, cs)
        self.assertEqual(self.update(cs=stale, plan=stale_plan), 2, self.out())
        self.assertIn("the run moved on after the diff", self.out())

    def test_an_edited_canonical_plan_refuses(self):
        self.base_path.write_text(self.base_path.read_text() + "\n")
        self.refuse_update(needle="edited in place")

    def test_an_op_id_the_manifest_does_not_own_refuses(self):
        self.tamper(lambda cs: cs["ops"][1].update(id=999, path=cs["ops"][1]["path"]))
        self.refuse_update(needle="is not an object this run owns")

    def test_an_op_path_outside_this_campaign_refuses(self):
        pid = self.ids("packages")["pkg-801"]
        self.tamper(lambda cs: cs["ops"][1].update(
            path=f"/api/admin/campaigns/999/packages/{pid}/"))
        self.refuse_update(needle="is not the route for PATCH packages pkg-801")

    def test_an_op_path_whose_section_or_id_does_not_match_refuses(self):
        pid = self.ids("packages")["pkg-801"]
        self.tamper(lambda cs: cs["ops"][1].update(
            path=f"/api/admin/campaigns/{self.cid}/offers/{pid}/"))
        self.refuse_update(needle="is not the route for PATCH packages pkg-801")
        other = self.ids("packages")["pkg-802"]
        self.tamper(lambda cs: cs["ops"][1].update(
            path=f"/api/admin/campaigns/{self.cid}/packages/{other}/"))
        self.refuse_update(needle="is not the route for PATCH packages pkg-801")

    def test_a_post_carrying_an_id_or_reusing_a_live_key_refuses(self):
        cand = self.cand()
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        self.assertEqual(self.diff(cand), 0, self.out())
        n = next(o["n"] for o in self.change_set()["ops"]
                 if o["method"] == "POST") - 1
        self.tamper(lambda cs: cs["ops"][n].update(id=4242))
        self.refuse_update(needle="cannot name id 4242")
        self.tamper(lambda cs: (cs["ops"][n].pop("id"),
                                cs["ops"][n].update(key="standard")))
        self.refuse_update(needle="is already an object this run owns")

    def test_an_op_out_of_order_refuses(self):
        self.tamper(lambda cs: cs["ops"][1].update(n=99))
        self.refuse_update(needle="is out of step")

    def test_a_post_its_dependent_image_put_and_a_scoped_offer_pass_the_binding(self):
        cand = self.cand()
        cand["packages"].append({"key": "pkg-900", "role": "bump", "name": "New Bump",
                                 "variant_title": "v900", "product_id": 22,
                                 "product_variant_ids": [900], "price": "9.95",
                                 "image": {"src": "https://cdn.example/new.png"}})
        cand["offers"].append({"key": "offer-new", "name": "New Offer", "offer_type": "offer",
                               "code": None,
                               "condition": {"type": "any", "value": None,
                                             "package_keys": ["pkg-900"]},
                               "benefit": {"type": "package_percentage", "value": "10.00",
                                           "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        ops = {(o["method"], o["key"]): o for o in self.change_set()["ops"]}
        put = ops[("PUT", "pkg-900")]
        self.assertEqual((put.get("id"), put["depends_on"]),
                         (None, ops[("POST", "pkg-900")]["n"]))
        self.assertIsNone(ops[("POST", "offer-new")]["body"])
        self.assertEqual(self.update(), 0, self.out())
        new_id = self.ids("packages")["pkg-900"]
        self.assertIn(("PUT", f"/api/admin/campaigns/{self.cid}/packages/{new_id}/image/"),
                      self.sent())
        offer_post = next(b for b in self.bodies()
                          if b[0] == "POST" and b[1].endswith("/offers/"))
        self.assertEqual(offer_post[2]["condition"]["package_ids"], [new_id])

    def test_the_ownership_read_back_exits_1_not_2(self):
        # the gate family is exit 2; the ownership check is not a gate, and it runs
        # before the baseline hash is recomputed, so this is what the operator sees
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["product_variant_id"] = 999
        self.refuse_update(needle="ownership check failed; refusing to update", code=1)

    def test_an_active_update_refuses_a_fresh_update(self):
        man = self.manifest()
        man["active_update"] = {"change_set_sha256": "x", "merged_plan_sha256": "y",
                                "started_at": "2026-10-08T00:00:00Z"}
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse_update(needle="an update is in progress")


class PreservedOnly(_UpdatableRun):
    """A change set with no requests in it is still worth applying: it carries the
    values the store changed and the candidate left alone."""

    def test_a_zero_op_change_set_promotes_the_merged_plan_with_no_write(self):
        self.state["campaigns"][self.cid]["name"] = "Renamed in the dashboard"
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        old_sha = self.manifest()["plan_sha256"]
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [], "nothing is sent for a change set with no ops")
        self.assertEqual(self.plan_now()["campaign"]["name"], "Renamed in the dashboard")
        man = self.manifest()
        self.assertEqual(man["plan_sha256"], cs["merged_plan_sha256"])
        self.assertEqual(man["campaign"]["name"], "Renamed in the dashboard")
        self.assertNotIn("active_update", man)
        self.assertNotIn("pending_promotion", man)
        self.assertEqual(ca.sha256_file(self.base_path), cs["merged_plan_sha256"])
        # the plan it replaced is kept as a byte copy
        archive = self.dir / f"campaign-plan.{old_sha[:8]}.json"
        self.assertEqual(ca.sha256_file(archive), old_sha)
        self.assertEqual([h["change_set_sha256"] for h in man["history"]],
                         [ca.sha256_file(self.dir / CHANGE_SET)])
        # and the run is in step: a second diff of the promoted plan sees nothing
        self.assertEqual(self.diff(self.plan_now()), 0, self.out())
        self.assertIn("no changes", self.out())


class ImageUpdate(_UpdatableRun):
    """An approved image PUT on a package that already carries one."""

    def test_a_put_on_a_package_whose_image_is_already_set_is_sent_and_recorded(self):
        man = self.manifest()
        man["packages"][0].update(image_status="set", image_src="https://cdn.example/old.png",
                                  image=CATALOGUE_IMAGE)
        ca.atomic_write_json(self.manifest_path, man)
        cand = self.cand()
        cand["packages"][0]["image"] = {"src": "https://cdn.example/new.png"}
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PUT", "packages", "pkg-801")])
        self.assertEqual(self.update(), 0, self.out())
        pid = self.ids("packages")["pkg-801"]
        self.assertEqual(self.bodies(),
                         [("PUT", f"/api/admin/campaigns/{self.cid}/packages/{pid}/image/",
                           {"src": "https://cdn.example/new.png"})])
        e = self.manifest()["packages"][0]
        self.assertEqual((e["image_status"], e["image_src"], e["image"]),
                         ("set", "https://cdn.example/new.png", OVERRIDE_IMAGE))
        self.assertEqual(self.plan_now()["packages"][0]["image"],
                         {"src": "https://cdn.example/new.png"})

    def test_a_rejected_image_marks_the_package_failed_and_the_update_finishes(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed too"
        cand["packages"][0]["image"] = {"src": "https://cdn.example/new.png"}
        self.assertEqual(self.diff(cand), 0, self.out())
        pid = self.ids("packages")["pkg-801"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PUT", f"/api/admin/campaigns/{self.cid}/packages/{pid}/image/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertIn("did not land", self.out())
        e = self.manifest()["packages"][0]
        self.assertEqual(e["image_status"], "failed")
        # the campaign PATCH before it landed, and the plan was promoted anyway
        self.assertEqual(self.state["campaigns"][self.cid]["name"], "Renamed too")
        self.assertEqual(self.plan_now()["campaign"]["name"], "Renamed too")
        self.assertNotIn("active_update", self.manifest())


class UpdateOrder(_UpdatableRun):
    """Execution order: the campaign, then offer deletes, then packages with their
    images, then shipping, then offers."""

    def _every_section(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Edited campaign"
        cand["packages"][0]["price"] = "29.95"
        cand["packages"] = [p for p in cand["packages"] if p["key"] != "pkg-802"]
        cand["packages"].append({"key": "pkg-900", "role": "bump", "name": "New Bump",
                                 "variant_title": "v900", "product_id": 22,
                                 "product_variant_ids": [900], "price": "9.95",
                                 "image": {"src": "https://cdn.example/new.png"}})
        cand["shipping_methods"][0]["price"] = "7.95"
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        cand["offers"] = [o for o in cand["offers"] if o["key"] != self.offer_keys[1]]
        cand["offers"][0]["benefit"]["value"] = "60.00"
        cand["offers"].append({"key": "offer-new", "name": "New Offer", "offer_type": "offer",
                               "code": None,
                               "condition": {"type": "any", "value": None,
                                             "package_keys": ["pkg-900"]},
                               "benefit": {"type": "package_percentage", "value": "10.00",
                                           "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        return cand

    def test_every_section_in_one_change_set_goes_out_in_the_printed_order(self):
        self._every_section()
        planned = [(o["method"], o["section"], o["key"]) for o in self.change_set()["ops"]]
        self.assertEqual(planned, [
            ("PATCH", "campaign", "campaign"),
            ("DELETE", "offers", self.offer_keys[1]),
            ("DELETE", "packages", "pkg-802"),
            ("PATCH", "packages", "pkg-801"),
            ("POST", "packages", "pkg-900"),
            ("PUT", "packages", "pkg-900"),
            ("PATCH", "shipping_methods", "standard"),
            ("POST", "shipping_methods", "express"),
            ("PATCH", "offers", self.offer_keys[0]),
            ("POST", "offers", "offer-new")])
        self.assertEqual(self.update("--allow-delete"), 0, self.out())
        self.assertEqual([m for m, _p in self.sent()],
                         [o[0] for o in planned])
        self.assertEqual([p for _m, p in self.sent()],
                         [o["path"] for o in self.change_set()["ops"][:4]]
                         + [self.sent()[4][1]]  # the POST's collection route
                         + [f"/api/admin/campaigns/{self.cid}/packages/"
                            f"{self.ids('packages')['pkg-900']}/image/"]
                         + [o["path"] for o in self.change_set()["ops"][6:]])

    def test_the_journal_records_every_op_queued_then_in_flight_then_done(self):
        self.edited()
        saved = []
        real = ca.Manifest.save

        def spy(man):
            real(man)
            saved.append([(o["n"], o["status"]) for o in man.data.get("ops") or []])
        with mock.patch.object(ca.Manifest, "save", spy):
            self.assertEqual(self.update(), 0, self.out())
        self.assertIn([(1, "queued"), (2, "queued"), (3, "queued"), (4, "queued")], saved)
        # every op is saved in_flight before the next one is even looked at
        for n in (1, 2, 3, 4):
            flight = next(i for i, s in enumerate(saved) if (n, "in_flight") in s)
            done = next(i for i, s in enumerate(saved) if (n, "done") in s)
            self.assertLess(flight, done, f"op {n} was not saved in flight before it was done")

    def test_an_op_that_fails_stops_the_run_and_leaves_the_journal_mid_update(self):
        self.edited()
        pid = self.ids("packages")["pkg-801"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{pid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertIn("returned 400", self.out())
        man = self.manifest()
        self.assertEqual([(o["n"], o["status"]) for o in man["ops"]],
                         [(1, "done"), (2, "in_flight"), (3, "queued"), (4, "queued")])
        self.assertIn("active_update", man)
        self.assertEqual(man["plan_sha256"], self.change_set()["base_plan_sha256"])


class RemovedEntries(_UpdatableRun):
    """A DELETE moves its entry out of the active section into `removed[]`, so nothing
    afterwards reads the run as torn down."""

    def _delete_the_free_shipping_offer(self):
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != self.offer_keys[1]]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertTrue(self.change_set()["has_deletes"])
        oid = self.ids("offers")[self.offer_keys[1]]
        self.assertEqual(self.update("--allow-delete"), 0, self.out())
        return oid

    def test_a_delete_moves_the_entry_into_removed(self):
        oid = self._delete_the_free_shipping_offer()
        man = self.manifest()
        self.assertEqual([e["key"] for e in man["offers"]], [self.offer_keys[0]])
        self.assertEqual([(r["section"], r["key"], r["id"], r["status"]) for r in man["removed"]],
                         [("offers", self.offer_keys[1], oid, "deleted")])
        self.assertEqual(ca.torn_down_entries(ca.Manifest.load(self.manifest_path)), [])
        self.assertEqual(self.sent(),
                         [("DELETE", f"/api/admin/campaigns/{self.cid}/offers/{oid}/")])

    def test_verify_passes_after_a_delete(self):
        self._delete_the_free_shipping_offer()
        cart_t = FakeTransport({("POST", "/api/v1/carts/calculate/"):
                                lambda path, body: (200, {"total": "0.00", "lines": []})})
        cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=cart_t, clock=FakeClock(), sleep=lambda s: None)
        self.t = DynamicTransport(self.disc, self.state)
        admin = make_client(self.t)
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "Client", lambda *a, **k: cart), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            rc = ca.main(["verify", "--manifest", str(self.manifest_path),
                          "--plan", str(self.base_path)])
        report = json.loads((self.dir / "verify-report.json").read_text())
        self.assertEqual((rc, report["result"]), (0, "PASS"), json.dumps(report, indent=1))

    def test_a_second_update_runs_on_the_reduced_run(self):
        self._delete_the_free_shipping_offer()
        cand = self.plan_now()
        cand["campaign"]["name"] = "Second update"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.plan_now()["campaign"]["name"], "Second update")
        self.assertEqual(len(self.manifest()["history"]), 2)


class ActiveUpdate(_UpdatableRun):
    """While an update is journalled and unfinished, every other command refuses and
    says how to finish it."""

    def setUp(self):
        super().setUp()
        self.edited()
        with self.assertRaises(_Interrupted):
            self.update(transport=InterruptingTransport(self.disc, self.state, stop_after=2))
        man = self.manifest()
        # op 3 was saved in flight before its request left, which is the whole point:
        # a resume can tell "never sent" from "may have landed".
        self.assertEqual([(o["n"], o["status"]) for o in man["ops"]],
                         [(1, "done"), (2, "done"), (3, "in_flight"), (4, "queued")])
        self.assertIn("active_update", man)

    def _main(self, argv, transport=None):
        return self._run_with(transport or DynamicTransport(self.disc, self.state), argv)

    def test_diff_refuses(self):
        self.assertEqual(self.diff(self.cand(), plan_path=self.dir / "again.json"), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())

    def test_verify_refuses(self):
        self.assertEqual(self._main(["verify", "--manifest", str(self.manifest_path),
                                     "--plan", str(self.base_path)]), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())

    def test_teardown_refuses(self):
        self.assertEqual(self._main(["teardown", "--manifest", str(self.manifest_path),
                                     "--plan", str(self.base_path), "--yes"]), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.sent(), [])

    def test_apply_resume_refuses(self):
        self.assertEqual(self._main(["apply", "--plan", str(self.base_path), "--yes",
                                     "--plan-sha256", ca.sha256_file(self.base_path),
                                     "--resume", str(self.manifest_path),
                                     "--out", str(self.dir)]), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.sent(), [])

    def test_update_without_resume_or_settle_refuses(self):
        self.assertEqual(self.update(), 2, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertIn("close it with update --settle", self.out())
        self.assertEqual(self.sent(), [])

class _CreatedRun(_UpdatableRun):
    """A run this skill created: `origin: created`, so teardown and apply --resume are
    in scope next to diff and update, and verify has real landed rows to price."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.state = fresh_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "run"
        self.dir.mkdir(parents=True)
        self.lines = []
        plan = ca.recommend(self.disc, ns(name="Bracelet - test", exit_code="BRACELET10"))
        self.base_path = self.dir / "campaign-plan.json"
        ca.atomic_write_json(self.base_path, plan)
        self.manifest_path = self.dir / "run-manifest.json"
        self.assertEqual(self._run(["apply", "--plan", str(self.base_path), "--yes",
                                    "--plan-sha256", ca.sha256_file(self.base_path),
                                    "--out", str(self.dir)]), 0, self.out())
        self.base = json.loads(self.base_path.read_text())
        self.cid = self.manifest()["campaign"]["id"]
        self.offer_keys = [o["key"] for o in self.base["offers"]]

    def _cart(self):
        def calc(path, body):
            qty = sum(l["quantity"] for l in body["lines"])
            unit = ca.landed_unit(Decimal("49.95"), Decimal({1: 50, 2: 55, 3: 60}[qty]), None)
            if body.get("vouchers"):
                unit = ca.landed_unit(unit, Decimal(10), None)
            total = unit * qty + Decimal("6.95")
            return 200, {"total": str(total), "subtotal": str(unit * qty), "lines": []}
        t = FakeTransport({("POST", "/api/v1/carts/calculate/"): calc})
        return ca.Client(ca.CART_API_ORIGIN, "campaignkey", auth_scheme="raw",
                         send_version_header=False, transport=t, clock=FakeClock(),
                         sleep=lambda s: None)

    def verify(self, plan=None):
        self.t = DynamicTransport(self.disc, self.state)
        admin, cart = make_client(self.t), self._cart()
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "Client", lambda *a, **k: cart), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            rc = ca.main(["verify", "--manifest", str(self.manifest_path),
                          "--plan", str(plan or self.base_path)])
        return rc

    def verify_report(self):
        return json.loads((self.dir / "verify-report.json").read_text())

    def teardown_run(self):
        return self._run(["teardown", "--manifest", str(self.manifest_path),
                          "--plan", str(self.base_path), "--yes"])

    def rename_in_the_dashboard(self):
        """What a dashboard edit looks like to this run: the campaign, one package and
        one offer renamed under it, with the candidate untouched."""
        self.state["campaigns"][self.cid]["name"] = "Dashboard renamed"
        pid = self.ids("packages")["hero-23"]
        self.state["packages"][self.cid][pid]["name"] = "Renamed hero - v23"
        oid = self.ids("offers")[self.offer_keys[0]]
        self.state["offers"][self.cid][oid]["name"] = "Renamed offer"
        return pid, oid


class PreservedThroughUpdate(_CreatedRun):
    """The three-way merge's other half: what the store changed and the candidate did
    not is written into the plan and into the manifest, so the run is in step again."""

    def test_a_dashboard_rename_is_preserved_then_the_manifest_is_refreshed(self):
        pid, oid = self.rename_in_the_dashboard()
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertEqual(len(cs["preserved"]), 3, cs["preserved"])
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [])
        man = self.manifest()
        # the names the run records are the live ones now, not what the plan used to say
        self.assertEqual(man["campaign"]["name"], "Dashboard renamed")
        self.assertEqual(next(e for e in man["packages"] if e["id"] == pid)["name"],
                         "Renamed hero - v23")
        self.assertEqual(next(e for e in man["offers"] if e["id"] == oid)["name"],
                         "Renamed offer")
        plan = self.plan_now()
        self.assertEqual(plan["campaign"]["name"], "Dashboard renamed")
        self.assertEqual(plan["packages"][0]["name"], "Renamed hero")  # the suffix is stripped
        # in step: the same candidate diffs to nothing, and verify and teardown both
        # read the run back against what is live
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        self.assertIn("no changes", self.out())
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))
        self.assertEqual(self.teardown_run(), 0, self.out())
        self.assertEqual(self.state["campaigns"], {})


class PlanPromotion(_CreatedRun):
    """Promotion in 4 steps, interrupted at every boundary and recovered through the
    real entry points with no hand edits."""

    def crash(self, target, *, before=False, when=None):
        """Replace one engine primitive so one call dies the way a killed process
        does: `before` skips the work, otherwise the work lands first."""
        real = getattr(ca, target)
        seen = {"n": 0}

        def wrapper(*a, **kw):
            seen["n"] += 1
            if when is None or when(seen["n"], a, kw):
                if before:
                    raise _Interrupted(f"{target} died before call {seen['n']}")
                real(*a, **kw)
                raise _Interrupted(f"{target} died after call {seen['n']}")
            return real(*a, **kw)
        return mock.patch.object(ca, target, wrapper)

    def crash_on_save(self, *, before_the_last=False):
        """Kill the run at one of the 2 manifest writes promotion makes: the one that
        records `pending_promotion` (step 2), or the one that clears it (step 4)."""
        real = ca.Manifest.save
        state = {"saw_pending": False}

        def wrapper(man):
            pending = "pending_promotion" in man.data
            if before_the_last and state["saw_pending"] and not pending:
                raise _Interrupted("died before the manifest that ends the promotion")
            real(man)
            if pending:
                state["saw_pending"] = True
                if not before_the_last:
                    raise _Interrupted("died after the manifest that commits the promotion")
        return mock.patch.object(ca.Manifest, "save", wrapper)

    def one_rename(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed by the plan"
        self.assertEqual(self.diff(cand), 0, self.out())
        return self.change_set()

    def resume(self):
        return self.update("--resume")

    def assertInProgress(self):
        """Before the manifest commits to the new plan the update is simply unfinished:
        every other command says so, and --resume finishes it."""
        man = self.manifest()
        self.assertIn("active_update", man)
        self.assertEqual(man["plan_sha256"], self.change_set()["base_plan_sha256"])
        self.assertEqual(self.verify(), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.teardown_run(), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.resume(), 0, self.out())
        self.assertFinished()

    def assertFinished(self):
        cs = self.change_set()
        man = self.manifest()
        self.assertNotIn("active_update", man)
        self.assertNotIn("pending_promotion", man)
        self.assertEqual(man["plan_sha256"], cs["merged_plan_sha256"])
        self.assertEqual(ca.sha256_file(self.base_path), cs["merged_plan_sha256"])
        self.assertEqual(self.plan_now()["campaign"]["name"], "Renamed by the plan")
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))
        self.assertEqual(self.teardown_run(), 0, self.out())

    def test_interrupted_before_the_first_step(self):
        self.one_rename()
        with self.crash("promote_plan", before=True), self.assertRaises(_Interrupted):
            self.update()
        self.assertInProgress()

    def test_interrupted_after_the_old_plan_was_archived(self):
        self.one_rename()
        old = self.manifest()["plan_sha256"]
        with self.crash("archive_bytes", when=lambda n, a, k: n == 1), \
                self.assertRaises(_Interrupted):
            self.update()
        self.assertEqual(ca.sha256_file(self.dir / f"campaign-plan.{old[:8]}.json"), old)
        self.assertNotIn("pending_promotion", self.manifest())
        self.assertInProgress()

    def test_interrupted_after_the_manifest_committed_to_the_new_plan(self):
        cs = self.one_rename()
        with self.crash_on_save(), self.assertRaises(_Interrupted):
            self.update()
        man = self.manifest()
        # the manifest is on the new plan, the file on disk is still the old one
        self.assertEqual(man["plan_sha256"], cs["merged_plan_sha256"])
        self.assertEqual(man["pending_promotion"],
                         {"archived": cs["merged_plan_file"], "sha256": cs["merged_plan_sha256"]})
        self.assertEqual(ca.sha256_file(self.base_path), cs["base_plan_sha256"])
        # verify alone recovers it: the plan is read only after the manifest is opened
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))
        self.assertFinished()

    def test_interrupted_after_the_plan_file_was_replaced(self):
        cs = self.one_rename()
        with self.crash_on_save(before_the_last=True), self.assertRaises(_Interrupted):
            self.update()
        man = self.manifest()
        self.assertIn("pending_promotion", man)
        self.assertEqual(ca.sha256_file(self.base_path), cs["merged_plan_sha256"])
        self.assertEqual(self.teardown_run(), 0, self.out())  # recovery, then the real work
        self.assertNotIn("pending_promotion", self.manifest())
        self.assertNotIn("active_update", self.manifest())

    def test_a_completed_update_needs_no_recovery(self):
        self.one_rename()
        self.assertEqual(self.update(), 0, self.out())
        self.assertFinished()

    def test_a_merged_plan_re_indented_by_hand_keeps_its_own_bytes(self):
        cs = self.one_rename()
        merged = self.dir / cs["merged_plan_file"]
        merged.write_text(json.dumps(json.loads(merged.read_text()), indent=4) + "\n")
        want = merged.read_bytes()
        self.tamper(lambda c: c.update(merged_plan_sha256=ca.sha256_file(merged)))
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.base_path.read_bytes(), want)
        self.assertEqual(self.manifest()["plan_sha256"], ca.sha256_file(merged))
        self.assertEqual(merged.read_bytes(), want, "the archive is never consumed or moved")
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))

    def test_a_plan_outside_the_run_directory_and_the_archive_both_stay_intact(self):
        cs = self.one_rename()
        merged = self.dir / cs["merged_plan_file"]
        outside = Path(self.tmp.name) / "elsewhere" / cs["merged_plan_file"]
        outside.parent.mkdir(parents=True)
        shutil.copy(merged, outside)
        self.assertEqual(self.update(plan=outside), 0, self.out())
        self.assertEqual(outside.read_bytes(), merged.read_bytes())
        self.assertEqual(self.base_path.read_bytes(), merged.read_bytes())
        self.assertTrue(merged.exists())

    def test_history_grows_by_one_per_update(self):
        for name in ("First", "Second"):
            cand = self.cand()
            cand["campaign"]["name"] = name
            self.assertEqual(self.diff(cand), 0, self.out())
            self.assertEqual(self.update(), 0, self.out())
        man = self.manifest()
        self.assertEqual(len(man["history"]), 2)
        first, second = man["history"]
        self.assertEqual(first["to_plan_sha256"], second["from_plan_sha256"])
        self.assertEqual(second["to_plan_sha256"], man["plan_sha256"])
        # every plan this run has had is still on disk, byte for byte
        for h in man["history"]:
            self.assertEqual(ca.sha256_file(self.dir / h["archived"]), h["from_plan_sha256"])

class _ResumableRun(_UpdatableRun):
    """An adopted run plus the two ways a run stops: killed before a write leaves, and
    killed after it landed with the answer lost."""

    def interrupt(self, *extra, stop_after=None, lose=()):
        t = InterruptingTransport(self.disc, self.state, stop_after=stop_after,
                                  lose_response_on=lose)
        with self.assertRaises(_Interrupted):
            self.update(*extra, transport=t)
        return t

    def journal(self):
        return [(o["n"], o["status"]) for o in self.manifest().get("ops") or []]

    def refuse_resume(self, *extra, needle, code=1):
        self.assertEqual(self.update("--resume", *extra), code, self.out())
        self.assertIn(needle, self.out())
        self.assertEqual(self.sent(), [], "a refused resume sends nothing")
        return self.out()

    def four_patches(self):
        """campaign, package, shipping and offer, one PATCH each: 4 ops, 4 writes."""
        return self.edited()

    def with_a_new_shipping_method(self):
        """campaign PATCH, package PATCH, shipping PATCH, shipping POST, offer PATCH."""
        cand = self.cand()
        cand["campaign"]["name"] = "Edited campaign"
        cand["packages"][0]["price"] = "29.95"
        cand["shipping_methods"][0]["price"] = "7.95"
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        cand["offers"][0]["benefit"]["value"] = "60.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual([o["method"] for o in self.change_set()["ops"]],
                         ["PATCH", "PATCH", "PATCH", "POST", "PATCH"])
        return cand


class UpdateResume(_ResumableRun):
    """Finishing an update that stopped: what landed is read off the store, what never
    went out is sent, and anything else refuses before a single request."""

    def test_a_post_whose_response_was_lost_is_claimed_after_a_full_read_back(self):
        self.with_a_new_shipping_method()
        self.interrupt(lose=[4])
        self.assertEqual(self.journal(), [(1, "done"), (2, "done"), (3, "done"), (4, "in_flight"),
                                          (5, "queued")])
        self.assertNotIn("express", self.ids("shipping_methods"))
        self.assertEqual(self.update("--resume"), 0, self.out())
        sid = self.ids("shipping_methods")["express"]
        self.assertEqual(self.state["shipping-methods"][self.cid][sid]["shipping_method"], "express")
        e = next(x for x in self.manifest()["shipping_methods"] if x["key"] == "express")
        self.assertEqual((e["status"], e.get("reconciled")), ("created", True))
        # the lost POST is not sent again: only the op after it goes out
        self.assertEqual(self.sent(),
                         [("PATCH", f"/api/admin/campaigns/{self.cid}/offers/"
                                    f"{self.ids('offers')[self.offer_keys[0]]}/")])
        self.assertNotIn("active_update", self.manifest())

    def test_a_same_named_object_with_different_contents_is_never_claimed(self):
        cand = self.cand()
        cand["packages"].append({"key": "pkg-900", "role": "bump", "name": "New Bump",
                                 "variant_title": "v900", "product_id": 22,
                                 "product_variant_ids": [900], "price": "9.95"})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual([o["method"] for o in self.change_set()["ops"]], ["POST"])
        self.interrupt(lose=[1])
        pid = next(i for i, x in self.state["packages"][self.cid].items()
                   if x["product_variant_id"] == 900)
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "11.95"
        before = self.manifest_path.read_bytes()
        self.refuse_resume(needle="was not created by this run")
        self.assertIn("price", self.out())
        self.assertEqual(self.manifest_path.read_bytes(), before,
                         "a refused claim leaves the manifest byte-identical")
        self.assertNotIn("pkg-900", self.ids("packages"))

    def test_the_candidate_lookup_is_read_only(self):
        # the lookup apply --resume and update --resume share never marks anything
        man = ca.Manifest.load(self.manifest_path)
        before = self.manifest_path.read_bytes()
        t = DynamicTransport(self.disc, self.state)
        hits = ca.find_pending_candidates(make_client(t), man, self.base, "packages", "pkg-801")
        self.assertEqual([x["id"] for x in hits], [self.ids("packages")["pkg-801"]])
        self.assertEqual(self.manifest_path.read_bytes(), before)
        self.assertEqual([(c[0], c[1]) for c in t.calls if c[0] != "GET"], [])

    def test_a_patch_that_never_went_out_is_re_sent(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight"), (3, "queued"),
                                          (4, "queued")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual([m for m, _p in self.sent()], ["PATCH", "PATCH", "PATCH"])
        self.assertEqual(self.plan_now()["packages"][0]["price"], "29.95")
        self.assertNotIn("active_update", self.manifest())

    def test_a_rename_whose_response_was_lost_resumes_through_after(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed once"
        cand["packages"][0]["name"] = "Renamed package"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.interrupt(lose=[2])
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(), [], "an op already at its after value is not re-sent")
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Renamed package")
        self.assertEqual(self.manifest()["packages"][0]["name"], "Renamed package - v801")

    def test_a_shipping_price_patch_whose_response_was_lost_resumes_through_after(self):
        cand = self.cand()
        cand["shipping_methods"][0]["price"] = "8.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.interrupt(lose=[1])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(), [])
        e = self.manifest()["shipping_methods"][0]
        self.assertEqual((e["price"], e["shipping_method"]), ("8.95", "standard"))

    def test_a_delete_that_landed_with_its_answer_lost_counts_the_404_as_done(self):
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != self.offer_keys[1]]
        self.assertEqual(self.diff(cand), 0, self.out())
        oid = self.ids("offers")[self.offer_keys[1]]
        self.interrupt("--allow-delete", lose=[1])
        self.assertNotIn(oid, self.state["offers"][self.cid])
        e = next(x for x in self.manifest()["offers"] if x["key"] == self.offer_keys[1])
        self.assertEqual(e["status"], "deleting")
        self.assertEqual(self.update("--resume", "--allow-delete"), 0, self.out())
        self.assertEqual(self.sent(), [], "the object is already gone: nothing is re-sent")
        man = self.manifest()
        self.assertEqual([x["key"] for x in man["offers"]], [self.offer_keys[0]])
        self.assertEqual([(r["key"], r["id"]) for r in man["removed"]],
                         [(self.offer_keys[1], oid)])
        self.assertEqual(ca.torn_down_entries(ca.Manifest.load(self.manifest_path)), [])

    def test_a_missing_object_no_delete_op_names_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        pid = self.ids("packages")["pkg-802"]  # no op touches it
        del self.state["packages"][self.cid][pid]
        self.refuse_resume(needle="is no longer on the campaign")
        self.assertIn("pkg-802", self.out())

    def test_a_dashboard_edit_to_a_campaign_setting_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        self.state["campaigns"][self.cid]["language"] = "de"
        self.refuse_resume(needle="campaign campaign: language is")

    def test_a_dashboard_edit_to_a_discount_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        oid = self.ids("offers")[self.offer_keys[0]]  # op 4, still queued
        self.state["offers"][self.cid][oid]["benefit"]["value"] = "70.00"
        self.refuse_resume(needle=f"offers {self.offer_keys[0]}: benefit_value is")

    def test_a_dashboard_edit_to_a_scope_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        oid = self.ids("offers")[self.offer_keys[1]]  # no op touches it
        self.state["offers"][self.cid][oid]["condition"]["packages"] = [
            {"id": self.ids("packages")["pkg-802"]}]
        self.refuse_resume(needle=f"offers {self.offer_keys[1]}: package_keys is")

    def test_a_dashboard_edit_to_an_object_no_pending_op_touches_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        pid = self.ids("packages")["pkg-802"]
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "13.95"
        self.refuse_resume(needle="packages pkg-802: price is")

    def test_an_object_created_in_the_dashboard_that_matches_a_queued_post_is_unmanaged(self):
        self.with_a_new_shipping_method()
        self.interrupt(stop_after=2)
        self.assertEqual(self.journal()[2:], [(3, "in_flight"), (4, "queued"), (5, "queued")])
        # someone adds exactly what op 4 would have created, by hand
        self.state["seq"] += 1
        self.state["shipping-methods"][self.cid][self.state["seq"]] = {
            "id": self.state["seq"], "shipping_method": "express",
            "prices": [{"currency": "USD", "price": "12.95"}]}
        self.refuse_resume(needle="object(s) this run does not own")
        self.assertIn("express", self.out())
        self.assertNotIn("express", self.ids("shipping_methods"))

    def test_a_claimed_package_lets_its_image_put_and_a_scoped_offer_finish(self):
        cand = self.cand()
        cand["packages"].append({"key": "pkg-900", "role": "bump", "name": "New Bump",
                                 "variant_title": "v900", "product_id": 22,
                                 "product_variant_ids": [900], "price": "9.95",
                                 "image": {"src": "https://cdn.example/new.png"}})
        cand["offers"].append({"key": "offer-new", "name": "New Offer", "offer_type": "offer",
                               "code": None,
                               "condition": {"type": "any", "value": None,
                                             "package_keys": ["pkg-900"]},
                               "benefit": {"type": "package_percentage", "value": "10.00",
                                           "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual([o["method"] for o in self.change_set()["ops"]],
                         ["POST", "PUT", "POST"])
        self.interrupt(lose=[1])  # the package exists; its answer never came back
        self.assertEqual(self.journal(), [(1, "in_flight"), (2, "queued"), (3, "queued")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        pid = self.ids("packages")["pkg-900"]
        # the image route is rebuilt from the id the claim recovered, and the offer's
        # scope is built with it too
        self.assertEqual(self.sent(),
                         [("PUT", f"/api/admin/campaigns/{self.cid}/packages/{pid}/image/"),
                          ("POST", f"/api/admin/campaigns/{self.cid}/offers/")])
        offer_post = self.bodies()[-1][2]
        self.assertEqual(offer_post["condition"]["package_ids"], [pid])
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual(self.manifest()["packages"][-1]["image_src"],
                         "https://cdn.example/new.png")

    def test_a_deleting_entry_no_op_of_this_change_set_names_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        man = self.manifest()
        man["packages"][1]["status"] = "deleting"
        ca.atomic_write_json(self.manifest_path, man)
        self.refuse_resume(needle="this run was torn down")

    def test_resuming_with_another_change_set_hash_refuses(self):
        self.four_patches()
        self.interrupt(stop_after=1)
        other = self.dir / "change-set.other.json"
        shutil.copy(self.dir / CHANGE_SET, other)
        cs = json.loads(other.read_text())
        cs["created_at"] = "2026-01-01T00:00:00Z"  # same run, different bytes
        ca.atomic_write_json(other, cs)
        self.assertEqual(self.update("--resume", cs=other), 2, self.out())
        self.assertIn("finish that one first", self.out())
        self.assertEqual(self.sent(), [])

    def test_resume_without_an_update_in_progress_refuses(self):
        self.four_patches()
        self.assertEqual(self.update("--resume"), 2, self.out())
        self.assertIn("no update is in progress", self.out())
        self.assertEqual(self.sent(), [])


class ImageResume(_ResumableRun):
    """The one op whose result the store makes up: a thumbnail URL nobody can predict."""

    def _image_put(self):
        man = self.manifest()
        man["packages"][0].update(image_status="set", image_src="https://cdn.example/old.png",
                                  image=CATALOGUE_IMAGE)
        ca.atomic_write_json(self.manifest_path, man)
        cand = self.cand()
        cand["packages"][0]["image"] = {"src": "https://cdn.example/new.png"}
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PUT", "packages", "pkg-801")])
        return self.ids("packages")["pkg-801"]

    def test_a_put_that_never_went_out_is_re_sent(self):
        pid = self._image_put()
        self.interrupt(stop_after=0)
        self.assertEqual(self.journal(), [(1, "in_flight")])
        self.assertEqual(self.manifest()["packages"][0]["image"], CATALOGUE_IMAGE)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(),
                         [("PUT", f"/api/admin/campaigns/{self.cid}/packages/{pid}/image/")])
        e = self.manifest()["packages"][0]
        self.assertEqual((e["image_status"], e["image_src"], e["image"]),
                         ("set", "https://cdn.example/new.png", OVERRIDE_IMAGE))

    def test_a_put_whose_answer_was_lost_records_the_live_url_and_is_not_re_sent(self):
        self._image_put()
        self.interrupt(lose=[1])
        self.assertEqual(self.state["packages"][self.cid][self.ids("packages")["pkg-801"]]["image"],
                         OVERRIDE_IMAGE)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(), [], "the live thumbnail is the receipt; nothing is re-sent")
        e = self.manifest()["packages"][0]
        self.assertEqual((e["image_status"], e["image_src"], e["image"]),
                         ("set", "https://cdn.example/new.png", OVERRIDE_IMAGE))
        self.assertNotIn("active_update", self.manifest())

class Settle(_CreatedRun):
    """Closing an update that cannot finish: no writes, a plan that describes what
    actually landed, and the run unlocked for a fresh diff."""

    def _stopped_on_a_400(self):
        """op 1 (the campaign rename) lands, op 2 (the package rename) is refused."""
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed campaign"
        cand["packages"][0]["name"] = "Renamed hero"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual([(o["method"], o["section"]) for o in self.change_set()["ops"]],
                         [("PATCH", "campaign"), ("PATCH", "packages")])
        pid = self.ids("packages")["hero-23"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{pid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertIn("returned 400", self.out())
        self.assertEqual([(o["n"], o["status"]) for o in self.manifest()["ops"]],
                         [(1, "done"), (2, "in_flight")])
        return cand

    def test_the_other_commands_refuse_until_it_is_settled(self):
        self._stopped_on_a_400()
        self.assertEqual(self.verify(), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.teardown_run(), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.diff(self.cand(), plan_path=self.dir / "again.json"), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())

    def test_settle_promotes_a_plan_holding_the_op_that_landed_and_sends_nothing(self):
        cand = self._stopped_on_a_400()
        cs = self.change_set()
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertEqual(self.sent(), [], "settle never writes to the store")
        plan = self.plan_now()
        self.assertEqual(plan["campaign"]["name"], "Renamed campaign")
        self.assertEqual(plan["packages"][0]["name"], self.base["packages"][0]["name"])
        man = self.manifest()
        self.assertNotIn("active_update", man)
        self.assertNotIn("pending_promotion", man)
        self.assertEqual(man["plan_sha256"], ca.sha256_file(self.base_path))
        self.assertNotEqual(man["plan_sha256"], cs["merged_plan_sha256"])
        h = man["history"][-1]
        self.assertEqual((h["change_set_sha256"], h["settled"]),
                         (ca.sha256_file(self.dir / CHANGE_SET), True))
        self.assertEqual([(o["n"], o["outcome"]) for o in h["ops"]],
                         [(1, "applied"), (2, "not applied")])
        self.assertIn("1 of 2 op(s) had landed", self.out())
        # verify reads the settled plan back clean, and a fresh diff proposes the rest
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PATCH", "packages", "hero-23")])

    def test_an_in_flight_op_at_neither_state_refuses(self):
        self._stopped_on_a_400()
        pid = self.ids("packages")["hero-23"]
        self.state["packages"][self.cid][pid]["name"] = "Something else entirely"
        self.assertEqual(self.update("--settle"), 1, self.out())
        self.assertIn("at neither the value before this update nor the value after it", self.out())
        self.assertEqual(self.sent(), [])
        self.assertIn("active_update", self.manifest())

    def test_a_delete_that_never_landed_puts_its_entry_back(self):
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != "exit-pop"]
        self.assertEqual(self.diff(cand), 0, self.out())
        oid = self.ids("offers")["exit-pop"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("DELETE", f"/api/admin/campaigns/{self.cid}/offers/{oid}/"): 400})
        self.assertEqual(self.update("--allow-delete", transport=t), 1, self.out())
        self.assertEqual(next(e for e in self.manifest()["offers"]
                              if e["key"] == "exit-pop")["status"], "deleting")
        self.assertEqual(self.update("--settle"), 0, self.out())
        e = next(x for x in self.manifest()["offers"] if x["key"] == "exit-pop")
        self.assertEqual(e["status"], "created")
        self.assertEqual(ca.torn_down_entries(ca.Manifest.load(self.manifest_path)), [])
        self.assertEqual([o["key"] for o in self.plan_now()["offers"]],
                         [o["key"] for o in self.base["offers"]])
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))


class RunLock(_UpdatableRun):
    """One process per run directory, for the whole life of a command that can write
    it. A stale lock is the operator's call, never the engine's."""

    def lock_path(self):
        return self.dir / ca.RUN_LOCK_NAME

    def hold(self, pid=424242):
        ca.atomic_write_bytes(self.lock_path(),
                              json.dumps({"pid": pid, "started_at": "2026-10-08T00:00:00Z"})
                              .encode())

    def test_a_second_update_while_one_holds_the_lock_refuses(self):
        self.edited()
        self.hold()
        self.assertEqual(self.update(), 1, self.out())
        self.assertIn(str(self.lock_path()), self.out())
        self.assertIn("pid 424242", self.out())
        self.assertEqual(self.sent(), [])
        self.assertTrue(self.lock_path().exists(), "a stale lock is never removed automatically")

    def test_teardown_during_an_update_refuses(self):
        self.hold(pid=99999)
        rc = self._run(["teardown", "--manifest", str(self.manifest_path),
                        "--plan", str(self.base_path), "--yes"])
        self.assertEqual(rc, 1, self.out())
        self.assertIn("holds this run directory", self.out())
        self.assertIn("pid 99999", self.out())
        self.assertEqual([(c[0], c[1]) for c in self.t.calls if c[0] != "GET"], [])

    def test_the_lock_is_released_after_a_run_and_after_an_error(self):
        self.edited()
        self.assertFalse(self.lock_path().exists())
        pid = self.ids("packages")["pkg-801"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{pid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertFalse(self.lock_path().exists(), "an error still releases the lock")
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertFalse(self.lock_path().exists())

    def test_the_read_only_commands_are_not_locked(self):
        self.hold()
        self.assertEqual(self._run(["plan", "--plan", str(self.base_path)]), 0, self.out())
        self.assertIn("What this campaign holds", self.out())


class EndToEndUpdate(unittest.TestCase):
    """Both whole paths, through ca.main only: a campaign that already existed, and one
    this skill created, each edited and read back."""

    def setUp(self):
        self.disc = load_fixture("discovery.json")
        self.state = fresh_state()
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name) / "run"
        self.lines = []

    def tearDown(self):
        self.tmp.cleanup()

    def out(self):
        return "\n".join(self.lines)

    def main(self, argv, cart=None):
        self.t = DynamicTransport(self.disc, self.state)
        admin = make_client(self.t)
        self.lines = []
        with mock.patch.object(ca, "_client_for", lambda slug: admin), \
                mock.patch.object(ca, "print",
                                  lambda *a, **k: self.lines.append(" ".join(map(str, a)))):
            if cart is None:
                return ca.main(argv)
            with mock.patch.object(ca, "Client", lambda *a, **k: cart):
                return ca.main(argv)

    def writes(self):
        return [(c[0], c[1]) for c in self.t.calls if c[0] != "GET"]

    def flat_cart(self):
        t = FakeTransport({("POST", "/api/v1/carts/calculate/"):
                           lambda path, body: (200, {"total": "0.00", "lines": []})})
        return ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=t, clock=FakeClock(), sleep=lambda s: None)

    def approve(self, d):
        cs = json.loads((d / CHANGE_SET).read_text())
        return (["update", "--plan", str(d / cs["merged_plan_file"]),
                 "--manifest", str(d / "run-manifest.json"),
                 "--change-set", str(d / CHANGE_SET), "--yes",
                 "--change-set-sha256", ca.sha256_file(d / CHANGE_SET)], cs)

    def test_adopt_diff_update_verify_on_a_dashboard_campaign(self):
        cid = seed_live_campaign(self.state)
        d = self.dir
        self.assertEqual(self.main(["adopt", "--store", "teststore", "--campaign", str(cid),
                                    "--out", str(d)]), 0, self.out())
        self.assertEqual(self.writes(), [], "adopt is read-only")
        plan = json.loads((d / "campaign-plan.json").read_text())
        cand = json.loads(json.dumps(plan))
        cand["campaign"]["statement_descriptor"] = "NEWDESC"
        cand["packages"][1]["price"] = "14.95"
        cand["offers"][0]["benefit"]["value"] = "60.00"
        nxt = d / "campaign-plan.next.json"
        nxt.write_text(json.dumps(cand, indent=2) + "\n")
        self.assertEqual(self.main(["diff", "--plan", str(nxt),
                                    "--manifest", str(d / "run-manifest.json")]), 0, self.out())
        self.assertEqual(self.writes(), [], "diff is read-only")
        argv, cs = self.approve(d)
        self.assertEqual([(o["method"], o["section"], o["key"]) for o in cs["ops"]],
                         [("PATCH", "campaign", "campaign"),
                          ("PATCH", "packages", "pkg-802"),
                          ("PATCH", "offers", cs["ops"][2]["key"])])
        self.assertEqual(self.main(argv), 0, self.out())
        self.assertEqual(self.writes(), [(o["method"], o["path"]) for o in cs["ops"]])
        self.assertEqual(ca.sha256_file(d / "campaign-plan.json"), cs["merged_plan_sha256"])
        live = self.state["campaigns"][cid]
        self.assertEqual(live["statement_descriptor"], "NEWDESC")
        self.assertEqual(self.main(["verify", "--manifest", str(d / "run-manifest.json"),
                                    "--plan", str(d / "campaign-plan.json")],
                                   cart=self.flat_cart()), 0, self.out())
        report = json.loads((d / "verify-report.json").read_text())
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        # and the run is in step: nothing left to diff
        again = d / "campaign-plan.again.json"
        again.write_bytes((d / "campaign-plan.json").read_bytes())
        self.assertEqual(self.main(["diff", "--plan", str(again),
                                    "--manifest", str(d / "run-manifest.json")]), 0, self.out())
        self.assertIn("no changes", self.out())

    def test_recommend_apply_diff_update_verify_teardown_on_a_created_campaign(self):
        d = self.dir
        d.mkdir(parents=True)
        disc_path = d / "discovery.json"
        ca.atomic_write_json(disc_path, self.disc)
        self.assertEqual(self.main(["recommend", "--discovery", str(disc_path), "--hero", "22",
                                    "--ctc", "low", "--anchor-price", "49.95",
                                    "--shipping", "standard:6.95", "--name", "Bracelet - test",
                                    "--exit-code", "BRACELET10"]), 0, self.out())
        plan_path = d / "campaign-plan.json"
        self.assertEqual(self.main(["apply", "--plan", str(plan_path), "--yes",
                                    "--plan-sha256", ca.sha256_file(plan_path)]), 0, self.out())
        man_path = d / "run-manifest.json"
        cand = json.loads(plan_path.read_text())
        cand["campaign"]["name"] = "Bracelet - renamed"
        cand["offers"] = [o for o in cand["offers"] if o["key"] != "exit-pop"]
        cand["voucher_codes"] = []
        nxt = d / "campaign-plan.next.json"
        nxt.write_text(json.dumps(cand, indent=2) + "\n")
        self.assertEqual(self.main(["diff", "--plan", str(nxt), "--manifest", str(man_path)]),
                         0, self.out())
        argv, cs = self.approve(d)
        self.assertEqual([(o["method"], o["section"], o["key"]) for o in cs["ops"]],
                         [("PATCH", "campaign", "campaign"), ("DELETE", "offers", "exit-pop")])
        self.assertEqual(self.main(argv), 2, self.out())  # the DELETE needs approving
        self.assertIn("--allow-delete", self.out())
        self.assertEqual(self.main(argv + ["--allow-delete"]), 0, self.out())
        self.assertEqual(self.writes(), [(o["method"], o["path"]) for o in cs["ops"]])
        man = json.loads(man_path.read_text())
        self.assertEqual([r["key"] for r in man["removed"]], ["exit-pop"])
        self.assertEqual(man["campaign"]["name"], "Bracelet - renamed")

        def calc(path, body):
            qty = sum(l["quantity"] for l in body["lines"])
            unit = ca.landed_unit(Decimal("49.95"), Decimal({1: 50, 2: 55, 3: 60}[qty]), None)
            return 200, {"total": str(unit * qty + Decimal("6.95")), "lines": []}
        cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=FakeTransport({("POST", "/api/v1/carts/calculate/"): calc}),
                         clock=FakeClock(), sleep=lambda s: None)
        self.assertEqual(self.main(["verify", "--manifest", str(man_path),
                                    "--plan", str(plan_path)], cart=cart), 0, self.out())
        report = json.loads((d / "verify-report.json").read_text())
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        self.assertEqual(self.main(["teardown", "--manifest", str(man_path),
                                    "--plan", str(plan_path), "--yes"]), 0, self.out())
        self.assertEqual(self.state["campaigns"], {})
        self.assertEqual([m for m, _p in self.writes()],
                         ["DELETE"] * len(self.writes()))

# --------------------------------------------------------------------------- #
# Review round four: the resume and settle paths, and multi-currency prices
# --------------------------------------------------------------------------- #

class ResumeAfterAPostThatLanded(_ResumableRun):
    """A POST that landed is journalled `done` and its object is in the manifest's
    active section from that moment. Re-binding the change set has to read it as the
    object this update created, not as a key a POST would duplicate, or the run is
    stuck: neither `--resume` nor `--settle` can get past the binding check."""

    def _with_a_new_package(self, image=None, cand=None):
        cand = cand if cand is not None else self.cand()
        p = {"key": "pkg-903", "role": "bump", "name": "Extra", "variant_title": "v903",
             "product_id": 22, "product_variant_ids": [903], "price": "9.95"}
        if image:
            p["image"] = {"src": image}
        cand["packages"].append(p)
        return cand

    def package_post_then_an_offer_patch(self):
        cand = self._with_a_new_package()
        cand["offers"][0]["benefit"]["value"] = "60.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("POST", "packages", "pkg-903"),
                                      ("PATCH", "offers", self.offer_keys[0])])
        return cand

    def assertFinished(self, *keys):
        man = self.manifest()
        self.assertNotIn("active_update", man)
        self.assertNotIn("ops", man)
        for key in keys:
            self.assertIsNotNone(self.ids("packages").get(key), man)

    def test_resume_finishes_after_a_package_post_that_landed(self):
        self.package_post_then_an_offer_patch()
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertFinished("pkg-903")
        self.assertEqual(self.plan_now()["offers"][0]["benefit"]["value"], "60.00")
        self.assertEqual(len(self.state["packages"][self.cid]), 3)

    def test_resume_finishes_when_the_op_behind_the_post_lost_its_answer(self):
        self.package_post_then_an_offer_patch()
        self.interrupt(lose=[2])
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(), [], "the PATCH had landed: resume re-sends nothing")
        self.assertFinished("pkg-903")
        self.assertEqual(self.plan_now()["offers"][0]["benefit"]["value"], "60.00")

    def test_resume_finishes_after_the_op_behind_the_post_was_rejected(self):
        self.package_post_then_an_offer_patch()
        oid = self.ids("offers")[self.offer_keys[0]]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/offers/{oid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        # the cause is fixed on the store's side: the same change set resumes
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertFinished("pkg-903")
        self.assertEqual(self.plan_now()["offers"][0]["benefit"]["value"], "60.00")

    def test_settle_closes_a_run_after_a_package_post_that_landed(self):
        self.package_post_then_an_offer_patch()
        oid = self.ids("offers")[self.offer_keys[0]]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/offers/{oid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        plan = self.plan_now()
        self.assertEqual([p["key"] for p in plan["packages"]], ["pkg-801", "pkg-802", "pkg-903"])
        self.assertEqual(plan["offers"][0]["benefit"]["value"], "55.00")

    def test_resume_finishes_after_a_shipping_post_that_landed(self):
        cand = self.cand()
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        cand["offers"][0]["benefit"]["value"] = "60.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("POST", "shipping_methods", "express"),
                                      ("PATCH", "offers", self.offer_keys[0])])
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        self.assertIsNotNone(self.ids("shipping_methods").get("express"))

    def test_resume_finishes_after_an_offer_post_that_landed(self):
        cand = self.cand()
        for n in ("one", "two"):
            cand["offers"].append({
                "key": f"offer-new-{n}", "name": f"Extra {n}", "offer_type": "offer",
                "code": None,
                "condition": {"type": "any", "value": None, "package_keys": ["pkg-802"]},
                "benefit": {"type": "package_percentage", "value": "15.00",
                            "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("POST", "offers", "offer-new-one"),
                                      ("POST", "offers", "offer-new-two")])
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual(sorted(x["name"] for x in self.state["offers"][self.cid].values())[:2],
                         ["Dashboard Bracelet - Buy 2", "Dashboard Bracelet - Free Shipping"])
        self.assertEqual(len(self.state["offers"][self.cid]), 4)

    def test_resume_finishes_the_image_put_of_a_package_it_just_created(self):
        self._with_a_new_package(image="https://cdn.example/extra.png")
        cand = self._with_a_new_package(image="https://cdn.example/extra.png")
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("POST", "packages", "pkg-903"),
                                      ("PUT", "packages", "pkg-903")])
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        pid = self.ids("packages")["pkg-903"]
        # the override is what the operator approved: the catalogue image a create
        # attaches is not the PUT landing
        self.assertEqual(self.state["packages"][self.cid][pid]["image"], OVERRIDE_IMAGE)
        e = next(x for x in self.manifest()["packages"] if x["key"] == "pkg-903")
        self.assertEqual((e["image_status"], e["image_src"]),
                         ("set", "https://cdn.example/extra.png"))


class OwnershipAndTheOpCommitTogether(_ResumableRun):
    """Recording that this run owns an object and marking the op that produced it
    `done` is ONE manifest save, for every op kind. Two saves left a window where the
    manifest owned a POST's object while its op was still `in_flight`, and both
    `--resume` and `--settle` read that as a duplicate and refused: the run was stuck
    with no way out that did not involve editing the manifest by hand."""

    def crash_committing(self, n):
        """Kill the run at the manifest write that marks op `n` done, the way a killed
        process does: the data in hand never reaches the file."""
        real = ca.Manifest.save

        def wrapper(man):
            if any(o.get("n") == n and o.get("status") == "done"
                   for o in man.data.get("ops") or []):
                raise _Interrupted(f"the process died committing op {n}")
            real(man)
        return mock.patch.object(ca.Manifest, "save", wrapper)

    def kill_at(self, n, *extra):
        with self.crash_committing(n), self.assertRaises(_Interrupted):
            self.update(*extra)
        self.assertEqual(dict(self.journal())[n], "in_flight",
                         f"op {n} is the one left in flight\n{self.out()}")
        return self.manifest()

    def new_package(self, cand=None, *, image=None):
        cand = cand if cand is not None else self.cand()
        p = {"key": "pkg-903", "role": "bump", "name": "Extra", "variant_title": "v903",
             "product_id": 22, "product_variant_ids": [903], "price": "9.95"}
        if image:
            p["image"] = {"src": image}
        cand["packages"].append(p)
        return cand

    def assertClosed(self):
        man = self.manifest()
        self.assertNotIn("active_update", man)
        self.assertNotIn("ops", man)

    # -- POST: the op kind the two-save window actually blocked --------------- #

    def test_a_package_post_killed_at_its_commit_resumes(self):
        self.assertEqual(self.diff(self.new_package()), 0, self.out())
        self.assertEqual(self.ops(), [("POST", "packages", "pkg-903")])
        man = self.kill_at(1)
        self.assertNotIn("pkg-903", {e["key"] for e in man["packages"]},
                         "the ownership never landed on its own")
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertIsNotNone(self.ids("packages").get("pkg-903"))
        self.assertEqual(len(self.state["packages"][self.cid]), 3,
                         "the package the interrupted POST created is claimed, not duplicated")

    def test_a_package_post_killed_at_its_commit_settles(self):
        self.assertEqual(self.diff(self.new_package()), 0, self.out())
        self.kill_at(1)
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual([p["key"] for p in self.plan_now()["packages"]],
                         ["pkg-801", "pkg-802", "pkg-903"])

    def test_a_shipping_post_killed_at_its_commit_resumes(self):
        cand = self.cand()
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.kill_at(1)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertIsNotNone(self.ids("shipping_methods").get("express"))
        self.assertEqual(len(self.state["shipping-methods"][self.cid]), 2)

    def test_an_offer_post_killed_at_its_commit_resumes(self):
        cand = self.cand()
        cand["offers"].append({
            "key": "offer-new", "name": "Extra", "offer_type": "offer", "code": None,
            "condition": {"type": "any", "value": None, "package_keys": ["pkg-802"]},
            "benefit": {"type": "package_percentage", "value": "15.00", "price_rounding": None}})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.kill_at(1)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertIsNotNone(self.ids("offers").get("offer-new"))
        self.assertEqual(len(self.state["offers"][self.cid]), 3)

    # -- a manifest already in the state the old engine could leave ---------- #

    def _stranded_post(self):
        """The manifest an engine that saved the ownership before the completed op
        leaves behind: the POST's object is `created` with its live id, and the op
        that created it is still `in_flight`."""
        self.assertEqual(self.diff(self.new_package()), 0, self.out())
        man = self.kill_at(1)
        pid = next(x["id"] for x in self.state["packages"][self.cid].values()
                   if x["product_variant_id"] == 903)
        man["packages"].append({"key": "pkg-903", "status": "created", "id": pid,
                                "name": "Extra - v903", "product_variant_id": 903,
                                "intent": None})
        ca.atomic_write_json(self.manifest_path, man)
        return pid

    def test_resume_reads_a_stranded_post_as_landed_not_as_a_duplicate(self):
        pid = self._stranded_post()
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertEqual(self.sent(), [], "the POST had landed: nothing is re-sent")
        self.assertClosed()
        self.assertEqual(self.ids("packages")["pkg-903"], pid)
        self.assertEqual(len(self.state["packages"][self.cid]), 3)
        self.assertEqual([p["key"] for p in self.plan_now()["packages"]],
                         ["pkg-801", "pkg-802", "pkg-903"])

    def test_settle_closes_a_stranded_post(self):
        pid = self._stranded_post()
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertEqual(self.sent(), [])
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual(self.ids("packages")["pkg-903"], pid)
        self.assertEqual([p["key"] for p in self.plan_now()["packages"]],
                         ["pkg-801", "pkg-802", "pkg-903"])

    def test_a_stranded_post_whose_object_holds_other_values_is_still_refused(self):
        """Tolerating the window is not a licence to claim anything: the read-back is
        the same full one the lost-response path does, and a mismatch stops the run."""
        self._stranded_post()
        pid = next(x["id"] for x in self.state["packages"][self.cid].values()
                   if x["product_variant_id"] == 903)
        self.state["packages"][self.cid][pid]["prices"][0]["price"] = "19.95"
        self.assertEqual(self.update("--resume"), 1, self.out())
        self.assertIn("was not created by this run", self.out())

    # -- the other op kinds, for the same window ----------------------------- #

    def test_a_campaign_patch_killed_at_its_commit_resumes(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed campaign"
        self.assertEqual(self.diff(cand), 0, self.out())
        man = self.kill_at(1)
        self.assertEqual(man["campaign"]["name"], "Dashboard Bracelet",
                         "the refreshed name never landed without its op")
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertEqual(self.plan_now()["campaign"]["name"], "Renamed campaign")
        self.assertEqual(self.manifest()["campaign"]["name"], "Renamed campaign")

    def test_a_package_patch_killed_at_its_commit_resumes(self):
        cand = self.cand()
        cand["packages"][0]["name"] = "Renamed hero"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.kill_at(1)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Renamed hero")

    def test_an_offer_patch_killed_at_its_commit_resumes(self):
        cand = self.cand()
        cand["offers"][0]["name"] = "Renamed offer"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.kill_at(1)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        self.assertEqual(self.plan_now()["offers"][0]["name"], "Renamed offer")

    def test_an_offer_delete_killed_at_its_commit_resumes(self):
        key = self.offer_keys[1]
        oid = self.ids("offers")[key]
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != key]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "offers", key)])
        man = self.kill_at(1, "--allow-delete")
        self.assertEqual([e["key"] for e in man["offers"] if e["status"] == "deleting"], [key],
                         "the entry is still `deleting`: the receipt never landed alone")
        self.assertNotIn(oid, self.state["offers"][self.cid])
        self.assertEqual(self.update("--resume", "--allow-delete"), 0, self.out())
        self.assertClosed()
        self.assertEqual([r["key"] for r in self.manifest()["removed"]], [key])
        self.assertEqual([o["key"] for o in self.plan_now()["offers"]], [self.offer_keys[0]])

    def test_a_package_delete_killed_at_its_commit_resumes(self):
        key = "pkg-802"
        cand = self.cand()
        cand["packages"] = [p for p in cand["packages"] if p["key"] != key]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "packages", key)])
        self.kill_at(1, "--allow-delete")
        self.assertEqual(self.update("--resume", "--allow-delete"), 0, self.out())
        self.assertClosed()
        self.assertEqual([r["key"] for r in self.manifest()["removed"]], [key])
        self.assertEqual([p["key"] for p in self.plan_now()["packages"]], ["pkg-801"])

    def test_a_shipping_delete_killed_at_its_commit_settles(self):
        """A plan needs one shipping method, so the method this deletes is one an
        earlier update added."""
        cand = self.cand()
        cand["shipping_methods"].append({"shipping_method": "express", "price": "12.95"})
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.update(), 0, self.out())
        cand = self.plan_now()
        cand["shipping_methods"] = [s for s in cand["shipping_methods"]
                                    if s["shipping_method"] != "express"]
        self.assertEqual(self.diff(cand, plan_path=self.dir / "campaign-plan.two.json"),
                         0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "shipping_methods", "express")])
        self.kill_at(1, "--allow-delete")
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual([s["shipping_method"] for s in self.plan_now()["shipping_methods"]],
                         ["standard"])
        self.assertEqual([r["key"] for r in self.manifest()["removed"]], ["express"])

    def test_an_image_put_killed_at_its_commit_resumes(self):
        man = self.manifest()
        man["packages"][0]["image_src"] = "https://cdn.example/old.png"
        ca.atomic_write_json(self.manifest_path, man)
        cand = self.cand()
        cand["packages"][0]["image"] = {"src": "https://cdn.example/new.png"}
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PUT", "packages", "pkg-801")])
        man = self.kill_at(1)
        e = next(x for x in man["packages"] if x["key"] == "pkg-801")
        self.assertEqual(e["image_status"], "pending",
                         "the image receipt never landed without its op")
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        e = next(x for x in self.manifest()["packages"] if x["key"] == "pkg-801")
        self.assertEqual((e["image_status"], e["image_src"]),
                         ("set", "https://cdn.example/new.png"))

    def test_the_post_of_a_package_and_its_image_put_both_resume(self):
        self.assertEqual(self.diff(self.new_package(image="https://cdn.example/extra.png")),
                         0, self.out())
        self.assertEqual(self.ops(), [("POST", "packages", "pkg-903"),
                                      ("PUT", "packages", "pkg-903")])
        self.kill_at(2)
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertClosed()
        e = next(x for x in self.manifest()["packages"] if x["key"] == "pkg-903")
        self.assertEqual((e["image_status"], e["image_src"]),
                         ("set", "https://cdn.example/extra.png"))
        self.assertEqual(self.state["packages"][self.cid][e["id"]]["image"], OVERRIDE_IMAGE)


class ResumeAfterARenameThatLanded(_ResumableRun):
    """A rename that landed moves the live name, and the manifest records the live
    form. Normalising the live name back into plan space has to read the NEW name, or
    a resume reads the applied rename as drift and refuses what it did itself."""

    def rename_then_a_later_op(self):
        cand = self.cand()
        cand["packages"][0]["name"] = "Renamed hero"
        cand["offers"][0]["benefit"]["value"] = "60.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PATCH", "packages", "pkg-801"),
                                      ("PATCH", "offers", self.offer_keys[0])])
        return cand

    def test_resume_finishes_after_a_package_rename_landed(self):
        self.rename_then_a_later_op()
        self.interrupt(stop_after=1)
        self.assertEqual(self.journal(), [(1, "done"), (2, "in_flight")])
        pid = self.ids("packages")["pkg-801"]
        self.assertEqual(self.state["packages"][self.cid][pid]["name"], "Renamed hero - v801")
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Renamed hero")
        self.assertEqual(self.plan_now()["offers"][0]["benefit"]["value"], "60.00")

    def test_settle_carries_the_rename_that_landed_into_the_plan(self):
        self.rename_then_a_later_op()
        oid = self.ids("offers")[self.offer_keys[0]]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/offers/{oid}/"): 400})
        self.assertEqual(self.update(transport=t), 1, self.out())
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Renamed hero")
        self.assertEqual(self.plan_now()["offers"][0]["benefit"]["value"], "55.00")

    def test_a_completed_rename_leaves_nothing_for_the_next_diff(self):
        cand = self.rename_then_a_later_op()
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Renamed hero")
        again = self.dir / "campaign-plan.again.json"
        self.assertEqual(self.diff(self.plan_now(), plan_path=again), 0, self.out())
        self.assertIn("no changes", self.out())

    def test_resume_finishes_after_a_campaign_and_an_offer_rename_landed(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed campaign"
        cand["offers"][0]["name"] = "Renamed offer"
        cand["offers"][1]["benefit"]["value"] = "90.00"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("PATCH", "campaign", "campaign"),
                                      ("PATCH", "offers", self.offer_keys[0]),
                                      ("PATCH", "offers", self.offer_keys[1])])
        self.interrupt(stop_after=2)
        self.assertEqual(self.journal(), [(1, "done"), (2, "done"), (3, "in_flight")])
        self.assertEqual(self.update("--resume"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        plan = self.plan_now()
        self.assertEqual(plan["campaign"]["name"], "Renamed campaign")
        self.assertEqual([o["name"] for o in plan["offers"]][0], "Renamed offer")
        self.assertEqual(plan["offers"][1]["benefit"]["value"], "90.00")


class AdvisoryRowsOfADeletedObject(_CreatedRun):
    """`landed_prices` and `voucher_codes` are the operator's receipt of what the cart
    engine should charge, never desired state. Deleting the offer or package a row
    names leaves that row stale, and neither the merge nor a settle may fail
    validation over one: the row is dropped and said out loud."""

    def _delete_an_offer_a_landed_row_names(self):
        cand = self.cand()
        cand["offers"] = [o for o in cand["offers"] if o["key"] != "tier-3"]
        return cand

    def test_diff_drops_a_landed_row_naming_a_deleted_offer_and_warns(self):
        self.assertEqual(self.diff(self._delete_an_offer_a_landed_row_names()), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "offers", "tier-3")])
        cs = self.change_set()
        self.assertEqual([l["tier"] for l in cs["merged_plan"]["landed_prices"]],
                         ["Buy 1", "Buy 2"])
        self.assertTrue(any("tier-3" in w and "landed_prices" in w for w in cs["warnings"]),
                        cs["warnings"])

    def test_update_of_that_change_set_promotes_the_pruned_plan(self):
        self.assertEqual(self.diff(self._delete_an_offer_a_landed_row_names()), 0, self.out())
        self.assertEqual(self.update("--allow-delete"), 0, self.out())
        self.assertEqual([l["tier"] for l in self.plan_now()["landed_prices"]], ["Buy 1", "Buy 2"])
        self.assertEqual(self.verify(), 0, json.dumps(self.verify_report(), indent=1))

    def test_settle_after_a_delete_landed_and_a_later_op_failed(self):
        cand = self._delete_an_offer_a_landed_row_names()
        cand["packages"][0]["price"] = "59.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "offers", "tier-3"),
                                      ("PATCH", "packages", "hero-23")])
        pid = self.ids("packages")["hero-23"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{pid}/"): 400})
        self.assertEqual(self.update("--allow-delete", transport=t), 1, self.out())
        self.assertEqual([(o["n"], o["status"]) for o in self.manifest()["ops"]],
                         [(1, "done"), (2, "in_flight")])
        self.assertEqual(self.update("--settle"), 0, self.out())
        self.assertNotIn("active_update", self.manifest())
        plan = self.plan_now()
        self.assertEqual([o["key"] for o in plan["offers"]], ["tier-1", "tier-2", "exit-pop"])
        self.assertEqual([l["tier"] for l in plan["landed_prices"]], ["Buy 1", "Buy 2"])
        self.assertEqual(plan["packages"][0]["price"], self.base["packages"][0]["price"])
        self.assertIn("landed_prices", self.out())

    def test_settle_after_a_package_and_its_offers_were_deleted(self):
        # every offer scopes every hero package, so the package can only go once they
        # have: the DELETEs land and the price PATCH behind them is rejected
        cand = self.cand()
        cand["packages"] = [p for p in cand["packages"] if p["key"] != "hero-26"]
        cand["offers"] = []
        cand["packages"][0]["price"] = "59.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops()[-1], ("PATCH", "packages", "hero-23"))
        self.assertIn(("DELETE", "packages", "hero-26"), self.ops())
        pid = self.ids("packages")["hero-23"]
        t = DynamicTransport(self.disc, self.state, fail_on={
            ("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{pid}/"): 400})
        self.assertEqual(self.update("--allow-delete", transport=t), 1, self.out())
        self.assertEqual(self.update("--settle"), 0, self.out())
        plan = self.plan_now()
        self.assertEqual([p["key"] for p in plan["packages"]], ["hero-23", "hero-24", "hero-25"])
        self.assertEqual(plan["packages"][0]["price"], self.base["packages"][0]["price"])
        self.assertEqual(plan["landed_prices"], [])
        self.assertEqual(plan["voucher_codes"], [])
        self.assertEqual(plan["offers"], [])
        self.assertIn("voucher_codes row for offer exit-pop", self.out())

    def test_a_row_that_still_resolves_is_never_dropped(self):
        cand = self.cand()
        cand["packages"][0]["price"] = "59.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual([l["tier"] for l in cs["merged_plan"]["landed_prices"]],
                         ["Buy 1", "Buy 2", "Buy 3"])
        self.assertEqual(cs["merged_plan"]["voucher_codes"], self.base["voucher_codes"])
        self.assertEqual(cs["warnings"], [])

    def test_a_landed_row_naming_a_key_that_never_existed_is_still_invalid(self):
        cand = self.cand()
        cand["landed_prices"][0]["offer_key"] = "tier-9"
        self.assertEqual(self.diff(cand), 1, self.out())
        self.assertIn("offer_key 'tier-9' does not resolve", self.out())


class MultiCurrencyPriceWarning(_AdoptedRun):
    """A price PATCH names the campaign currency only, and the update endpoint leaves
    the currencies it does not name alone. On a campaign that prices in more than one
    currency the diff has to say so: the other prices stay as they are on the store."""

    seed_kw = {"extra_currency": "EUR"}

    def test_a_package_price_change_warns_about_the_other_currencies(self):
        cand = self.cand()
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"][0]["body"],
                         {"prices": [{"currency": "USD", "price": "29.95",
                                      "price_recurring": None}]})
        self.assertTrue(any("pkg-801" in w and "EUR" in w for w in cs["warnings"]),
                        cs["warnings"])

    def test_a_shipping_price_change_warns_too(self):
        cand = self.cand()
        cand["shipping_methods"][0]["price"] = "8.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertTrue(any("standard" in w and "EUR" in w
                            for w in self.change_set()["warnings"]),
                        self.change_set()["warnings"])

    def test_a_change_that_is_not_a_price_warns_about_nothing(self):
        cand = self.cand()
        cand["packages"][0]["name"] = "Renamed hero"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.change_set()["warnings"], [])


class PlanOnlyChanges(_UpdatableRun):
    """A change set is worth writing whenever the MERGED plan differs from the base
    plan, ops or no ops. Deciding it by counting ops and preserved rows discarded the
    merged plan whenever the only differences were ones no request carries: a
    plan-only field such as `role`, a value the candidate and the dashboard reached
    independently, an advisory row dropped with the object it priced. The canonical
    plan was then left stale and a role correction could not land on its own."""

    def plan_only(self):
        return self.change_set()["plan_only"]

    def test_a_role_only_correction_lands_with_nothing_sent(self):
        cand = self.cand()
        self.assertEqual(cand["packages"][1]["role"], "bump")
        cand["packages"][1]["role"] = "upsell"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.plan_only(), ['package pkg-802.role: "bump" -> "upsell"'])
        self.assertIn("Plan only, nothing sent", self.out())
        old_sha = self.manifest()["plan_sha256"]
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [], "a role correction sends nothing")
        self.assertEqual(self.plan_now()["packages"][1]["role"], "upsell")
        man = self.manifest()
        self.assertEqual(man["plan_sha256"], self.change_set()["merged_plan_sha256"])
        self.assertNotIn("active_update", man)
        self.assertEqual(ca.sha256_file(self.dir / f"campaign-plan.{old_sha[:8]}.json"), old_sha)
        # and the correction is the baseline now: the next diff has nothing to say
        self.assertEqual(self.diff(self.plan_now(), plan_path=self.dir / "again.json"),
                         0, self.out())
        self.assertIn("no changes", self.out())

    def test_a_convergent_campaign_rename_is_written_with_no_requests(self):
        self.state["campaigns"][self.cid]["name"] = "Both sides agree"
        cand = self.cand()
        cand["campaign"]["name"] = "Both sides agree"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.plan_only(),
                         ['campaign.name: "Dashboard Bracelet" -> "Both sides agree"'])
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.plan_now()["campaign"]["name"], "Both sides agree")
        self.assertEqual(self.diff(self.plan_now(), plan_path=self.dir / "again.json"),
                         0, self.out())
        self.assertIn("no changes", self.out())

    def test_a_convergent_package_rename_is_written_with_no_requests(self):
        pid = self.ids("packages")["pkg-801"]
        self.state["packages"][self.cid][pid]["name"] = "Agreed hero - v801"
        cand = self.cand()
        cand["packages"][0]["name"] = "Agreed hero"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.plan_only(),
                         ['package pkg-801.name: "Photo Bracelet" -> "Agreed hero"'])
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.plan_now()["packages"][0]["name"], "Agreed hero")
        self.assertEqual(self.diff(self.plan_now(), plan_path=self.dir / "again.json"),
                         0, self.out())
        self.assertIn("no changes", self.out())

    def test_a_convergent_offer_rename_is_written_with_no_requests(self):
        key = self.offer_keys[0]
        oid = self.ids("offers")[key]
        self.state["offers"][self.cid][oid]["name"] = "Agreed offer"
        cand = self.cand()
        cand["offers"][0]["name"] = "Agreed offer"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [])
        self.assertEqual(self.plan_only(),
                         [f'offer {key}.name: "Dashboard Bracelet - Buy 2" -> "Agreed offer"'])
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.plan_now()["offers"][0]["name"], "Agreed offer")
        self.assertEqual(self.diff(self.plan_now(), plan_path=self.dir / "again.json"),
                         0, self.out())
        self.assertIn("no changes", self.out())

    def test_a_value_preserved_from_the_store_is_not_listed_twice(self):
        """A preserved value moves the plan without a request too, so it would show up
        here. It has its own block, which says more about it, so it does not."""
        self.state["campaigns"][self.cid]["name"] = "Renamed in the dashboard"
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertEqual(cs["plan_only"], [])
        self.assertTrue(any("Renamed in the dashboard" in x for x in cs["preserved"]),
                        cs["preserved"])
        self.assertNotIn("Plan only, nothing sent", self.out())
        self.assertIn("Kept from the store", self.out())

    def test_a_field_a_request_does_carry_is_not_called_plan_only(self):
        cand = self.cand()
        cand["campaign"]["name"] = "Renamed"
        cand["packages"][0]["price"] = "29.95"
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.plan_only(), [])

    def test_an_unchanged_copy_of_an_adopted_plan_is_still_no_changes(self):
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        self.assertIn("no changes", self.out())
        self.assertFalse((self.dir / CHANGE_SET).exists())

    def test_a_candidate_that_drops_image_src_is_written_and_then_goes_quiet(self):
        """The merged plan no longer carries the image, so it differs from the base
        plan and is written. The warning is the point of the change set: there is no
        delete-image route, so the store keeps the picture. Once the merged plan is
        the baseline the next diff has nothing to write, and the warning is still
        said out loud rather than swallowed with the change set."""
        # the run already PUT this image: the base plan asks for it and the manifest
        # records the src that landed
        base = json.loads(self.base_path.read_text())
        base["packages"][0]["image"] = {"src": "https://cdn.example/old.png"}
        ca.atomic_write_json(self.base_path, base)
        man = self.manifest()
        man["packages"][0]["image_src"] = "https://cdn.example/old.png"
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        self.assertEqual(self.diff(self.cand()), 0, self.out())
        self.assertIn("no changes", self.out(), "an unchanged image src is not a change")

        cand = self.cand()
        cand["packages"][0].pop("image")
        self.assertEqual(self.diff(cand, plan_path=self.dir / "dropped.json"), 0, self.out())
        cs = self.change_set()
        self.assertEqual(cs["ops"], [])
        self.assertEqual(cs["plan_only"],
                         ['package pkg-801.image.src: "https://cdn.example/old.png" -> null'])
        self.assertTrue(any("dropped image.src" in w for w in cs["warnings"]), cs["warnings"])
        self.assertEqual(self.update(), 0, self.out())
        self.assertEqual(self.sent(), [])
        self.assertNotIn("image", self.plan_now()["packages"][0])
        # the manifest still records what this run PUT, which is the truth about the
        # store, and the next diff is quiet about it apart from the same warning
        e = next(x for x in self.manifest()["packages"] if x["key"] == "pkg-801")
        self.assertEqual(e["image_src"], "https://cdn.example/old.png")
        self.assertEqual(self.diff(self.plan_now(), plan_path=self.dir / "again.json"),
                         0, self.out())
        self.assertIn("no changes", self.out())
        self.assertIn("dropped image.src", self.out())

    def test_an_advisory_row_dropped_with_its_offer_is_a_plan_only_difference(self):
        key = self.offer_keys[0]
        base = json.loads(self.base_path.read_text())
        base["voucher_codes"] = [{"offer_key": key, "code": "KEEP10"}]
        ca.atomic_write_json(self.base_path, base)
        man = self.manifest()
        man["plan_sha256"] = ca.sha256_file(self.base_path)
        ca.atomic_write_json(self.manifest_path, man)
        cand = json.loads(self.base_path.read_text())
        cand["offers"] = [o for o in cand["offers"] if o["key"] != key]
        self.assertEqual(self.diff(cand), 0, self.out())
        self.assertEqual(self.ops(), [("DELETE", "offers", key)])
        self.assertEqual(self.change_set()["plan_only"],
                         ['voucher_codes: [{"offer_key": "%s", "code": "KEEP10"}] -> []' % key])


if __name__ == "__main__":
    unittest.main()
