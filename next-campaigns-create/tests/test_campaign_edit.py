"""Unit tests for the `edit` subcommand of scripts/campaign_admin.py (no network).

An edit changes a campaign this run created, in place. These tests hold it to
the rules teardown already follows (manifest-owned objects only, each read back
and identity-checked) and to its own: one hashed approval gate, a before-image
saved ahead of the first write, a re-read just before and just after every
write, no retries, and a plan and manifest left in line so verify, resume and
teardown keep working.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import test_campaign_admin as base

ca = base.ca
ns = base.ns


def live_engine(state, cid):
    """A fake carts/calculate that prices from what the fake store holds now,
    not from the plan: the highest-percentage live automatic package offer the
    cart meets (none in upsell mode), then any entered live voucher, then
    shipping unless a live 100% shipping offer is met. Because it reads the
    store, verify passing after an edit means the plan and the store agree."""
    def scope(o):
        return {p["id"] for p in o["condition"]["packages"]}

    def calc(path, body):
        upsell = path.endswith("?upsell=true")
        packages = state["packages"][cid]
        live = [o for o in state["offers"].get(cid, {}).values() if o.get("available", True)]
        lines = [(l["package_id"], l["quantity"]) for l in body["lines"]]

        def met(o):
            need = o["condition"].get("value") if o["condition"]["type"] == "count" else 1
            return sum(q for i, q in lines if i in scope(o)) >= (need or 1)

        def pct(o, kind):
            return o.get("offer_type", "offer") == kind and o["benefit"]["type"] == "package_percentage"

        total = Decimal(0)
        for i, q in lines:
            unit = Decimal(next(r["price"] for r in packages[i]["prices"] if r["currency"] == "USD"))
            autos = [] if upsell else [o for o in live if pct(o, "offer") and i in scope(o) and met(o)]
            if autos:
                best = max(autos, key=lambda o: Decimal(o["benefit"]["value"]))
                unit = ca.landed_unit(unit, Decimal(best["benefit"]["value"]), best["benefit"].get("price_rounding"))
            for code in body.get("vouchers", []):
                v = next((o for o in live if pct(o, "voucher") and o.get("code") == code), None)
                if v and i in scope(v) and met(v):
                    unit = ca.landed_unit(unit, Decimal(v["benefit"]["value"]), v["benefit"].get("price_rounding"))
            total += unit * q
        if "shipping_method" in body:
            free = any(o.get("offer_type", "offer") == "offer" and o["benefit"]["type"] == "shipping_percentage"
                       and Decimal(o["benefit"]["value"]) == 100 and met(o) for o in live)
            if not free:
                method = state["shipping-methods"][cid][body["shipping_method"]]
                total += Decimal(next(r["price"] for r in method["prices"] if r["currency"] == "USD"))
        return 200, {"total": str(total), "lines": []}
    return calc


class EditHarness(unittest.TestCase):
    """A campaign applied to the fake store, with the CLI wired to it."""

    RECOMMEND = dict(name="Scope - test", anchor_price="49.99", tiers="45,50,55,60", exit_code="SCOPE10")

    def setUp(self):
        self.disc = base.load_fixture("discovery.json")
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.plan_path = self.dir / "campaign-plan.json"
        self.manifest = self.dir / "run-manifest.json"
        self.original = ca.recommend(self.disc, ns(**self.RECOMMEND))
        ca.atomic_write_json(self.plan_path, self.original)
        self.original_sha = ca.sha256_file(self.plan_path)
        self.state = base.fresh_state()
        self.t = base.DynamicTransport(self.disc, self.state)
        self.man = ca.apply(self.client(), self.original, self.original_sha, self.manifest, None)
        self.cid = self.man.data["campaign"]["id"]
        self.t.calls.clear()
        self.out = []
        self._client_for, self._print = ca._client_for, ca.print
        ca._client_for = lambda slug: self.client()
        ca.print = lambda *a, **k: self.out.append(" ".join(str(x) for x in a))

    def tearDown(self):
        ca._client_for, ca.print = self._client_for, self._print
        self.tmp.cleanup()

    # -- helpers ----------------------------------------------------------- #
    def client(self):
        return base.make_client(self.t)

    def plan(self):
        return ca.load_json(self.plan_path)

    def reload(self):
        self.man = ca.Manifest.load(self.manifest)
        return self.man

    def heroes(self):
        return [p["key"] for p in self.original["packages"] if p["role"] == "hero"]

    def entry_id(self, section, key):
        return self.reload().entry(section, key)["id"]

    def offer(self, key):
        return self.state["offers"][self.cid][self.entry_id("offers", key)]

    def offer_path(self, key):
        return f"/api/admin/campaigns/{self.cid}/offers/{self.entry_id('offers', key)}/"

    def offers_path(self):
        return f"/api/admin/campaigns/{self.cid}/offers/"

    def package(self, key):
        return self.state["packages"][self.cid][self.entry_id("packages", key)]

    def row(self, tier, plan=None):
        return next(l for l in (plan or self.plan())["landed_prices"] if l["tier"] == tier)

    def spec(self, ops, name="campaign-edit.json"):
        p = self.dir / name
        p.write_text(json.dumps({"operations": ops}))
        return p

    def cli(self, *extra):
        return ca.main(["edit", "--manifest", str(self.manifest), "--plan", str(self.plan_path),
                        *[str(x) for x in extra]])

    def sha(self):
        return next(l.split(": ", 1)[1] for l in reversed(self.out) if l.startswith("Edit SHA-256: "))

    def approve(self, *args, live="no"):
        """Preview (exit 2, nothing written), then approve with the hash it printed."""
        before = len(self.writes())
        self.assertEqual(self.cli(*args), 2, self.out[-3:])
        self.assertEqual(len(self.writes()), before)
        extra = ["--live-traffic", live] if live else []
        return self.cli(*args, "--yes", "--edit-sha256", self.sha(), *extra)

    def edit(self, ops, name="campaign-edit.json"):
        return self.approve("--changes", self.spec(ops, name))

    def undo(self, n=1, live="no"):
        return self.approve("--undo", self.dir / f"edit-{n}-receipt.json", live=live)

    def writes(self):
        return [c for c in self.t.calls if c[0] != "GET"]

    def error(self):
        return next(l for l in reversed(self.out) if l.startswith("ERROR: "))

    def verify(self):
        plan, sha = ca.load_json_and_hash(self.plan_path)
        tt = base.FakeTransport({("POST", "/api/v1/carts/calculate/"): live_engine(self.state, self.cid)})
        cart = ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=tt, clock=base.FakeClock(), sleep=lambda s: None)
        return ca.verify(self.client(), cart, self.reload(), plan, sha)

    def assert_in_line(self):
        """Plan and manifest agree, and verify passes against the live store."""
        self.assertNotIn("pending_edit", self.reload().data)
        self.assertEqual(self.man.data["plan_sha256"], ca.sha256_file(self.plan_path))
        report = self.verify()
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        return report

    def dashboard_offer(self, name="Dashboard deal", value="5.00", code=None):
        """An offer someone added in the dashboard: live on the campaign, not in
        the manifest."""
        self.state["seq"] += 1
        oid = self.state["seq"]
        ids = [self.entry_id("packages", k) for k in self.heroes()]
        self.state["offers"][self.cid][oid] = {
            "id": oid, "name": name, "offer_type": "voucher" if code else "offer", "code": code, "available": True,
            "condition": {"type": "any", "value": None, "all_packages": False, "packages": [{"id": i} for i in ids]},
            "benefit": {"type": "package_percentage", "value": value}}
        return oid

    def ladder_ops(self, price="99.98", pcts=(50, 55, 60, 65)):
        return ([{"op": "set_package_price", "package_keys": self.heroes(), "price": price}]
                + [{"op": "set_offer_benefit", "offer_key": f"tier-{i}", "value": str(p)}
                   for i, p in enumerate(pcts, start=1)])


# --------------------------------------------------------------------------- #
class EditGateAndOwnership(EditHarness):
    def test_gate_refuses_without_matching_hash(self):
        sp = self.spec(self.ladder_ops())
        self.assertEqual(self.cli("--changes", sp), 2)
        sha = self.sha()
        self.assertEqual(self.cli("--changes", sp, "--yes", "--edit-sha256", "deadbeef", "--live-traffic", "no"), 2)
        self.assertEqual(self.cli("--changes", sp, "--edit-sha256", sha, "--live-traffic", "no"), 2)
        # the right hash still needs the live-traffic statement
        self.assertEqual(self.cli("--changes", sp, "--yes", "--edit-sha256", sha), 2)
        self.assertEqual(self.writes(), [])
        self.assertFalse((self.dir / "edit-1-receipt.json").exists())
        self.assertNotIn("pending_edit", self.reload().data)
        self.assertEqual(ca.sha256_file(self.plan_path), self.original_sha)

    def test_preview_shows_field_diff_and_landed_prices(self):
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops())), 2)
        text = "\n".join(self.out)
        self.assertIn("price: 49.99 -> 99.98", text)
        self.assertIn("offer tier-1", text)
        self.assertIn("benefit.value: 45.00 -> 50.00", text)
        buy1 = next(l for l in self.out if l.strip().startswith("Buy 1 "))
        self.assertIn("unit 27.49 -> 49.99", buy1)
        self.assertIn("PATCH", text)

    def test_changes_and_undo_together_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops()), "--undo", "x.json"), 1)
        self.assertEqual(self.cli(), 1)
        self.assertEqual(self.writes(), [])

    def test_object_the_manifest_does_not_own_is_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_package_price", "package_keys": ["someone-elses"], "price": "9.99"}])), 1)
        self.assertIn("did not create", self.error())
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_benefit", "offer_key": "dashboard", "value": "20"}])), 1)
        self.assertIn("did not create", self.error())
        # in the plan, but the manifest never journalled it as created
        man = self.reload()
        man.mark("offers", "tier-1", status="pending", id=None)
        for extra in ([], ["--yes", "--edit-sha256", "0" * 8, "--live-traffic", "no"]):
            self.assertEqual(self.cli("--changes", self.spec(
                [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "40"}]), *extra), 1)
            self.assertIn("does not own", self.error())
        self.assertEqual(self.writes(), [])

    def test_identity_mismatch_refuses_everything(self):
        self.package(self.heroes()[0])["name"] = "Someone else's package"
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops())), 1)
        self.assertIn("identity mismatch", self.error())
        self.state["campaigns"][self.cid]["name"] = "Another campaign"
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops())), 1)
        self.assertIn("identity mismatch", self.error())
        self.assertEqual(self.writes(), [])

    def test_torn_down_run_is_refused(self):
        self.reload().mark("offers", "tier-1", status="deleting")
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops())), 1)
        self.assertIn("torn down", self.error())

    def test_live_value_changed_outside_the_skill_is_refused(self):
        self.offer("tier-2")["benefit"]["value"] = "51.00"
        self.assertEqual(self.cli("--changes", self.spec(self.ladder_ops())), 1)
        self.assertIn("benefit.value is 51.00 live, 50.00 in the plan", self.error())
        self.assertEqual(self.writes(), [])

    def test_live_offer_of_another_kind_is_refused(self):
        op = [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "46"}]
        self.offer("tier-1")["benefit"]["type"] = "order_percentage"
        self.assertEqual(self.cli("--changes", self.spec(op)), 1)
        self.assertIn("benefit.type is order_percentage live", self.error())
        self.offer("tier-1")["benefit"]["type"] = "package_percentage"
        self.offer("exit-pop")["code"] = "OTHER10"
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_benefit", "offer_key": "exit-pop", "value": "12"}])), 1)
        self.assertIn("code is OTHER10 live", self.error())
        self.assertEqual(self.writes(), [])

    def test_second_edit_in_the_same_run_directory_is_locked_out(self):
        sp = self.spec(self.ladder_ops())
        self.assertEqual(self.cli("--changes", sp), 2)
        sha = self.sha()
        (self.dir / ca.EDIT_LOCK_NAME).write_text("held")
        self.assertEqual(self.cli("--changes", sp, "--yes", "--edit-sha256", sha, "--live-traffic", "no"), 1)
        self.assertIn("another edit is running", self.error())
        self.assertEqual(self.writes(), [])
        self.assertTrue((self.dir / ca.EDIT_LOCK_NAME).exists())  # not ours to remove
        (self.dir / ca.EDIT_LOCK_NAME).unlink()
        self.assertEqual(self.cli("--changes", sp, "--yes", "--edit-sha256", sha, "--live-traffic", "no"), 0)
        self.assertFalse((self.dir / ca.EDIT_LOCK_NAME).exists())

    def test_drift_between_preview_and_apply_is_refused(self):
        sp = self.spec(self.ladder_ops())
        self.assertEqual(self.cli("--changes", sp), 2)
        sha = self.sha()
        # A change the plan does not model still moves the before-image hash.
        self.package(self.heroes()[0])["prices"].append({"currency": "EUR", "price": "45.00", "price_recurring": None})
        self.assertEqual(self.cli("--changes", sp, "--yes", "--edit-sha256", sha, "--live-traffic", "no"), 2)
        self.assertNotEqual(self.sha(), sha)
        self.assertEqual(self.writes(), [])

    def test_before_images_are_saved_before_the_first_write(self):
        seen = {}
        first = self.heroes()[0]

        def at_first_write():
            receipt = json.loads((self.dir / "edit-1-receipt.json").read_text())
            seen["receipt"] = receipt
            seen["pending"] = json.loads(self.manifest.read_text()).get("pending_edit")
            seen["live_price"] = self.package(first)["prices"][0]["price"]
        self.t.before[("PATCH", f"/api/admin/campaigns/{self.cid}/packages/{self.entry_id('packages', first)}/")] = at_first_write
        self.assertEqual(self.edit(self.ladder_ops()), 0, self.out[-3:])
        self.assertEqual(seen["live_price"], "49.99")
        obj = next(o for o in seen["receipt"]["objects"] if o["key"] == first)
        self.assertEqual(obj["before"]["prices"], [{"currency": "USD", "price": "49.99"}])
        self.assertEqual(obj["restore"], {"prices": [{"currency": "USD", "price": "49.99", "price_recurring": None}]})
        self.assertEqual(obj["patch"], {"prices": [{"currency": "USD", "price": "99.98", "price_recurring": None}]})
        self.assertEqual(seen["pending"]["objects"][f"packages:{first}"], "sending")
        self.assertEqual(seen["receipt"]["old_plan_sha256"], self.original_sha)
        self.assertFalse(seen["receipt"]["live_traffic_acknowledged"])
        # the receipt carries no api_key
        self.assertNotIn(self.man.data["campaign"]["api_key"], (self.dir / "edit-1-receipt.json").read_text())

    def test_live_traffic_answer_is_recorded(self):
        self.assertEqual(self.approve("--changes", self.spec(self.ladder_ops()), live="yes"), 0)
        self.assertTrue(json.loads((self.dir / "edit-1-receipt.json").read_text())["live_traffic_acknowledged"])

    def test_drift_just_before_a_write_stops_and_rollback_leaves_it_alone(self):
        ops = [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "46"},
               {"op": "set_offer_benefit", "offer_key": "tier-2", "value": "51"}]

        def dashboard_edit():
            self.offer("tier-2")["benefit"]["value"] = "52.00"
        self.t.before[("PATCH", self.offer_path("tier-1"))] = dashboard_edit
        self.assertEqual(self.edit(ops), 1)
        self.assertIn("changed since it was read", self.error())
        self.assertEqual([c[1] for c in self.writes()], [self.offer_path("tier-1")])
        pending = self.reload().data["pending_edit"]["objects"]
        self.assertEqual(pending, {"offers:tier-1": "verified", "offers:tier-2": "pending"})
        # Rollback restores what the edit wrote and does not touch what it never wrote.
        self.assertEqual(self.undo(live=None), 0, self.out[-3:])
        self.assertEqual(self.offer("tier-1")["benefit"]["value"], "45.00")
        self.assertEqual(self.offer("tier-2")["benefit"]["value"], "52.00")
        self.assertEqual(len([c for c in self.writes() if c[1] == self.offer_path("tier-2")]), 0)
        self.assertEqual(self.plan_path.read_bytes(), ca.json_bytes(self.original))

    def test_one_patch_per_offer_with_the_whole_condition(self):
        rc = self.edit([{"op": "set_offer_benefit", "offer_key": "tier-4", "value": "70"},
                        {"op": "set_offer_condition", "offer_key": "tier-4", "value": 5}])
        self.assertEqual(rc, 0, self.out[-3:])
        patches = [c for c in self.writes() if c[0] == "PATCH"]
        self.assertEqual(len(patches), 1)
        ids = sorted(self.entry_id("packages", k) for k in self.heroes())
        self.assertEqual(patches[0][2]["benefit"], {"value": "70.00"})
        cond = patches[0][2]["condition"]
        self.assertEqual((cond["type"], cond["value"], cond["all_packages"], sorted(cond["package_ids"])),
                         ("count", 5, False, ids))
        # Buy 4 no longer meets tier-4, so the next tier down prices it.
        self.assertEqual((self.row("Buy 4")["offer_key"], self.row("Buy 4")["pct"]), ("tier-3", 55))
        self.assert_in_line()

    def test_condition_or_scope_edit_refused_when_the_read_lacks_the_condition(self):
        del self.offer("tier-4")["condition"]["value"]
        for op in ({"op": "set_offer_condition", "offer_key": "tier-4", "value": 5},
                   {"op": "set_offer_scope", "offer_key": "tier-4", "package_keys": list(reversed(self.heroes()))}):
            self.assertEqual(self.cli("--changes", self.spec([op])), 1)
            self.assertIn("cannot be proven", self.error())
        # a benefit-only edit sends no condition, so it does not need one
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_benefit", "offer_key": "tier-4", "value": "61"}])), 2)
        self.assertEqual(self.writes(), [])

    def test_post_write_mismatch_stops_and_does_not_retry(self):
        path = self.offer_path("tier-1")
        self.t.ignore_patch.add(("PATCH", path))
        self.assertEqual(self.edit([{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "46"}]), 1)
        self.assertIn("not the intended state", self.error())
        self.assertIn("not retried", self.error())
        self.assertEqual(len(self.writes()), 1)
        self.assertEqual(self.reload().data["pending_edit"]["objects"]["offers:tier-1"], "sending")
        self.assertEqual(ca.sha256_file(self.plan_path), self.original_sha)

    def test_failed_patch_is_not_retried_and_a_rerun_finishes(self):
        ops = [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "46"},
               {"op": "set_offer_benefit", "offer_key": "tier-2", "value": "51"}]
        self.t.fail_on[("PATCH", self.offer_path("tier-2"))] = 500
        self.assertEqual(self.edit(ops), 1)
        self.assertEqual(len([c for c in self.writes() if c[1] == self.offer_path("tier-2")]), 1)
        with self.assertRaises(ca.CampaignAdminError):
            self.verify()
        with self.assertRaises(ca.CampaignAdminError):
            ca.apply(self.client(), self.plan(), self.original_sha, self.manifest, self.manifest)
        # A different edit is refused while this one is unfinished.
        other = self.spec([{"op": "set_offer_benefit", "offer_key": "tier-3", "value": "56"}], "other.json")
        self.assertEqual(self.cli("--changes", other), 1)
        self.assertIn("unfinished", self.error())
        # Re-running the same edit continues under its recorded approval hash.
        self.assertEqual(self.approve("--changes", self.spec(ops), live=None), 0, self.out[-3:])
        self.assertEqual(len([c for c in self.writes() if c[1] == self.offer_path("tier-1")]), 1)
        self.assertEqual(self.offer("tier-2")["benefit"]["value"], "51.00")
        self.assert_in_line()


# --------------------------------------------------------------------------- #
class EditPricing(EditHarness):
    def test_price_and_ladder_edit_keeps_verify_resume_and_teardown_working(self):
        self.assertEqual(self.edit(self.ladder_ops()), 0, self.out[-3:])
        for key in self.heroes():
            self.assertEqual(self.package(key)["prices"], [{"currency": "USD", "price": "99.98", "price_recurring": None}])
        plan = self.plan()
        self.assertEqual([p["price"] for p in plan["packages"] if p["role"] == "hero"], ["99.98"] * 4)
        for qty, pct in enumerate((50, 55, 60, 65), start=1):
            row = self.row(f"Buy {qty}", plan)
            unit = ca.landed_unit(Decimal("99.98"), Decimal(pct), None)
            self.assertEqual((row["offer_key"], row["pct"], row["anchor"], row["unit_after"], row["order_total"]),
                             (f"tier-{qty}", pct, "99.98", ca.money(unit), ca.money(unit * qty)))
            self.assertEqual(self.offer(f"tier-{qty}")["benefit"]["value"], f"{pct}.00")
        report = self.assert_in_line()
        self.assertIn("Buy 4 mixed variants", [c["case"] for c in report["calculate_cases"]])
        self.assertEqual(report["sections"], {"owned_fields": "PASS", "calculate": "PASS", "live_offer_set": "PASS"})
        self.assertEqual(self.man.data["edits"][0]["kind"], "edit")
        # resume: nothing left to create, and it accepts the edited plan
        posts = len([c for c in self.t.calls if c[0] == "POST"])
        ca.apply(self.client(), plan, ca.sha256_file(self.plan_path), self.manifest, self.manifest)
        self.assertEqual(len([c for c in self.t.calls if c[0] == "POST"]), posts)
        # teardown: identity still checks out against the edited plan
        ca.teardown(self.client(), self.reload(), plan, ca.sha256_file(self.plan_path), lambda: True)
        self.assertEqual(self.state["campaigns"], {})

    def test_offerscoop_shape_leaves_a_dashboard_offer_alone(self):
        """2026-10-02: a hero price change and four new tier percentages on a
        campaign that also carried an offer added in the dashboard."""
        oid = self.dashboard_offer()
        before = json.dumps(self.state["offers"][self.cid][oid], sort_keys=True)
        self.assertEqual(self.edit(self.ladder_ops()), 0, self.out[-3:])
        self.assertTrue(any(f"offer {oid} 'Dashboard deal'" in l for l in self.out))
        self.assertEqual(json.dumps(self.state["offers"][self.cid][oid], sort_keys=True), before)
        self.assertFalse([c for c in self.writes() if c[1].endswith(f"/offers/{oid}/")])
        self.assertNotIn(oid, [e.get("id") for e in self.reload().data["offers"]])
        self.assertEqual(self.package(self.heroes()[0])["prices"][0]["price"], "99.98")
        # verify's exact-set check is not weakened: the dashboard offer still fails
        # it, and the report says which part is and is not about this run.
        report = self.verify()
        self.assertEqual(report["result"], "FAIL")
        self.assertEqual(report["sections"]["owned_fields"], "PASS")
        self.assertEqual(report["sections"]["calculate"], "NOT ATTRIBUTABLE")
        self.assertEqual(report["sections"]["live_offer_set"], "FAIL")
        self.assertEqual(report["unowned_live_offers"], [{"id": oid, "name": "Dashboard deal"}])

    def test_repricing_one_variant_of_a_row_is_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_package_price", "package_keys": self.heroes()[:1], "price": "59.99"}])), 1)
        self.assertIn("share one price", self.error())
        self.assertIn("teardown and recreate", self.error())

    def test_two_offers_at_the_same_percentage_are_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_benefit", "offer_key": "tier-2", "value": "45"}])), 1)
        self.assertIn("both apply at 45%", self.error())

    def test_scope_covering_part_of_a_row_is_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_scope", "offer_key": "tier-1", "package_keys": self.heroes()[:1]}])), 1)
        self.assertIn("only some of its packages", self.error())
        self.assertEqual(self.writes(), [])

    def test_pausing_only_tier_lands_at_package_price(self):
        self.assertEqual(self.edit([{"op": "set_offer_available", "offer_key": "tier-1", "available": False}]), 0)
        self.assertEqual(self.writes()[-1][2], {"available": False})
        self.assertFalse(self.offer("tier-1")["available"])
        row = self.row("Buy 1")
        self.assertEqual((row["offer_key"], row["pct"], row["unit_after"]), (None, 0, "49.99"))
        self.assertFalse(next(o for o in self.plan()["offers"] if o["key"] == "tier-1")["available"])
        report = self.assert_in_line()
        self.assertIn("Buy 1 single variant", [c["case"] for c in report["calculate_cases"]])
        # resuming an offer drops the flag again
        self.assertEqual(self.edit([{"op": "set_offer_available", "offer_key": "tier-1", "available": True}], "on.json"), 0)
        self.assertNotIn("available", next(o for o in self.plan()["offers"] if o["key"] == "tier-1"))
        self.assertEqual(self.row("Buy 1")["offer_key"], "tier-1")
        self.assert_in_line()

    def test_verify_reads_condition_and_availability_back(self):
        self.offer("tier-2")["condition"]["value"] = 3
        self.offer("tier-3")["available"] = False
        checks = {c["check"]: c["result"] for c in self.verify()["admin_checks"]}
        self.assertEqual(checks["offer tier-2 condition value"], "FAIL")
        self.assertEqual(checks["offer tier-3 available"], "FAIL")
        self.assertEqual(checks["offer tier-1 condition value"], "PASS")

    def test_pausing_a_higher_tier_falls_to_the_next_one(self):
        self.assertEqual(self.edit([{"op": "set_offer_available", "offer_key": "tier-3", "available": False}]), 0)
        self.assertEqual((self.row("Buy 3")["offer_key"], self.row("Buy 3")["pct"]), ("tier-2", 50))
        self.assertEqual(self.row("Buy 4")["offer_key"], "tier-4")
        self.assert_in_line()

    def test_paused_plan_resumes_but_a_fresh_apply_refuses_it(self):
        self.assertEqual(self.edit([{"op": "set_offer_available", "offer_key": "tier-1", "available": False}]), 0)
        plan, sha = ca.load_json_and_hash(self.plan_path)
        posts = len([c for c in self.t.calls if c[0] == "POST"])
        ca.apply(self.client(), plan, sha, self.manifest, self.manifest)
        self.assertEqual(len([c for c in self.t.calls if c[0] == "POST"]), posts)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(self.client(), dict(plan, campaign=dict(plan["campaign"], name="Fresh")), sha,
                     self.dir / "fresh" / "run-manifest.json", None)
        self.assertIn("creates every offer live", str(cm.exception))
        # resume refuses to pause something it would have to create
        man = self.reload()
        man.mark("offers", "tier-1", status="absent")
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.apply(self.client(), plan, sha, self.manifest, self.manifest)
        self.assertIn("has not created", str(cm.exception))

    def test_pausing_a_voucher_a_row_depends_on_is_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_available", "offer_key": "exit-pop", "available": False}])), 1)
        self.assertIn("paused code is unobserved", self.error())

    def test_excluded_requests_are_refused_by_name(self):
        for op, field in ca.NOT_EDITABLE.items():
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.build_edit(self.original, {"operations": [{"op": op}]})
            self.assertIn(f"cannot be edited in place: {field}", str(cm.exception))
            self.assertIn("teardown and recreate", str(cm.exception))
        for op, field in (({"op": "set_offer_benefit", "offer_key": "tier-1", "type": "order_percentage"}, "type"),
                          ({"op": "set_offer_condition", "offer_key": "tier-1", "all_packages": True}, "all_packages"),
                          ({"op": "set_package_price", "package_keys": self.heroes(), "price": "9.99",
                            "currency": "EUR"}, "currency")):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.build_edit(self.original, {"operations": [op]})
            self.assertIn(f"cannot be edited in place: {field}", str(cm.exception))
        for bad in ({"operations": []}, {"operations": [{"op": "rename_campaign"}]}, [],
                    {"operations": [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "0"}]},
                    {"operations": [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "101"}]},
                    {"operations": [{"op": "set_package_price", "package_keys": self.heroes(), "price": "0"}]},
                    {"operations": [{"op": "set_offer_condition", "offer_key": "tier-2", "value": 0}]},
                    {"operations": [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "45"}]}):
            with self.assertRaises(ca.CampaignAdminError):
                ca.build_edit(self.original, bad)

    def test_hand_edited_or_dashboard_offer_plans_are_refused(self):
        plan = json.loads(json.dumps(self.original))
        plan["landed_prices"][0]["unit_after"] = "1.00"
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.build_edit(plan, {"operations": self.ladder_ops()})
        self.assertIn("edited by", str(cm.exception))
        plan = json.loads(json.dumps(self.original))
        plan["offers"] = []
        for l in plan["landed_prices"]:
            l["offer_key"] = None
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.build_edit(plan, {"operations": [{"op": "set_package_price", "package_keys": self.heroes(),
                                                 "price": "59.99"}]})
        self.assertIn("handed", str(cm.exception))


class EditPricingRounded(EditHarness):
    RECOMMEND = dict(EditHarness.RECOMMEND, rounding="0.99")

    def test_value_only_edit_keeps_rounding_and_can_clear_it(self):
        self.assertEqual(self.edit([{"op": "set_offer_benefit", "offer_key": "tier-2", "value": "52"}]), 0)
        self.assertEqual(self.writes()[-1][2], {"benefit": {"value": "52.00"}})
        self.assertEqual(self.offer("tier-2")["benefit"], {"type": "package_percentage", "value": "52.00",
                                                          "price_rounding": "0.99"})
        self.assertEqual(self.row("Buy 2")["unit_after"], "24.99")
        self.assert_in_line()
        self.assertEqual(self.edit([{"op": "set_offer_benefit", "offer_key": "tier-2", "price_rounding": None}],
                                   "clear.json"), 0)
        self.assertEqual(self.writes()[-1][2], {"benefit": {"value": "52.00", "price_rounding": None}})
        self.assertEqual(self.row("Buy 2")["unit_after"], "24.00")
        self.assert_in_line()

    def test_rounding_that_undoes_the_discount_is_refused(self):
        self.assertEqual(self.cli("--changes", self.spec(
            [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "1"}])), 1)
        self.assertIn("not below", self.error())


class Reland(unittest.TestCase):
    def setUp(self):
        self.disc = base.load_fixture("discovery.json")

    def test_reland_reproduces_recommend_on_every_plan_shape(self):
        shapes = dict(
            default=ns(), rounded=ns(rounding="0.99"), ladder=ns(tiers="50,55,60,65", shipping=base.LADDER),
            high=ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50", "17:39.95:40"]),
            bxgy=ns(offer_type="bxgy", paid_qty=2, free_qty=1), bxgy_rounded=ns(offer_type="bxgy", paid_qty=3, free_qty=1, rounding="0.95"),
            gwp=ns(offer_type="gwp", gift=["16:19.95"]), gifts=ns(gift=["16:19.95:2", "17:24.95"], free_shipping_min_qty=2),
            free_shipping=ns(free_shipping=True), no_exit=ns(exit="0"),
            hero_upsell=ns(upsell=["23:49.95:50"]), bump_upsell=ns(bump=["7:9.95"], upsell=["7:9.95:50"]),
        )
        for name, args in shapes.items():
            with self.subTest(name):
                plan = ca.recommend(self.disc, args)
                self.assertEqual(ca.reland(plan), plan["landed_prices"])

    def test_upsell_row_is_priced_by_its_own_voucher(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        row = next(l for l in ca.reland(plan) if l["kind"] == "upsell")
        self.assertEqual((row["anchor"], row["unit_after"]), ("39.95", "19.97"))

    def test_winner_is_the_highest_percentage_not_the_lowest_landed_unit(self):
        pkg = {"key": "p", "price": "10.00"}

        def offer(key, value, rounding):
            return {"key": key, "name": key, "offer_type": "offer",
                    "condition": {"type": "any", "value": None, "package_keys": ["p"]},
                    "benefit": {"type": "package_percentage", "value": value, "price_rounding": rounding}}
        plan = {"packages": [pkg], "offers": [offer("low", "49.00", None), offer("high", "50.00", "0.99")],
                "landed_prices": [{"tier": "Buy 1", "kind": "single", "qty": 1, "offer_key": None,
                                   "package_keys": ["p"], "anchor": "10.00", "pct": 0,
                                   "unit_after": "10.00", "order_total": "10.00"}]}
        self.assertEqual(ca.landed_unit(Decimal("10"), Decimal(49), None), Decimal("5.10"))
        row = ca.reland(plan)[0]
        self.assertEqual((row["offer_key"], row["pct"], row["unit_after"]), ("high", 50, "5.99"))

    def test_hero_package_reused_as_an_upsell(self):
        plan = ca.recommend(self.disc, ns(upsell=["23:49.95:50"]))
        heroes = [p["key"] for p in plan["packages"] if p["role"] == "hero"]
        new, changed, added = ca.build_edit(plan, {"operations": [
            {"op": "set_package_price", "package_keys": heroes, "price": "59.95"}]})
        upsell = next(l for l in new["landed_prices"] if l["kind"] == "upsell")
        # the upsell voucher covers one hero package only; that is not a partial
        # scope, because a voucher never enters the automatic-offer contest
        self.assertEqual((upsell["package_keys"], upsell["anchor"], upsell["unit_after"]), (["hero-23"], "59.95", "29.97"))
        self.assertEqual(next(l for l in new["landed_prices"] if l["tier"] == "Buy 1")["unit_after"], "29.97")
        self.assertEqual(sorted(k for _, k in changed), sorted(heroes))
        voucher = next(o["key"] for o in plan["offers"] if o["offer_type"] == "voucher" and o["key"] != "exit-pop")
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.build_edit(plan, {"operations": [{"op": "set_offer_available", "offer_key": voucher, "available": False}]})
        self.assertIn("unobserved", str(cm.exception))

    def test_upsell_voucher_that_stops_applying_is_refused(self):
        plan = ca.recommend(self.disc, ns(hero=10, ctc="high", anchor_price="189.95", upsell=["16:39.95:50"]))
        voucher = next(l["offer_key"] for l in plan["landed_prices"] if l["kind"] == "upsell")
        hero = next(p["key"] for p in plan["packages"] if p["role"] == "hero")
        for op in ({"op": "set_offer_condition", "offer_key": voucher, "type": "count", "value": 2},
                   {"op": "set_offer_scope", "offer_key": voucher, "package_keys": [hero]}):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.build_edit(plan, {"operations": [op]})
            self.assertIn("would no longer apply", str(cm.exception))

    def test_exit_voucher_out_of_scope_is_not_applied_in_cart_cases(self):
        plan = ca.recommend(self.disc, ns(bump=["7:9.95"], exit_code="SAVE10"))
        ids = {p["key"]: i for i, p in enumerate(plan["packages"], start=1)}
        self.assertTrue(any("exit voucher" in c.name for c in ca._cart_cases_from_plan(plan, ids)))
        bump = next(p["key"] for p in plan["packages"] if p["role"] == "bump")
        new, _, _ = ca.build_edit(plan, {"operations": [
            {"op": "set_offer_scope", "offer_key": "exit-pop", "package_keys": [bump]}]})
        self.assertFalse(any("exit voucher" in c.name for c in ca._cart_cases_from_plan(new, ids)))

    def test_buy_x_get_y_percentage_and_condition_are_not_editable(self):
        plan = ca.recommend(self.disc, ns(offer_type="bxgy", paid_qty=2, free_qty=1))
        for op in ({"op": "set_offer_benefit", "offer_key": "bxgy-2-1", "value": "40"},
                   {"op": "set_offer_condition", "offer_key": "bxgy-2-1", "value": 4}):
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.build_edit(plan, {"operations": [op]})
            self.assertIn("buy-X-get-Y", str(cm.exception))
        heroes = [p["key"] for p in plan["packages"] if p["role"] == "hero"]
        new, _, _ = ca.build_edit(plan, {"operations": [
            {"op": "set_package_price", "package_keys": heroes, "price": "60.00"}]})
        deal = next(l for l in new["landed_prices"] if l.get("paid_qty") is not None)
        self.assertEqual((deal["anchor"], deal["full_retail"], deal["payable"]), ("60.00", "180.00", deal["order_total"]))


# --------------------------------------------------------------------------- #
class EditAddOffer(EditHarness):
    def add_op(self, name="Scope - Loyalty - 5%", available=None, key="loyalty"):
        op = {"op": "add_offer", "offer": {
            "key": key, "name": name, "offer_type": "offer",
            "condition": {"type": "any", "package_keys": self.heroes()},
            "benefit": {"type": "package_percentage", "value": "5", "price_rounding": None}}}
        if available is not None:
            op["available"] = available
        return op

    def new_offer_path(self):
        return f"/api/admin/campaigns/{self.cid}/offers/{self.state['seq'] + 1}/"

    def test_add_then_pause(self):
        self.assertEqual(self.edit([self.add_op(available=False)]), 0, self.out[-3:])
        kinds = [(c[0], c[2].get("available")) for c in self.writes()]
        self.assertEqual(kinds, [("POST", None), ("PATCH", False)])
        self.assertFalse(self.offer("loyalty")["available"])
        entry = self.reload().entry("offers", "loyalty")
        self.assertEqual((entry["status"], entry["pause_pending"]), ("created", False))
        self.assertFalse(next(o for o in self.plan()["offers"] if o["key"] == "loyalty")["available"])
        self.assert_in_line()

    def test_added_live_offer_reprices_nothing_it_does_not_win(self):
        self.assertEqual(self.edit([self.add_op()]), 0, self.out[-3:])
        self.assertTrue(self.offer("loyalty")["available"])
        self.assertEqual(self.row("Buy 1")["offer_key"], "tier-1")
        self.assert_in_line()

    def test_name_or_code_collision_is_refused_before_anything_is_journalled(self):
        self.dashboard_offer(name="Scope - Loyalty - 5%")
        self.assertEqual(self.cli("--changes", self.spec([self.add_op()])), 1)
        self.assertIn("already taken", self.error())
        self.assertEqual(self.writes(), [])
        self.assertIsNone(self.reload().entry("offers", "loyalty"))
        self.assertNotIn("pending_edit", self.man.data)
        self.assertFalse((self.dir / "edit-1-receipt.json").exists())

    def test_lost_post_response_is_claimed_not_duplicated(self):
        sp = self.spec([self.add_op()])
        self.t.lose.add(("POST", self.offers_path()))
        self.assertEqual(self.approve("--changes", sp), 1)
        self.assertEqual(self.reload().entry("offers", "loyalty")["status"], "pending")
        count = len(self.state["offers"][self.cid])
        self.assertEqual(self.approve("--changes", sp, live=None), 0, self.out[-3:])
        self.assertEqual(len([c for c in self.writes() if c[0] == "POST"]), 1)
        self.assertEqual(len(self.state["offers"][self.cid]), count)
        self.assertTrue(self.reload().entry("offers", "loyalty")["reconciled"])
        self.assert_in_line()

    def test_a_rerun_never_claims_an_offer_that_was_live_before_the_edit(self):
        other = self.dashboard_offer(name="Someone's own offer")
        sp = self.spec([self.add_op()])

        def rename():
            # the dashboard offer takes the same name while the edit is in flight
            self.state["offers"][self.cid][other]["name"] = "Scope - Loyalty - 5%"
        self.t.before[("POST", self.offers_path())] = rename
        self.t.fail_on[("POST", self.offers_path())] = 400
        self.assertEqual(self.approve("--changes", sp), 1)
        self.assertEqual(self.approve("--changes", sp, live=None), 0, self.out[-3:])
        mine = self.reload().entry("offers", "loyalty")["id"]
        self.assertNotEqual(mine, other)
        self.assertNotIn(other, [e.get("id") for e in self.man.data["offers"]])
        # and teardown therefore never deletes it
        plan, sha = ca.load_json_and_hash(self.plan_path)
        ca.teardown(self.client(), self.man, plan, sha, lambda: True)
        self.assertFalse([c for c in self.t.calls if c[0] == "DELETE" and c[1].endswith(f"/offers/{other}/")])

    def test_stop_between_create_and_pause_finishes_the_pause(self):
        sp = self.spec([self.add_op(available=False)])
        self.t.fail_on[("PATCH", self.new_offer_path())] = 500
        self.assertEqual(self.approve("--changes", sp), 1)
        self.assertIn("LIVE", self.error())
        entry = self.reload().entry("offers", "loyalty")
        self.assertEqual((entry["status"], entry["pause_pending"]), ("created", True))
        self.assertTrue(self.offer("loyalty")["available"])
        self.assertEqual(self.approve("--changes", sp, live=None), 0, self.out[-3:])
        self.assertEqual(len([c for c in self.writes() if c[0] == "POST"]), 1)
        self.assertFalse(self.offer("loyalty")["available"])
        self.assert_in_line()

    def test_changed_added_offer_is_not_paused_on_rerun(self):
        sp = self.spec([self.add_op(available=False)])
        self.t.fail_on[("PATCH", self.new_offer_path())] = 500
        self.assertEqual(self.approve("--changes", sp), 1)
        self.offer("loyalty")["benefit"]["value"] = "25.00"  # changed in the dashboard meanwhile
        writes = len(self.writes())
        self.assertEqual(self.approve("--changes", sp, live=None), 1)
        self.assertIn("not paused or changed", self.error())
        self.assertEqual(len(self.writes()), writes)
        self.assertTrue(self.offer("loyalty")["available"])

    def test_add_offer_input_is_checked_by_name(self):
        for change, text in (({"condition": {"type": "any", "value": 2, "package_keys": self.heroes()}}, "takes no value"),
                             ({"benefit": {"type": "package_percentage", "value": "150"}}, "percentage in (0, 100]"),
                             ({"benefit": {"type": "package_percentage", "value": "5", "price_rounding": "0.50"}},
                              "price_rounding")):
            op = self.add_op()
            op["offer"].update(change)
            with self.assertRaises(ca.CampaignAdminError) as cm:
                ca.build_edit(self.original, {"operations": [op]})
            self.assertIn("add_offer", str(cm.exception))
            self.assertIn(text, str(cm.exception))

    def test_teardown_mid_edit_takes_the_new_offer_identity_from_the_receipt(self):
        sp = self.spec([self.add_op(available=False)])
        self.t.fail_on[("PATCH", self.new_offer_path())] = 500
        self.assertEqual(self.approve("--changes", sp), 1)
        new_id = self.entry_id("offers", "loyalty")
        # the plan on disk is still the old one and does not know this offer
        self.assertEqual(ca.sha256_file(self.plan_path), self.original_sha)
        receipt = self.dir / "edit-1-receipt.json"
        hidden = self.dir / "hidden.json"
        receipt.rename(hidden)
        with self.assertRaises(ca.CampaignAdminError) as cm:
            ca.teardown(self.client(), self.reload(), self.plan(), self.original_sha, lambda: True)
        self.assertIn("receipt cannot be read", str(cm.exception))
        self.assertFalse([c for c in self.t.calls if c[0] == "DELETE"])
        hidden.rename(receipt)
        ca.teardown(self.client(), self.reload(), self.plan(), self.original_sha, lambda: True)
        self.assertTrue([c for c in self.t.calls if c[0] == "DELETE" and c[1].endswith(f"/offers/{new_id}/")])
        self.assertEqual(self.state["campaigns"], {})
        self.assertEqual(self.state["offers"][self.cid], {})

    def test_teardown_mid_edit_resolves_a_lost_create(self):
        self.t.lose.add(("POST", self.offers_path()))
        self.assertEqual(self.approve("--changes", self.spec([self.add_op()])), 1)
        ca.teardown(self.client(), self.reload(), self.plan(), self.original_sha, lambda: True)
        self.assertEqual(self.state["offers"][self.cid], {})
        self.assertEqual(self.reload().entry("offers", "loyalty")["status"], "deleted")

    def test_undo_of_an_added_offer_pauses_it_and_keeps_it(self):
        self.assertEqual(self.edit([self.add_op()]), 0)
        self.assertEqual(self.undo(), 0, self.out[-3:])
        self.assertTrue(any("pauses it" in l for l in self.out))
        self.assertFalse([c for c in self.t.calls if c[0] == "DELETE"])
        self.assertFalse(self.offer("loyalty")["available"])
        self.assertEqual(self.reload().entry("offers", "loyalty")["status"], "created")
        self.assertFalse(next(o for o in self.plan()["offers"] if o["key"] == "loyalty")["available"])
        self.assertEqual(self.man.data["edits"][-1]["kind"], "undo")
        self.assert_in_line()


# --------------------------------------------------------------------------- #
class EditRecovery(EditHarness):
    OPS = [{"op": "set_offer_benefit", "offer_key": "tier-1", "value": "46"},
           {"op": "set_offer_benefit", "offer_key": "tier-2", "value": "51"}]

    def crash_on_plan_write(self, after):
        """Make the next write of the plan file die, before or after it lands."""
        real = ca.atomic_write_bytes

        def boom(path, raw, mode=0o600):
            if Path(path) == self.plan_path:
                ca.atomic_write_bytes = real
                if after:
                    real(path, raw, mode)
                raise RuntimeError("power cut")
            return real(path, raw, mode)
        ca.atomic_write_bytes = boom
        self.addCleanup(lambda: setattr(ca, "atomic_write_bytes", real))

    def test_stop_before_the_plan_write_recovers_on_rerun(self):
        sp = self.spec(self.OPS)
        self.crash_on_plan_write(after=False)
        with self.assertRaises(RuntimeError):
            self.approve("--changes", sp)
        self.assertEqual(ca.sha256_file(self.plan_path), self.original_sha)
        self.assertEqual(set(self.reload().data["pending_edit"]["objects"].values()), {"verified"})
        writes = len(self.writes())
        self.assertEqual(self.approve("--changes", sp, live=None), 0, self.out[-3:])
        self.assertEqual(len(self.writes()), writes)  # nothing is sent twice
        self.assert_in_line()

    def test_stop_after_the_plan_write_recovers_on_rerun(self):
        sp = self.spec(self.OPS)
        self.crash_on_plan_write(after=True)
        with self.assertRaises(RuntimeError):
            self.approve("--changes", sp)
        man = self.reload()
        self.assertEqual(man.data["plan_sha256"], self.original_sha)
        self.assertEqual(ca.sha256_file(self.plan_path), man.data["pending_edit"]["new_plan_sha256"])
        self.assertEqual(self.approve("--changes", sp, live=None), 0, self.out[-3:])
        self.assert_in_line()

    def test_rerun_rechecks_objects_it_already_verified(self):
        sp = self.spec(self.OPS)
        self.t.fail_on[("PATCH", self.offer_path("tier-2"))] = 500
        self.assertEqual(self.approve("--changes", sp), 1)
        self.offer("tier-1")["benefit"]["value"] = "47.00"  # verified earlier, changed since
        self.assertEqual(self.approve("--changes", sp, live=None), 1)
        self.assertIn("has changed again since", self.error())
        self.assertEqual(ca.sha256_file(self.plan_path), self.original_sha)

    def test_receipt_left_by_an_edit_that_never_started_does_not_block(self):
        (self.dir / "edit-1-receipt.json").write_text("{}")
        self.assertEqual(self.edit(self.OPS), 0, self.out[-3:])
        self.assert_in_line()

    def test_undo_restores_before_images_and_the_old_landed_prices(self):
        self.assertEqual(self.edit(self.ladder_ops()), 0)
        self.assertEqual(self.undo(), 0, self.out[-3:])
        self.assertEqual(self.package(self.heroes()[0])["prices"][0]["price"], "49.99")
        self.assertEqual([self.offer(f"tier-{i}")["benefit"]["value"] for i in (1, 2, 3, 4)],
                         ["45.00", "50.00", "55.00", "60.00"])
        self.assertEqual(self.plan(), self.original)
        self.assertEqual(self.reload().data["plan_sha256"], self.original_sha)
        self.assertEqual([e["kind"] for e in self.man.data["edits"]], ["edit", "undo"])
        self.assertTrue((self.dir / "edit-2-receipt.json").exists())
        self.assert_in_line()
        # only the latest edit can be undone
        self.assertEqual(self.cli("--undo", self.dir / "edit-1-receipt.json"), 1)
        self.assertIn("most recent", self.error())

    def test_undo_of_a_half_applied_edit_restores_only_what_was_written(self):
        self.t.fail_on[("PATCH", self.offer_path("tier-2"))] = 500
        self.assertEqual(self.edit(self.OPS), 1)
        self.assertEqual(self.offer("tier-1")["benefit"]["value"], "46.00")
        posts = len([c for c in self.t.calls if c[0] == "POST"])
        self.assertEqual(self.undo(live=None), 0, self.out[-3:])
        self.assertEqual(self.offer("tier-1")["benefit"]["value"], "45.00")
        self.assertEqual(self.offer("tier-2")["benefit"]["value"], "50.00")
        # tier-2 was never changed, so it gets no restoring write
        self.assertEqual(len([c for c in self.writes() if c[1] == self.offer_path("tier-2")]), 1)
        self.assertEqual(len([c for c in self.t.calls if c[0] == "POST"]), posts)
        self.assertEqual(self.plan_path.read_bytes(), ca.json_bytes(self.original))
        self.assertEqual(self.reload().data["edits"][-1]["kind"], "rolled_back")
        self.assertTrue((self.dir / "edit-1-receipt.json").exists())  # the forward receipt is kept
        self.assertTrue((self.dir / "edit-1-rollback.json").exists())
        self.assert_in_line()
        # the run is usable again: a new edit gets the next number
        self.assertEqual(self.edit(self.OPS, "again.json"), 0, self.out[-3:])
        self.assertTrue((self.dir / "edit-2-receipt.json").exists())
        self.assert_in_line()

    def add_op(self):
        return {"op": "add_offer", "offer": {
            "key": "loyalty", "name": "Scope - Loyalty - 5%", "offer_type": "offer",
            "condition": {"type": "any", "package_keys": self.heroes()},
            "benefit": {"type": "package_percentage", "value": "5", "price_rounding": None}}}

    def test_undo_after_a_lost_create_claims_and_pauses_without_posting(self):
        self.t.lose.add(("POST", self.offers_path()))
        self.assertEqual(self.edit([self.add_op()]), 1)
        self.assertEqual(self.undo(live=None), 0, self.out[-3:])
        self.assertEqual(len([c for c in self.writes() if c[0] == "POST"]), 1)
        self.assertEqual(self.reload().entry("offers", "loyalty")["status"], "created")
        self.assertFalse(self.offer("loyalty")["available"])
        kept = next(o for o in self.plan()["offers"] if o["key"] == "loyalty")
        self.assertFalse(kept["available"])
        self.assertEqual(self.plan()["landed_prices"], self.original["landed_prices"])
        self.assertEqual(self.man.data["edits"][-1]["kept_added_offers"], ["loyalty"])
        self.assert_in_line()

    def test_undo_after_a_create_that_never_happened_drops_the_entry(self):
        self.t.fail_on[("POST", self.offers_path())] = 500
        self.assertEqual(self.edit([self.add_op()]), 1)
        self.assertEqual(self.reload().entry("offers", "loyalty")["status"], "pending")
        self.assertEqual(self.undo(live=None), 0, self.out[-3:])
        self.assertEqual(len([c for c in self.writes() if c[0] == "POST"]), 1)  # the failed one only
        self.assertIsNone(self.reload().entry("offers", "loyalty"))
        self.assertEqual(self.man.data["edits"][-1]["dropped_added_offers"], ["loyalty"])
        self.assertEqual(self.plan_path.read_bytes(), ca.json_bytes(self.original))
        # the manifest holds no offer the plan lacks, so verify and a later edit work
        self.assert_in_line()
        self.assertEqual(self.edit(self.OPS, "later.json"), 0, self.out[-3:])
        self.assert_in_line()

    def test_rollback_interrupted_after_its_plan_write_resumes(self):
        self.t.lose.add(("POST", self.offers_path()))
        self.assertEqual(self.edit([self.add_op()]), 1)
        receipt = self.dir / "edit-1-receipt.json"
        self.assertEqual(self.cli("--undo", receipt), 2)
        sha = self.sha()
        self.crash_on_plan_write(after=True)
        with self.assertRaises(RuntimeError):
            self.cli("--undo", receipt, "--yes", "--edit-sha256", sha)
        man = self.reload()
        third = man.data["pending_edit"]["rollback"]["plan_sha256"]
        self.assertEqual(ca.sha256_file(self.plan_path), third)
        self.assertNotIn(third, (self.original_sha, man.data["pending_edit"]["new_plan_sha256"]))
        # the same approval finishes it; a different hash does not
        self.assertEqual(self.cli("--undo", receipt, "--yes", "--edit-sha256", "0" * 8), 2)
        self.assertEqual(self.cli("--undo", receipt, "--yes", "--edit-sha256", sha), 0, self.out[-3:])
        self.assertEqual(self.reload().data["plan_sha256"], third)
        self.assert_in_line()

    def test_teardown_accepts_the_rollback_plan_while_it_is_unfinished(self):
        self.t.lose.add(("POST", self.offers_path()))
        self.assertEqual(self.edit([self.add_op()]), 1)
        receipt = self.dir / "edit-1-receipt.json"
        self.assertEqual(self.cli("--undo", receipt), 2)
        self.crash_on_plan_write(after=True)
        with self.assertRaises(RuntimeError):
            self.cli("--undo", receipt, "--yes", "--edit-sha256", self.sha())
        plan, sha = ca.load_json_and_hash(self.plan_path)
        ca.teardown(self.client(), self.reload(), plan, sha, lambda: True)
        self.assertEqual(self.state["offers"][self.cid], {})
        self.assertEqual(self.state["campaigns"], {})

    def test_rollback_rereads_just_before_it_restores(self):
        self.t.fail_on[("PATCH", self.offer_path("tier-2"))] = 500
        self.assertEqual(self.edit(self.OPS), 1)
        receipt = self.dir / "edit-1-receipt.json"
        self.assertEqual(self.cli("--undo", receipt), 2)
        sha = self.sha()
        self.offer("tier-1")["benefit"]["value"] = "48.00"  # a dashboard change after the preview
        writes = len(self.writes())
        self.assertEqual(self.cli("--undo", receipt, "--yes", "--edit-sha256", sha), 1)
        self.assertEqual(len(self.writes()), writes)
        self.assertEqual(self.offer("tier-1")["benefit"]["value"], "48.00")

    def test_rollback_refuses_an_object_in_neither_state(self):
        self.t.fail_on[("PATCH", self.offer_path("tier-2"))] = 500
        self.assertEqual(self.edit(self.OPS), 1)
        self.offer("tier-1")["benefit"]["value"] = "47.00"
        self.assertEqual(self.cli("--undo", self.dir / "edit-1-receipt.json"), 1)
        self.assertIn("will not guess", self.error())


if __name__ == "__main__":
    unittest.main()
