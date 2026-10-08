"""Unit tests for `edit` and the adopt/diff/update path on the same run (no network).

The two features were built separately: `edit` changes a field on a campaign a run
of this skill created, and `adopt`/`diff`/`update` reconcile a whole plan against
the store. They share one run directory, one manifest and one plan file, so these
tests are the seams between them: which command refuses while the other is
mid-flight, that the plan and its hash stay in step across both, and that `verify`
and `teardown` see what each of them left behind.
"""
from __future__ import annotations

import json
from pathlib import Path

import test_campaign_admin as base
import test_campaign_edit as edits

ca = base.ca


class EditAndUpdateTogether(base.EndToEndUpdate):
    """The seams between `edit` and the adopt/diff/update path, through ca.main only.
    The two arrived separately: one changes a field on a campaign this skill built,
    the other reconciles a whole plan against the store. They share a run directory,
    a manifest and one plan file, so each has to see what the other left behind."""

    # This class borrows the end-to-end harness, not its cases: those run in their
    # own module, and the loader skips an attribute that is not callable.
    test_adopt_diff_update_verify_on_a_dashboard_campaign = None
    test_recommend_apply_diff_update_verify_teardown_on_a_created_campaign = None

    def created_run(self):
        """recommend + apply, as an operator reaches an editable run."""
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
        return d, plan_path, d / "run-manifest.json"

    def edit_argv(self, plan_path, man_path, ops, name="campaign-edit.json"):
        sp = Path(plan_path).parent / name
        sp.write_text(json.dumps({"operations": ops}) + "\n")
        return ["edit", "--manifest", str(man_path), "--plan", str(plan_path), "--changes", str(sp)]

    def edit(self, plan_path, man_path, ops, name="campaign-edit.json"):
        """Preview, then approve with the hash the preview printed."""
        argv = self.edit_argv(plan_path, man_path, ops, name)
        self.assertEqual(self.main(argv), 2, self.out())
        sha = next(l.split(": ", 1)[1] for l in reversed(self.lines) if l.startswith("Edit SHA-256: "))
        return self.main(argv + ["--yes", "--edit-sha256", sha, "--live-traffic", "no"])

    def candidate(self, d, plan_path, name="campaign-plan.next.json", **_):
        cand = json.loads(Path(plan_path).read_text())
        return cand, d / name

    def write_candidate(self, cand, path):
        Path(path).write_text(json.dumps(cand, indent=2) + "\n")
        return path

    def live_cart(self):
        """The edit suite's cart engine: it prices from what the fake store holds now,
        so a PASS means the plan and the store agree, not that the plan agrees with
        itself."""
        cid = json.loads((self.dir / "run-manifest.json").read_text())["campaign"]["id"]
        t = base.FakeTransport({("POST", "/api/v1/carts/calculate/"):
                                edits.live_engine(self.state, cid)})
        return ca.Client(ca.CART_API_ORIGIN, "k", auth_scheme="raw", send_version_header=False,
                         transport=t, clock=base.FakeClock(), sleep=lambda s: None)

    def assertVerifies(self, plan_path, man_path):
        self.assertEqual(self.main(["verify", "--manifest", str(man_path), "--plan", str(plan_path)],
                                   cart=self.live_cart()), 0, self.out())
        report = json.loads((Path(man_path).parent / "verify-report.json").read_text())
        self.assertEqual(report["result"], "PASS", json.dumps(report, indent=1))
        return report

    # -- 4c: the plan file and its hash stay in step across both commands ---- #
    def test_diff_after_an_edit_sees_no_changes(self):
        d, plan_path, man_path = self.created_run()
        self.assertEqual(self.edit(plan_path, man_path,
                                   [{"op": "set_package_price",
                                     "package_keys": [p["key"] for p in
                                                      json.loads(plan_path.read_text())["packages"]
                                                      if p["role"] == "hero"],
                                     "price": "54.95"}]), 0, self.out())
        man = json.loads(man_path.read_text())
        self.assertEqual(man["plan_sha256"], ca.sha256_file(plan_path),
                         "edit moved the plan and the manifest's hash together")
        # an unedited byte copy of the canonical plan is now the baseline
        again = d / "campaign-plan.again.json"
        again.write_bytes(plan_path.read_bytes())
        self.assertEqual(self.main(["diff", "--plan", str(again), "--manifest", str(man_path)]),
                         0, self.out())
        self.assertIn("no changes", self.out())
        self.assertEqual(self.writes(), [], "diff is read-only")

    def test_update_after_an_edit_then_an_edit_after_the_update(self):
        d, plan_path, man_path = self.created_run()
        heroes = [p["key"] for p in json.loads(plan_path.read_text())["packages"]
                  if p["role"] == "hero"]
        self.assertEqual(self.edit(plan_path, man_path,
                                   [{"op": "set_package_price", "package_keys": heroes,
                                     "price": "54.95"}]), 0, self.out())
        self.assertVerifies(plan_path, man_path)

        # update on top of the edited plan
        cand, nxt = self.candidate(d, plan_path)
        cand["campaign"]["statement_descriptor"] = "BRACELET"
        self.write_candidate(cand, nxt)
        self.assertEqual(self.main(["diff", "--plan", str(nxt), "--manifest", str(man_path)]),
                         0, self.out())
        argv, cs = self.approve(d)
        self.assertEqual([(o["method"], o["section"]) for o in cs["ops"]], [("PATCH", "campaign")])
        self.assertEqual(self.main(argv), 0, self.out())
        self.assertEqual(ca.sha256_file(plan_path), cs["merged_plan_sha256"])
        self.assertVerifies(plan_path, man_path)

        # and an edit on top of the updated plan
        self.assertEqual(self.edit(plan_path, man_path,
                                   [{"op": "set_offer_benefit", "offer_key": "tier-2",
                                     "value": "57"}], name="second-edit.json"), 0, self.out())
        self.assertEqual(json.loads(man_path.read_text())["plan_sha256"],
                         ca.sha256_file(plan_path))
        self.assertEqual(next(o for o in json.loads(plan_path.read_text())["offers"]
                              if o["key"] == "tier-2")["benefit"]["value"], "57.00")
        self.assertVerifies(plan_path, man_path)

    # -- 4b: each refuses while the other is mid-flight ---------------------- #
    def test_diff_and_update_refuse_while_an_edit_is_journalled(self):
        d, plan_path, man_path = self.created_run()
        cand, nxt = self.candidate(d, plan_path)
        cand["campaign"]["statement_descriptor"] = "BRACELET"
        self.write_candidate(cand, nxt)
        self.assertEqual(self.main(["diff", "--plan", str(nxt), "--manifest", str(man_path)]),
                         0, self.out())
        argv, _cs = self.approve(d)
        man = json.loads(man_path.read_text())
        man["pending_edit"] = {"edit": 1, "kind": "edit", "receipt": "edit-1-receipt.json",
                               "old_plan_sha256": man["plan_sha256"],
                               "new_plan_sha256": "f" * 64, "objects": {}, "adds": {}}
        ca.atomic_write_json(man_path, man)
        for cmd in (["diff", "--plan", str(nxt), "--manifest", str(man_path)], argv):
            self.assertEqual(self.main(cmd), 1, self.out())
            self.assertIn("an in-place edit of this run is unfinished", self.out())
            self.assertIn("--undo", self.out())
            self.assertEqual(self.writes(), [])

    def test_edit_refuses_while_an_update_is_in_flight(self):
        d, plan_path, man_path = self.created_run()
        man = json.loads(man_path.read_text())
        man["active_update"] = {"change_set_sha256": "a" * 64, "merged_plan_sha256": "b" * 64,
                                "started_at": ca.utcnow()}
        ca.atomic_write_json(man_path, man)
        argv = self.edit_argv(plan_path, man_path,
                              [{"op": "set_offer_benefit", "offer_key": "tier-2", "value": "57"}])
        self.assertEqual(self.main(argv), 1, self.out())
        self.assertIn(ca.ACTIVE_UPDATE_MSG, self.out())
        self.assertEqual(self.writes(), [])

    # -- 4a: edit opens the run the way every other writing command does ----- #
    def test_edit_finishes_a_promotion_a_crash_interrupted(self):
        d, plan_path, man_path = self.created_run()
        # Step 2 of a promotion: the manifest commits to the new hash and names the
        # bytes to copy. The bytes here are the same plan re-indented, so the store
        # still matches it and only the hash moved.
        archive = d / "campaign-plan.deadbeef.json"
        archive.write_text(json.dumps(json.loads(plan_path.read_text()), indent=4) + "\n")
        new_sha = ca.sha256_file(archive)
        man = json.loads(man_path.read_text())
        self.assertNotEqual(new_sha, man["plan_sha256"])
        man["plan_sha256"] = new_sha
        man["pending_promotion"] = {"archived": archive.name, "sha256": new_sha}
        ca.atomic_write_json(man_path, man)
        # Without the recovery the plan on disk hashes to the old value and the edit
        # would refuse it as not this manifest's plan.
        self.assertEqual(self.edit(plan_path, man_path,
                                   [{"op": "set_offer_benefit", "offer_key": "tier-2",
                                     "value": "57"}]), 0, self.out())
        man = json.loads(man_path.read_text())
        self.assertNotIn("pending_promotion", man)
        self.assertEqual(man["plan_sha256"], ca.sha256_file(plan_path))
        self.assertVerifies(plan_path, man_path)

    # -- 4d: an adopted campaign is the update path's business --------------- #
    def test_edit_refuses_an_adopted_manifest(self):
        cid = base.seed_live_campaign(self.state)
        d = self.dir
        self.assertEqual(self.main(["adopt", "--store", "teststore", "--campaign", str(cid),
                                    "--out", str(d)]), 0, self.out())
        argv = self.edit_argv(d / "campaign-plan.json", d / "run-manifest.json",
                              [{"op": "set_package_price", "package_keys": ["pkg-801"],
                                "price": "19.95"}])
        self.assertEqual(self.main(argv), 1, self.out())
        self.assertIn("was adopted from an existing campaign", self.out())
        self.assertIn("diff and update", self.out())
        self.assertEqual(self.writes(), [])

    # -- 4e: what one removes and what the other adds, read back and deleted - #
    def test_verify_and_teardown_see_an_added_offer_and_a_removed_entry(self):
        d, plan_path, man_path = self.created_run()
        heroes = [p["key"] for p in json.loads(plan_path.read_text())["packages"]
                  if p["role"] == "hero"]
        self.assertEqual(self.edit(plan_path, man_path, [
            {"op": "add_offer", "offer": {
                "key": "loyalty", "name": "Bracelet - Loyalty - 5%", "offer_type": "offer",
                "condition": {"type": "any", "package_keys": heroes},
                "benefit": {"type": "package_percentage", "value": "5", "price_rounding": None}}}]),
            0, self.out())
        self.assertVerifies(plan_path, man_path)

        # an update that deletes the exit voucher, with the added offer still in place
        cand, nxt = self.candidate(d, plan_path)
        cand["offers"] = [o for o in cand["offers"] if o["key"] != "exit-pop"]
        cand["voucher_codes"] = []
        cand["landed_prices"] = [l for l in cand["landed_prices"] if l.get("offer_key") != "exit-pop"]
        self.write_candidate(cand, nxt)
        self.assertEqual(self.main(["diff", "--plan", str(nxt), "--manifest", str(man_path)]),
                         0, self.out())
        argv, cs = self.approve(d)
        self.assertEqual([(o["method"], o["section"], o["key"]) for o in cs["ops"]],
                         [("DELETE", "offers", "exit-pop")])
        self.assertEqual(self.main(argv + ["--allow-delete"]), 0, self.out())
        man = json.loads(man_path.read_text())
        self.assertEqual([r["key"] for r in man["removed"]], ["exit-pop"])
        self.assertIn("loyalty", [e["key"] for e in man["offers"]])

        # verify reads the added offer back and says nothing about the removed one
        report = self.assertVerifies(plan_path, man_path)
        rows = [c["check"] for c in report["admin_checks"]]
        self.assertIn("offer loyalty available", rows)
        self.assertFalse([r for r in rows if "exit-pop" in r])
        self.assertEqual(report["unowned_live_offers"], [])

        # and teardown deletes the offer the edit added, never the removed entry
        removed_id = man["removed"][0]["id"]
        self.assertEqual(self.main(["teardown", "--manifest", str(man_path),
                                    "--plan", str(plan_path), "--yes"]), 0, self.out())
        deleted = [p for m, p in self.writes() if m == "DELETE"]
        loyalty_id = next(e["id"] for e in man["offers"] if e["key"] == "loyalty")
        self.assertTrue(any(p.endswith(f"/offers/{loyalty_id}/") for p in deleted), deleted)
        self.assertFalse([p for p in deleted if p.endswith(f"/offers/{removed_id}/")])
        self.assertEqual(self.state["campaigns"], {})
