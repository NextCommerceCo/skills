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
                upsell=None, free_shipping=False, free_shipping_min_qty=None, rounding=None,
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

    def test_high_ctc_with_bumps_and_upsells(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          bump=["7:9.95"], upsell=["16:39.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertFalse([o for o in plan["offers"] if o["key"].startswith("tier-")])
        roles = sorted(p["role"] for p in plan["packages"])
        self.assertEqual(roles, ["bump", "hero", "hero", "hero", "hero", "upsell"])
        up = next(o for o in plan["offers"] if o["key"].startswith("upsell-"))
        self.assertEqual((up["offer_type"], up["code"], up["condition"]["package_keys"]), ("voucher", "MUSICPHOTOMAGNET50", ["upsell-16"]))
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
                            ns(bump=["24:19.95"], upsell=["25:39.95:20"]))
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
            state["seq"] += 1
            item = dict(body, id=state["seq"])
            if kind == "packages":
                item["product_variant_id"] = body["product_variant_ids"][0]
                item["name"] = body["name"] + " - v" + str(item["product_variant_id"])  # API appends the variant
                item["prices"] = [{"currency": "USD", "price": body["price"], "price_recurring": None}]
                item["product_purchase_availability"] = "available"
                # The real PackageCreateSerializer fetches the catalogue product/variant
                # image and attaches it at create time, so a created package normally
                # already carries one. Mirror that, or every verify PASS assertion breaks.
                item["image"] = state.get("catalogue_image", CATALOGUE_IMAGE)
            if kind == "offers":
                item["condition"] = {"type": body["condition"]["type"], "all_packages": False,
                                     "packages": [{"id": i} for i in body["condition"]["package_ids"]]}
                item["benefit"] = dict(body["benefit"])
            if kind == "shipping-methods":
                item["prices"] = [{"currency": "USD", "price": body["price"]}]
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
        self.routes, self.child_create, self.child_list = store_routes(disc, state)

    def __call__(self, req, timeout):
        method = req.get_method()
        path = req.full_url.split(".29next.store", 1)[1]
        body = json.loads(req.data.decode()) if req.data else None
        self.calls.append((method, path, body, dict(req.headers)))
        base = path.split("?")[0]
        parts = base.strip("/").split("/")
        key = (method, base)
        if key in self.fail_on:
            st = self.fail_on.pop(key)
            return st, {}, json.dumps({"detail": "injected"})
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
                if method == "DELETE":
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
    return {"seq": 100, "campaigns": {}, "packages": {}, "shipping-methods": {}, "offers": {}}


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
        self.assertEqual(up[0][2], ["MUSICPHOTOMAGNET50"])

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

    def _run(self, plan, after_apply=None, during_probes=None, ship_override=None, **engine):
        """apply the plan to a fake store, then verify it against _engine.
        after_apply(state, man, campaign_id, transport) can change the store or the
        journal in between, the way a dashboard edit or an interrupted run would;
        during_probes(state, campaign_id) runs on every calculate call. The engine
        prices each created shipping method by id at its planned price, except the
        shipping keys in ship_override, which it charges at the given price."""
        with tempfile.TemporaryDirectory() as d:
            pp = Path(d) / "plan.json"; ca.atomic_write_json(pp, plan); sha = ca.sha256_file(pp)
            state = fresh_state()
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
            upsell = body.get("vouchers") == ["MUSICPHOTOMAGNET50"]
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


LADDER = ["standard:9.99:ship-1", "standard:12.99:ship-2", "standard:14.99:ship-3", "standard:16.99:ship-4"]
RUNG = {"Buy 1": "9.99", "Buy 2": "12.99", "Buy 3": "14.99", "Buy 4": "16.99"}


def ladder_plan(disc, **kw):
    """Four tiers on one store code, each tier row priced with its own rung."""
    plan = ca.recommend(disc, ns(tiers="50,55,60,65", shipping=LADDER, **kw))
    for i, row in enumerate(r for r in plan["landed_prices"] if r["kind"] == "tier"):
        row["shipping_key"] = f"ship-{i + 1}"
    return plan


class TieredShippingLadder(unittest.TestCase):
    """Several campaign shipping methods on the same store code at different
    prices, identified by a plan-level key, each landed row priced with its own."""

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

    def test_same_code_different_prices_validates(self):
        plan = ladder_plan(self.disc)
        self.assertEqual(ca.validate_plan(plan), [])
        ships = [r[2] for r in ca.render_requests(plan) if r[1].endswith("/shipping-methods/")]
        self.assertEqual(ships, [{"shipping_method": "standard", "price": p} for p in ("9.99", "12.99", "14.99", "16.99")])

    def test_same_code_same_price_rejects(self):
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][1]["price"] = "9.99"
        errs = ca.validate_plan(plan)
        self.assertTrue(any("duplicates code 'standard' at 9.99" in e for e in errs), errs)

    def test_duplicate_keys_reject(self):
        plan = ladder_plan(self.disc)
        plan["shipping_methods"][1]["key"] = "ship-1"
        self.assertTrue(any("duplicate shipping key 'ship-1'" in e for e in ca.validate_plan(plan)))
        # two code-only entries on one code: the key defaults to the code and collides
        plan = ladder_plan(self.disc)
        for s in plan["shipping_methods"]:
            s.pop("key")
        for row in plan["landed_prices"]:
            row.pop("shipping_key", None)
        errs = ca.validate_plan(plan)
        self.assertTrue(any("give each entry on code 'standard' its own key" in e for e in errs), errs)
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
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(shipping=["standard:9.99", "standard:12.99"]))
        self.assertIn("give each entry its own key", str(cm.exception))
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.recommend(self.disc, ns(shipping=["standard:9.99:ship-1", "standard:12.99:ship-1"]))
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
                         [("standard", "9.99"), ("standard", "12.99"), ("standard", "14.99"), ("standard", "16.99")])
        self.assertEqual(len({e["id"] for e in man.data["shipping_methods"]}), 4)
        self.assertTrue(all(c[2]["shipping_method"] == "standard" for c in self._ship_posts(t)))

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
        state["shipping-methods"][cid][999] = {"id": 999, "shipping_method": "standard", "price": "12.99",
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
        self.assertIn("  standard  16.99  key ship-4", lines)
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

    def test_upsell_at_other_price_gets_its_own_package(self):
        plan = ca.recommend(self.disc, ns(upsell=["23:39.95:50"]))
        pkg = next(p for p in plan["packages"] if p["key"] == "upsell-23")
        self.assertEqual(pkg["price"], "39.95")
        self.assertTrue(any("different price" in r for r in plan["rationale"]))
        self.assertFalse(any("also applies at checkout" in h for h in plan["handoff"]))

    def test_reuse_matches_variant_and_price_together(self):
        plan = ca.recommend(self.disc, ns(bump=["23:39.95"], upsell=["23:39.95:50"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertNotIn("upsell-23", [p["key"] for p in plan["packages"]])
        (up,) = self._upsell_offers(plan)
        self.assertEqual(up["condition"]["package_keys"], ["bump-23"])
        plan2 = ca.recommend(self.disc, ns(bump=["23:39.95"], upsell=["23:29.95:50"]))
        self.assertEqual(next(p for p in plan2["packages"] if p["key"] == "upsell-23")["price"], "29.95")

    def test_bump_is_never_merged_into_hero(self):
        plan = ca.recommend(self.disc, ns(bump=["23:49.95"]))
        self.assertIn("bump-23", [p["key"] for p in plan["packages"]])
        self.assertEqual(ca.validate_plan(plan), [])

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
                         ("upsell-15-50", "Music Photo Magnet - 50%", "MUSICPHOTOMAGNET50",
                          ["upsell-16", "upsell-17"]))
        rows = [l for l in plan["landed_prices"] if l["kind"] == "upsell"]
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({r["tier"] for r in rows}), 2)
        self.assertEqual({r["offer_key"] for r in rows}, {"upsell-15-50"})
        ids = {p["key"]: 300 + i for i, p in enumerate(plan["packages"])}
        cases = [c for c in ca._cart_cases_from_plan(plan, ids) if c[0].startswith("Upsell")]
        self.assertEqual(len(cases), 2)
        self.assertTrue(all(c[2] == ["MUSICPHOTOMAGNET50"] for c in cases))

    def test_same_product_two_percentages_two_vouchers(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95",
                                          upsell=["16:39.95:50", "17:39.95:40"]))
        self.assertEqual(ca.validate_plan(plan), [])
        self.assertEqual(sorted(o["code"] for o in self._upsell_offers(plan)),
                         ["MUSICPHOTOMAGNET40", "MUSICPHOTOMAGNET50"])
        tiers = [l["tier"] for l in plan["landed_prices"] if l["kind"] == "upsell"]
        self.assertEqual(len(set(tiers)), 2, tiers)

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
        self.assertTrue(any(p.endswith("?upsell=true") and b.get("vouchers") == ["PHOTOBRACELET50"]
                            for _, p, b, _ in tt.calls))

    def test_hero_reuse_voucher_stacks_at_checkout(self):
        """The disclosed cost of reuse: entered at checkout the upsell code stacks on
        the Buy 1 tier (50% then 50%). The handoff and the doctrine say so."""
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:50"]))
        ids = {p["key"]: 400 + i for i, p in enumerate(plan["packages"])}
        calc = self._engine(plan, ids)
        _, upsell_page = calc("/api/v1/carts/calculate/?upsell=true",
                              {"lines": [{"package_id": ids["hero-23"], "quantity": 1}], "vouchers": ["PHOTOBRACELET50"]})
        _, checkout = calc("/api/v1/carts/calculate/",
                           {"lines": [{"package_id": ids["hero-23"], "quantity": 1}], "vouchers": ["PHOTOBRACELET50"]})
        self.assertEqual((upsell_page["total"], checkout["total"]), ("24.97", "12.48"))

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


if __name__ == "__main__":
    unittest.main()
