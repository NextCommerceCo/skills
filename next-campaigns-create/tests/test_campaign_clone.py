"""Clone approval, recovery and destination ownership regressions, using fake stores only."""
import builtins
import copy
import io
import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest import mock

import test_campaign_admin as base

ca = base.ca
# Synthetic numeric fixture exceeds both float and default Decimal context precision.
PRECISE_PRICE = "12.123456789012345678901234567890123"  # public-safety: allow high-entropy


class Clone(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = Path(self.tmp.name) / "copy"
        self.path = self.out / ca.MANIFEST_NAME
        self.state = base.fresh_state()
        self.state["campaigns"][7] = {
            "id": 7, "name": "Synthetic original", "api_key": "SOURCE-SECRET-DO-NOT-PRINT",
            "created_at": "2026-01-01T00:00:00Z", "currency": "USD", "additional_currencies": ["EUR"],
            "language": "en", "available_shipping_countries": [{"code": "US"}, {"code": "CA"}],
            "payment_gateway_group_id": 1, "available_payment_methods": [{"code": "card"}],
            "available_express_payment_methods": [], "paypal_account_id": None, "statement_descriptor": "SYNTHETIC"}
        self.state["packages"][7] = {42: {"id": 42, "name": "Synthetic package", "product_variant_id": 88,
                                         "prices": [{"currency": "USD", "price": PRECISE_PRICE}]}}
        self.state["shipping-methods"][7] = {9: {"id": 9, "shipping_method": "standard",
                                                 "prices": [{"currency": "USD", "price": "5.00"}]}}
        self.state["offers"][7] = {
            4: {"id": 4, "name": "Synthetic offer", "code": "SYNTHETIC10", "offer_type": "voucher", "available": False,
                "condition": {"type": "count", "value": 1, "all_packages": True, "packages": [{"id": 42}]},
                "benefit": {"type": "package_percentage", "value": "10.00", "price_rounding": None}},
            5: {"id": 5, "is_removed": True}}
        self.t = base.DynamicTransport({}, self.state)
        self.client = base.make_client(self.t, token="ADMIN-SECRET-DO-NOT-PRINT")
        self.source = copy.deepcopy(self.state)

    def approval(self, name=None):
        return ca.clone_approval(self.client, "teststore", 7, name)

    def run_clone(self, name=None, resume=False, sha=None, gate=True):
        argv = ["clone", "--store", "teststore", "--source", "7", "--out", str(self.out)]
        if name is not None:
            argv += ["--name", name]
        if resume:
            argv += ["--resume", str(self.path)]
        if gate:
            sha = sha or (self.man().data["clone_sha256"] if resume else ca.canonical_sha256(self.approval(name)))
            argv += ["--yes", "--clone-sha256", sha]
        with mock.patch.object(ca, "_client_for", return_value=self.client):
            return ca.main(argv)

    def man(self):
        return ca.Manifest.load(self.path)

    def writes(self):
        return [c for c in self.t.calls if c[0] != "GET"]

    def post_count(self):
        return len([c for c in self.t.calls if c[0] == "POST"])

    def lifecycle(self, cmd, *extra):
        with mock.patch.object(ca, "_client_for", return_value=self.client):
            return ca.main([cmd, "--manifest", str(self.path), *extra])

    def assert_source_untouched(self):
        for kind in ("campaigns", "packages", "shipping-methods", "offers"):
            self.assertEqual(self.state[kind][7], self.source[kind][7])

    def test_preview_and_stale_hash_touch_nothing(self):
        for kwargs in ({"gate": False}, {"sha": "stale"}):
            output = io.StringIO()
            with mock.patch.object(ca, "print", builtins.print), mock.patch("sys.stdout", io.StringIO()), mock.patch("sys.stderr", output):
                self.assertEqual(self.run_clone(**kwargs), 2)
            self.assertIn("NOT APPLIED: pass --yes --clone-sha256", output.getvalue())
            self.assertFalse(self.out.exists())
            self.assertEqual(self.writes(), [])

    def test_order_and_decimal_representation_are_canonical(self):
        self.state["packages"][7][43] = dict(self.state["packages"][7][42], id=43, product_variant_id=89)
        before = self.approval()
        self.state["packages"][7] = dict(reversed(list(self.state["packages"][7].items())))
        self.state["campaigns"][7]["available_shipping_countries"].reverse()
        self.state["shipping-methods"][7][9]["prices"][0]["price"] = 5
        self.assertEqual(before, self.approval())
        self.state["packages"][7][42]["prices"][0]["price"] = float(PRECISE_PRICE)
        self.assertNotEqual(before, self.approval())

    def test_json_decimal_decode_preserves_numeric_precision(self):
        precise = PRECISE_PRICE
        transport = lambda req, timeout: (200, {}, '{"price":' + precise + '}')
        client = base.make_client(transport)
        _, row = client.request("GET", "/api/admin/campaigns/7/", clone_decode=True)
        self.assertEqual(ca.clone_number(row["price"]), precise)
        _, row = client.request("GET", "/api/admin/campaigns/7/")
        self.assertIsInstance(row["price"], float)

    def test_named_clone_one_bodyless_post_and_destination_patch(self):
        self.assertEqual(self.run_clone("Copy for testing"), 0)
        cid = self.man().data["campaign"]["id"]
        self.assertEqual([(c[0], c[1], c[2]) for c in self.writes()], [
            ("POST", "/api/admin/campaigns/7/clone/", None),
            ("PATCH", f"/api/admin/campaigns/{cid}/", {"name": "Copy for testing"})])
        self.assertNotEqual(cid, 7)
        self.assertEqual(self.man().data["parity"]["status"], "match")
        self.assertTrue(self.man().data["inventory_complete"])
        self.assertEqual(self.man().data["offers"][0]["code"], "SYNTHETIC10")
        self.assertFalse(self.man().data["offers"][0]["available"])
        self.assertTrue(self.man().data["offers"][0]["condition"]["all_packages"])
        self.assertEqual(list(self.state["offers"][cid]), [4])
        self.state["offers"][cid][4]["condition"]["packages"].clear()
        self.assert_source_untouched()

    def test_unnamed_clone_one_write_and_refuses_overwrite(self):
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(len(self.writes()), 1)
        before = self.path.read_bytes()
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(len(self.writes()), 1)

    def test_keys_protected_in_all_outputs(self):
        output = io.StringIO()
        with mock.patch.object(ca, "print", builtins.print), mock.patch("sys.stdout", output), mock.patch("sys.stderr", output):
            self.assertEqual(self.run_clone(gate=False), 2)
            self.assertEqual(self.run_clone(), 0)
            self.assertEqual(self.lifecycle("verify"), 0)
        for secret in ("SOURCE-SECRET-DO-NOT-PRINT", "ADMIN-SECRET-DO-NOT-PRINT", "CLONE-KEY-REDACTED-101"):
            self.assertNotIn(secret, output.getvalue())
            self.assertNotIn(secret, (self.out / "verify-report.json").read_text())
        self.assertNotIn("SOURCE-SECRET", self.path.read_text())
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual((self.out / "verify-report.json").stat().st_mode & 0o777, 0o600)

    def test_lost_post_recovers_without_repost_even_after_source_drift(self):
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.man().data["clone_request"]["status"], "uncertain")
        self.state["campaigns"][7]["language"] = "fr"
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.post_count(), 1)
        report = ca.clone_verify(self.client, self.man())
        self.assertEqual(report["result"], "PASS")
        self.assertIn("settings.language", report["source_drift"])

    def test_actual_transport_exception_after_commit(self):
        self.t.transport_loss.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.post_count(), 1)

    def test_zero_candidates_and_5xx_never_repost(self):
        self.t.fail_on[("POST", "/api/admin/campaigns/7/clone/")] = 503
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertEqual(self.post_count(), 1)

    def test_ambiguous_candidates_never_adopt(self):
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.state["campaigns"][102] = dict(self.state["campaigns"][101], id=102)
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertNotIn("id", self.man().data["campaign"])
        self.assertEqual(self.post_count(), 1)

    def test_after_deadline_and_missing_window_never_adopt(self):
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        man = self.man()
        deadline = ca._parse_instant(man.data["clone_request"]["attempt_deadline"])
        self.state["campaigns"][101]["created_at"] = (deadline + timedelta(seconds=1)).isoformat()
        self.assertEqual(self.run_clone(resume=True), 1)
        self.state["campaigns"][101]["created_at"] = man.data["clone_request"]["attempted_at"]
        del man.data["clone_request"]["attempt_deadline"]
        man.save()
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertNotIn("id", self.man().data["campaign"])
        self.assertEqual(self.post_count(), 1)

    def test_preexisting_candidate_excluded(self):
        self.state["campaigns"][99] = dict(self.state["campaigns"][7], id=99, name="Synthetic original-COPY", created_at=ca.utcnow())
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.man().data["campaign"]["id"], 101)

    def test_unique_candidate_must_match_content(self):
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.state["offers"][101][4]["code"] = "OTHER"
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertNotIn("id", self.man().data["campaign"])

    def test_rename_lost_response_readback_and_ignored_patch(self):
        self.t.lose.add(("PATCH", "/api/admin/campaigns/101/"))
        self.assertEqual(self.run_clone("Renamed"), 0)
        self.assertEqual(self.run_clone("Renamed", resume=True), 0)
        self.assertEqual(len([c for c in self.writes() if c[0] == "PATCH"]), 1)

    def test_rename_transport_loss_uses_readback_without_repatch(self):
        self.t.transport_loss.add(("PATCH", "/api/admin/campaigns/101/"))
        self.assertEqual(self.run_clone("Renamed"), 0)
        self.assertEqual(self.run_clone("Renamed", resume=True), 0)
        self.assertEqual(self.man().data["rename"]["status"], "done")
        self.assertEqual(len([c for c in self.writes() if c[0] == "PATCH"]), 1)

    def test_ignored_rename_is_pending_and_resume_checks_identity(self):
        self.t.ignore_patch.add(("PATCH", "/api/admin/campaigns/101/"))
        self.assertEqual(self.run_clone("Renamed"), 1)
        self.assertEqual(self.man().data["rename"]["status"], "pending")
        self.t.ignore_patch.clear()
        self.state["campaigns"][101]["name"] = "Unexpected"
        self.assertEqual(self.run_clone("Renamed", resume=True), 1)
        self.assertEqual(len([c for c in self.writes() if c[0] == "PATCH"]), 1)
        self.state["campaigns"][101]["name"] = "Renamed"
        self.assertEqual(self.run_clone("Renamed", resume=True), 0)
        self.assertEqual(len([c for c in self.writes() if c[0] == "PATCH"]), 1)

    def test_endpoint_and_auth_failures_are_safe(self):
        for status, message in ((404, "deployment"), (405, "deployment"), (401, "API key"), (403, "campaigns:write")):
            with self.subTest(status=status):
                self.out = Path(self.tmp.name) / str(status)
                self.path = self.out / ca.MANIFEST_NAME
                self.t.fail_on[("POST", "/api/admin/campaigns/7/clone/")] = status
                output = io.StringIO()
                with mock.patch.object(ca, "print", builtins.print), mock.patch("sys.stderr", output):
                    self.assertEqual(self.run_clone(), 1)
                self.assertIn(message, output.getvalue())
                self.assertEqual(self.man().data["clone_request"]["status"], "rejected")
                self.assertNotIn("id", self.man().data["campaign"])
                self.assertEqual(self.run_clone(resume=True), 1)

    def test_verify_detects_price_id_code_and_settings_changes(self):
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "PASS")
        mutations = [lambda: self.state["packages"][101][42]["prices"][0].update(price="12.123456789012346"),
                     lambda: self.state["packages"][101][42].update(id=999),
                     lambda: self.state["offers"][101][4].update(code="CHANGED"),
                     lambda: self.state["campaigns"][101].update(language="fr")]
        saved = copy.deepcopy(self.state)
        for mutate in mutations:
            mutate()
            self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "FAIL")
            self.state.clear()
            self.state.update(copy.deepcopy(saved))

    def test_teardown_only_destination_and_rejects_source_substitution(self):
        self.assertEqual(self.run_clone(), 0)
        man = self.man()
        man.data["campaign"]["id"] = 7
        man.save()
        self.assertEqual(self.lifecycle("teardown", "--yes"), 1)
        self.assertFalse([c for c in self.writes() if c[0] == "DELETE"])
        man.data["campaign"]["id"] = 101
        man.save()
        self.assertEqual(self.lifecycle("teardown", "--yes"), 0)
        self.assert_source_untouched()
        self.assertNotIn(101, self.state["campaigns"])
        self.assertTrue(all("/101/" in c[1] for c in self.writes() if c[0] == "DELETE"))

    def test_teardown_of_already_deleted_destination_finalizes_manifest(self):
        self.assertEqual(self.run_clone(), 0)
        del self.state["campaigns"][101]
        self.assertEqual(self.lifecycle("teardown", "--yes"), 0)
        man = self.man().data
        self.assertTrue(man.get("torn_down_at"))
        self.assertEqual(man["campaign"]["status"], "deleted")
        self.assertTrue(all(e["status"] == "deleted" for s in ("offers", "shipping_methods", "packages") for e in man[s]))
        self.assertFalse([c for c in self.writes() if c[0] == "DELETE"])

    def test_empty_body_decodes_to_none_on_both_paths(self):
        client = base.make_client(lambda req, timeout: (200, {}, ""))
        self.assertEqual(client.request("GET", "/api/admin/campaigns/7/", clone_decode=True), (200, None))
        self.assertEqual(client.request("GET", "/api/admin/campaigns/7/"), (200, None))
        with self.assertRaises(ca.CampaignAdminError):
            ca.clone_get(client, "/api/admin/campaigns/7/")

    def test_parity_mismatch_still_owned_and_tears_down(self):
        self.t.before[("GET", "/api/admin/campaigns/101/shipping-methods/")] = lambda: self.state["shipping-methods"][101][9]["prices"][0].update(price="9.99")
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(self.man().data["parity"]["status"], "mismatch")
        self.assertTrue(self.man().data.get("completed_at"))
        self.assertEqual(self.lifecycle("verify"), 1)
        self.assertEqual(self.lifecycle("teardown", "--yes"), 0)
        self.assert_source_untouched()

    def interrupt_inventory(self):
        original = self.t.child_list
        # Only fail the destination, source reads still complete.
        self.t.child_list = lambda kind: (lambda path, body: (500, {}) if "/101/" in path else original(kind)(path, body)) if kind == "packages" else original(kind)
        self.assertEqual(self.run_clone(), 1)
        self.assertEqual(self.man().data["campaign"]["id"], 101)
        self.assertFalse(self.man().data["inventory_complete"])
        self.t.child_list = original

    def test_inventory_failure_resume_is_read_only(self):
        self.interrupt_inventory()
        writes = self.writes()
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.writes(), writes)

    def test_inventory_failure_teardown_refreshes(self):
        self.interrupt_inventory()
        self.assertEqual(self.lifecycle("teardown", "--yes"), 0)
        self.assert_source_untouched()
        self.assertNotIn(101, self.state["campaigns"])

    def test_interrupted_inventory_never_claims_dashboard_additions(self):
        self.interrupt_inventory()
        self.state["offers"][101][77] = dict(self.state["offers"][101][4], id=77, name="Dashboard offer")
        self.state["packages"][101][43] = dict(self.state["packages"][101][42], id=43, product_variant_id=89)
        # Owned children go; the campaign stays because deleting it would cascade
        # through the dashboard additions the run does not own.
        self.assertEqual(self.lifecycle("teardown", "--yes"), 1)
        deleted = [c[1] for c in self.writes() if c[0] == "DELETE"]
        self.assertTrue(any(p.endswith("/offers/4/") for p in deleted))
        self.assertFalse(any(p.endswith("/offers/77/") or p.endswith("/packages/43/") for p in deleted))
        self.assertFalse(any(p.endswith("/campaigns/101/") for p in deleted))
        self.assertIn(101, self.state["campaigns"])
        self.assertEqual([e["id"] for e in self.man().data["offers"]], [4])
        self.assertEqual([e["id"] for e in self.man().data["packages"]], [42])
        self.assertNotIn("torn_down_at", self.man().data)
        # Once the operator removes them in the dashboard, teardown finishes.
        del self.state["offers"][101][77]
        del self.state["packages"][101][43]
        self.assertEqual(self.lifecycle("teardown", "--yes"), 0)
        self.assertNotIn(101, self.state["campaigns"])

    def test_recovery_excludes_renamed_preexisting_campaign(self):
        self.state["campaigns"][98] = dict(self.state["campaigns"][7], id=98, name="Unrelated", created_at=ca.utcnow())
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.assertIn(98, self.man().data["clone_request"]["preexisting_ids"])
        del self.state["campaigns"][101]
        self.state["campaigns"][98]["name"] = "Synthetic original-COPY"
        self.assertEqual(self.run_clone(resume=True), 1)
        self.assertNotIn("id", self.man().data["campaign"])
        self.assertEqual(self.post_count(), 1)

    def test_verify_without_source_and_missing_currency_price(self):
        self.assertEqual(self.run_clone(), 0)
        del self.state["campaigns"][7]
        report = ca.clone_verify(self.client, self.man())
        self.assertEqual(report["result"], "PASS")
        self.assertTrue(report["source_drift"][0].startswith("source unavailable"))
        self.state["packages"][101][42]["prices"] = [p for p in self.state["packages"][101][42]["prices"] if p["currency"] != "EUR"]
        report = ca.clone_verify(self.client, self.man())
        self.assertEqual(report["result"], "FAIL")
        self.assertIn("packages.42.currency_coverage", [c["check"] for c in report["admin_checks"] if c["result"] == "FAIL"])

    def test_incomplete_inventory_substitute_time_rejected(self):
        self.interrupt_inventory()
        original = self.state["campaigns"][101]["created_at"]
        self.state["campaigns"][101]["created_at"] = (ca._parse_instant(original) + timedelta(seconds=1)).isoformat()
        self.assertEqual(self.lifecycle("teardown", "--yes"), 1)
        self.assertFalse([c for c in self.writes() if c[0] == "DELETE"])

    def test_lock_blocks_clone_resume_and_teardown_without_changes(self):
        self.assertEqual(self.run_clone(), 0)
        before, writes = self.path.read_bytes(), self.writes()
        with ca.run_lock(self.out):
            for action in (lambda: self.run_clone(), lambda: self.run_clone(resume=True),
                           lambda: self.lifecycle("teardown", "--yes")):
                self.assertEqual(action(), 1)
                self.assertEqual(self.path.read_bytes(), before)
                self.assertEqual(self.writes(), writes)

    def test_fresh_prepost_snapshot_required(self):
        sha = ca.canonical_sha256(self.approval())
        original = ca.clone_approval
        count = [0]
        def changed(*args):
            count[0] += 1
            if count[0] == 2:
                self.state["campaigns"][7]["language"] = "fr"
            return original(*args)
        with mock.patch.object(ca, "clone_approval", side_effect=changed):
            self.assertEqual(self.run_clone(sha=sha), 1)
        self.assertEqual(self.writes(), [])
        self.assertEqual(self.man().data["clone_request"]["status"], "not_sent")
        self.assertEqual(self.run_clone(resume=True), 1)

    def test_missing_fields_and_partial_collections_fail_before_writes(self):
        del self.state["offers"][7][4]["condition"]["packages"]
        self.assertEqual(self.run_clone(gate=False), 1)
        self.assertFalse(self.out.exists())
        self.state["offers"][7][4] = copy.deepcopy(self.source["offers"][7][4])
        # An envelope whose advertised count does not match the rows read is a partial read.
        self.t.child_list = lambda kind: lambda path, body: (200, {"count": 1, "results": [], "next": None})
        self.assertEqual(self.run_clone(gate=False), 1)
        self.assertEqual(self.writes(), [])

    def test_results_envelope_without_next_is_a_single_page(self):
        original = self.t.child_list
        self.t.child_list = lambda kind: (lambda path, body: (200, {"results": [{"id": 4}]})) if kind == "offers" else original(kind)
        self.assertEqual(len(self.approval()["snapshot"]["offers"]), 1)

    def test_subscription_fields_preserved_and_verified(self):
        self.state["packages"][7][42].update(is_recurring=True, interval="month", interval_count=1)
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(self.man().data["packages"][0]["interval"], "month")
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "PASS")
        self.state["packages"][101][42]["interval_count"] = 3
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "FAIL")

    def test_pagination_reads_every_page_and_offer_detail(self):
        original = self.t.child_list
        def pages(kind):
            if kind == "offers":
                return lambda path, body: (200, {"results": [{"id": 4}], "next": None} if "page=2" in path else
                                          {"results": [], "next": base.ORIGIN + "/api/admin/campaigns/7/offers/?page=2"})
            return original(kind)
        self.t.child_list = pages
        self.assertEqual(len(self.approval()["snapshot"]["offers"]), 1)
        self.assertTrue(any(c[1].endswith("/offers/4/") for c in self.t.calls))

    def test_truncated_counted_collection_refuses_preview_without_files(self):
        self.t.child_list = lambda kind: lambda path, body: (200, {
            "count": 2, "results": [], "next": None})
        self.assertEqual(self.run_clone(gate=False), 1)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.writes(), [])

    def test_changing_count_during_pagination_refuses_snapshot(self):
        self.t.child_list = lambda kind: lambda path, body: (200, {
            "count": 1, "results": [], "next": None} if "page=2" in path else {
            "count": 2, "results": [],
            "next": base.ORIGIN + "/api/admin/campaigns/7/packages/?page=2"})
        self.assertEqual(self.run_clone(gate=False), 1)
        self.assertFalse(self.out.exists())
        self.assertEqual(self.writes(), [])

    def test_malformed_201_is_uncertain(self):
        original = self.t._handle
        def malformed(method, path, *args):
            answer = original(method, path, *args)
            return (201, {}, '{"api_key":"DO-NOT-ECHO"}') if path.endswith("/clone/") else answer
        self.t._handle = malformed
        output = io.StringIO()
        with mock.patch.object(ca, "print", builtins.print), mock.patch("sys.stderr", output):
            self.assertEqual(self.run_clone(), 1)
        self.assertNotIn("DO-NOT-ECHO", output.getvalue())
        self.assertEqual(self.man().data["clone_request"]["status"], "uncertain")
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.post_count(), 1)

    def test_completed_resume_does_not_claim_dashboard_additions(self):
        self.assertEqual(self.run_clone(), 0)
        inventory = self.man().data["offers"]
        self.state["offers"][101][77] = dict(self.state["offers"][101][4], id=77, name="Dashboard offer")
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.man().data["offers"], inventory)
        self.assertEqual(self.man().data["parity"]["status"], "mismatch")

    def test_exposed_resets_and_forex_report(self):
        self.state["campaigns"][7]["enable_retail_price_and_quantity"] = True
        self.state["offers"][7][4]["num_orders"] = 12
        self.assertEqual(self.run_clone(), 0)
        report = ca.clone_verify(self.client, self.man())
        self.assertEqual(report["result"], "PASS")
        self.assertTrue(report["forex_derived_prices"])
        self.assertEqual(report["cart_probes"]["status"], "skipped")
        self.state["offers"][101][4]["num_orders"] = 1
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "FAIL")

    def test_completed_rename_is_not_reapplied_over_dashboard_change(self):
        self.assertEqual(self.run_clone("Renamed"), 0)
        before = self.writes()
        self.state["campaigns"][101]["name"] = "Synthetic original-COPY"
        output = io.StringIO()
        with mock.patch.object(ca, "print", builtins.print), mock.patch("sys.stderr", output):
            self.assertEqual(self.run_clone("Renamed", resume=True), 1)
        self.assertIn("dashboard", output.getvalue())
        self.assertEqual(self.writes(), before)

    def test_missing_creation_identity_refuses_teardown(self):
        self.assertEqual(self.run_clone(), 0)
        man = self.man()
        man.data["campaign"]["created_at"] = None
        man.save()
        self.state["campaigns"][101]["created_at"] = None
        self.assertEqual(self.lifecycle("teardown", "--yes"), 1)
        self.assertFalse([c for c in self.writes() if c[0] == "DELETE"])

    def test_recovery_paginates_campaign_candidates(self):
        self.t.lose.add(("POST", "/api/admin/campaigns/7/clone/"))
        self.assertEqual(self.run_clone(), 1)
        self.t.routes[("GET", "/api/admin/campaigns/")] = lambda path, body: (200, {
            "results": [self.state["campaigns"][101]], "next": None} if "page=2" in path else {
            "results": [self.state["campaigns"][7]], "next": base.ORIGIN + "/api/admin/campaigns/?page=2"})
        self.assertEqual(self.run_clone(resume=True), 0)
        self.assertEqual(self.post_count(), 1)

    def test_supported_additional_price_preserved_and_verified(self):
        self.state["packages"][7][42]["prices"].append({"currency": "EUR", "price": "10.12345678901234567890123"})
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "PASS")
        self.state["packages"][101][42]["prices"][1]["price"] = "10.12345678901234568"
        self.assertEqual(ca.clone_verify(self.client, self.man())["result"], "FAIL")

    def test_resume_binding_and_plan_refusal(self):
        self.assertEqual(self.run_clone(), 0)
        self.assertEqual(self.run_clone("Different", resume=True), 1)
        self.assertEqual(self.lifecycle("verify", "--plan", "unrelated.json"), 1)
        self.assertEqual(self.lifecycle("teardown", "--plan", "unrelated.json", "--yes"), 1)
        man = self.man()
        man.data["clone_approval"]["snapshot"]["settings"]["language"] = "xx"
        man.save()
        self.assertEqual(self.run_clone(resume=True), 1)


if __name__ == "__main__":
    unittest.main()
