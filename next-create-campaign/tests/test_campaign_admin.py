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
                language=None, countries=None, tiers=None, exit=None, exit_code=None, bump=None,
                upsell=None, free_shipping=False, rounding=None, statement_descriptor=None)
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
        for part in ("next-create-campaign.sh metadata --store teststore --apply", "metadata:write", "re-run discover"):
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
        for m in (all_packages, voucher_no_code, count_no_value, bad_decimal, dup_names, qty_pkg, bad_origin, dangling, multi_variant, dup_offer_key):
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
        self.assertEqual(up["offer_key"], "upsell-upsell-16")
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


if __name__ == "__main__":
    unittest.main()
