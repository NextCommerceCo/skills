import csv
import email.message
import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
import urllib.response
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).parents[1] / "scripts" / "bulk_subscription.py"
spec = importlib.util.spec_from_file_location("bulk_subscription", MODULE)
bulk = importlib.util.module_from_spec(spec); sys.modules[spec.name] = bulk
spec.loader.exec_module(bulk)


class FakeClient:
    def __init__(self, failures=()): self.calls, self.failures = [], set(failures)
    def mutate(self, method, endpoint, payload):
        self.calls.append((method, endpoint, payload))
        parts = endpoint.strip("/").split("/")
        sid = parts[1]
        if sid in self.failures: raise urllib.error.HTTPError("url", 422, "bad", {}, None)
        action = parts[2] if len(parts) > 2 else "update"
        # Confirming response: echoes id + applied fields and a lifecycle status
        # proving the action took effect, plus PII the results CSV must redact.
        status = {"pause": "paused", "cancel": "canceled",
                  "resume": "active", "renew": "active"}.get(action)
        resp = {"id": sid, **payload,
                "shipping_address": {"line1": "Secret St"}, "card_last4": "4242"}
        if status:
            resp["status"] = status
        if action == "renew" and "next_renewal_date" not in resp:
            resp["next_renewal_date"] = "2026-09-01T00:00:00Z"
        return resp


class BulkSubscriptionTests(unittest.TestCase):
    def run_bulk(self, client, rows, execute=True, resume=None, action="pause", payload=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); output = Path(td.name) / "results.csv"
        if payload is None:
            payload = {"pause_until": "2026-08-01"}
        worker = bulk.BulkSubscription(client, action, payload, execute=execute,
                                       row_delay=0, sleep=lambda _: None)
        results = worker.run(rows, output, bulk.resume_completed(resume)); return results, output

    def test_negative_limit_is_rejected(self):
        with self.assertRaises(SystemExit):
            bulk.parser().parse_args(["--store", "s", "--input", "i.csv",
                                      "--results", "o.csv", "--action", "pause",
                                      "--execute", "--limit", "-1"])

    def test_resume_identity_only_response_is_needs_verification(self):
        class IdentityOnlyClient(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                sid = endpoint.split("/")[1]
                return {"id": sid}  # no status/effect signal for resume
        client = IdentityOnlyClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"}],
                                action="resume", payload={})
        self.assertEqual("error", rows[0].status)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_resume_response_still_paused_is_needs_verification(self):
        class StillPausedClient(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                sid = endpoint.split("/")[1]
                return {"id": sid, "status": "paused"}  # resume did not take effect
        client = StillPausedClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"}],
                                action="resume", payload={})
        self.assertEqual("error", rows[0].status)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_renew_identity_only_response_is_needs_verification(self):
        class IdOnly(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return {"id": endpoint.strip("/").split("/")[1]}
        rows, _ = self.run_bulk(IdOnly(), [{"subscription_id": "s1"}],
                                action="renew", payload={})
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_cancel_notification_echo_only_is_needs_verification(self):
        class EchoOnly(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return {"id": endpoint.strip("/").split("/")[1],
                        "send_cancel_notification": True}
        rows, _ = self.run_bulk(EchoOnly(), [{"subscription_id": "s1"}],
                                action="cancel",
                                payload={"send_cancel_notification": True})
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_pause_null_date_only_is_needs_verification(self):
        class NullDate(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return {"id": endpoint.strip("/").split("/")[1], "pause_until": None}
        rows, _ = self.run_bulk(NullDate(), [{"subscription_id": "s1"}],
                                action="pause", payload={})
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_non_allowlisted_action_refused(self):
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            bulk.BulkSubscription(FakeClient(), "delete", {}, execute=True)

    def test_response_pii_never_appears_in_results(self):
        _, output = self.run_bulk(FakeClient(), [{"subscription_id": "s1", "order_id": "o1", "customer_id": "c1"}])
        text = output.read_text(); self.assertNotIn("Secret St", text); self.assertNotIn("4242", text); self.assertNotIn("address", text.lower()); self.assertNotIn("card", text.lower())

    def test_dry_run_zero_mutating_calls(self):
        client = FakeClient(); rows, output = self.run_bulk(client, [{"subscription_id": "s1"}], execute=False)
        self.assertEqual([], client.calls); self.assertEqual("dry_run", rows[0].status)
        with output.open(newline="") as handle:
            self.assertNotIn("attempted", [row["status"] for row in csv.DictReader(handle)])

    def test_resume_skips_completed(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); prior = Path(td.name) / "prior.csv"
        with prior.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader()
            w.writerow({"subscription_id": "s1", "action": "pause",
                        "payload_fingerprint": bulk.fingerprint(
                            {"pause_until": "2026-08-01"}), "status": "success"})
        client = FakeClient(); self.run_bulk(client, [{"subscription_id": "s1"}, {"subscription_id": "s2"}], resume=prior)
        self.assertEqual(1, len(client.calls)); self.assertIn("s2", client.calls[0][1])

    def test_resume_attempted_requires_verification_without_post(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        prior = Path(td.name) / "prior.csv"
        payload_id = bulk.fingerprint({"pause_until": "2026-08-01"})
        with prior.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=bulk.FIELDS); writer.writeheader()
            writer.writerow({"subscription_id": "s1", "action": "pause",
                             "payload_fingerprint": payload_id,
                             "status": "attempted"})
        self.assertIn(("s1", "pause", payload_id), bulk.resume_completed(prior))
        client = FakeClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"}], resume=prior)
        self.assertEqual([], client.calls)
        self.assertEqual(["NEEDS_VERIFICATION"], [row.error_code for row in rows])
        self.assertEqual(["error"], [row.status for row in rows])

    def test_resume_needs_verification_row_stays_unresolved_without_post(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        prior = Path(td.name) / "prior.csv"
        payload_id = bulk.fingerprint({"pause_until": "2026-08-01"})
        with prior.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=bulk.FIELDS); writer.writeheader()
            writer.writerow({"subscription_id": "s1", "action": "pause",
                             "payload_fingerprint": payload_id, "status": "error",
                             "error_code": "NEEDS_VERIFICATION"})
        state = bulk.resume_completed(prior)
        self.assertIn(("s1", "pause", payload_id), state.needs_verification)
        client = FakeClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"}], resume=prior)
        self.assertEqual([], client.calls)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_success_writes_attempted_then_success_and_resume_skips(self):
        client = FakeClient()
        _, output = self.run_bulk(client, [{"subscription_id": "s1"}])
        with output.open(newline="") as handle:
            written = list(csv.DictReader(handle))
        self.assertEqual(["attempted", "success"],
                         [row["status"] for row in written])
        resumed = FakeClient()
        rows, _ = self.run_bulk(resumed, [{"subscription_id": "s1"}], resume=output)
        self.assertEqual([], resumed.calls)
        self.assertEqual([], rows)

    def test_csv_pause_until_overrides_shared_payload_per_row(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        source = Path(td.name) / "subscriptions.csv"
        with source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["subscription_id", "pause_until"])
            writer.writeheader()
            writer.writerow({"subscription_id": "s1", "pause_until": "2026-08-01"})
            writer.writerow({"subscription_id": "s2", "pause_until": "2026-09-15"})
        client = FakeClient()
        self.run_bulk(client, bulk.read_rows(source),
                      payload={"pause_until": "2026-07-20"})
        self.assertEqual(
            [{"pause_until": "2026-08-01"}, {"pause_until": "2026-09-15"}],
            [call[2] for call in client.calls],
        )

    def test_renew_action_posts_to_documented_endpoint(self):
        client = FakeClient()
        self.run_bulk(client, [{"subscription_id": "s1"}], action="renew", payload={})
        self.assertEqual(
            [("POST", "subscriptions/s1/renew/", {})],
            client.calls,
        )

    def test_empty_update_is_flagged_without_mutation(self):
        client = FakeClient()
        rows, _ = self.run_bulk(
            client,
            [{"subscription_id": "s1"}],
            action="update",
            payload={},
        )
        self.assertEqual([], client.calls)
        self.assertEqual("error", rows[0].status)
        self.assertEqual("EMPTY_UPDATE", rows[0].error_code)

    def test_resume_pause_does_not_skip_cancel(self):
        pause_client = FakeClient()
        _, prior = self.run_bulk(pause_client, [{"subscription_id": "s1"}])
        cancel_client = FakeClient()
        self.run_bulk(cancel_client, [{"subscription_id": "s1"}], resume=prior,
                      action="cancel", payload={})
        self.assertEqual(
            [("POST", "subscriptions/s1/cancel/", {})],
            cancel_client.calls,
        )

    def test_resume_requires_matching_payload(self):
        pause_client = FakeClient()
        _, prior = self.run_bulk(pause_client, [{"subscription_id": "s1"}],
                                 payload={"pause_until": "2026-08-01"})
        changed_client = FakeClient()
        self.run_bulk(changed_client, [{"subscription_id": "s1"}], resume=prior,
                      payload={"pause_until": "2026-09-01"})
        self.assertEqual(
            {"pause_until": "2026-09-01"},
            changed_client.calls[0][2],
        )

    def test_partial_failure_continues(self):
        client = FakeClient({"s2"}); rows, _ = self.run_bulk(client, [{"subscription_id": x} for x in ("s1", "s2", "s3")])
        self.assertEqual(3, len(client.calls)); self.assertEqual(["success", "error", "success"], [r.status for r in rows])

    def test_authenticated_post_refuses_cross_host_redirect(self):
        client = bulk.AdminClient("mystore", "secret", min_interval=0)
        target, seen = "https://evil.example/collect", []

        class RedirectTransport(urllib.request.BaseHandler):
            handler_order = 100

            def https_open(self, request):
                seen.append(request)
                headers = email.message.Message(); headers["Location"] = target
                response = urllib.response.addinfourl(
                    io.BytesIO(b""), headers, request.full_url, code=302)
                response.msg = "Found"
                return response

        client.opener = urllib.request.build_opener(
            bulk.AuthenticatedRedirectHandler(), RedirectTransport())
        with self.assertRaisesRegex(urllib.error.HTTPError,
                                    "refusing authenticated redirect to " + target):
            client.mutate("POST", "subscriptions/s1/retry/", {})
        self.assertEqual(1, len(seen))
        self.assertEqual("Bearer secret", seen[0].get_header("Authorization"))
        self.assertEqual("POST", seen[0].get_method())

    def test_duplicate_successful_id_is_not_mutated_twice(self):
        client = FakeClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"},
                                         {"subscription_id": "s1"}])
        self.assertEqual(1, len(client.calls))
        self.assertEqual(["success", "skipped"], [row.status for row in rows])
        self.assertEqual("DUPLICATE", rows[1].action)

    def test_duplicate_after_mutation_error_is_not_mutated_twice(self):
        client = FakeClient({"s1"})
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1"},
                                         {"subscription_id": "s1"}])
        self.assertEqual(1, len(client.calls))
        self.assertEqual(["error", "skipped"], [row.status for row in rows])
        self.assertEqual("DUPLICATE", rows[1].action)

    def test_same_id_with_distinct_payloads_is_mutated_twice(self):
        client = FakeClient()
        rows, _ = self.run_bulk(client, [
            {"subscription_id": "s1", "pause_until": "2026-08-01"},
            {"subscription_id": "s1", "pause_until": "2026-09-01"},
        ])
        self.assertEqual(2, len(client.calls))
        self.assertEqual(["success", "success"], [row.status for row in rows])


    def test_resume_state_merges_active_results_journal(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        active = Path(td.name) / "results.csv"
        with active.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader()
            w.writerow({"subscription_id": "s1", "action": "pause",
                        "payload_fingerprint": bulk.fingerprint({"pause_until": "2026-08-01"}),
                        "status": "success"})
        # Older --resume file is empty/missing; the active --results journal must
        # still be consulted so a re-run does not repeat s1.
        state = bulk.resume_state(None, active)
        key = ("s1", "pause", bulk.fingerprint({"pause_until": "2026-08-01"}))
        self.assertIn(key, state)

    def test_resume_reads_foreign_header_fails_closed(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        stale = Path(td.name) / "stale.csv"
        stale.write_text("foo,bar\n1,2\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            bulk.resume_state(stale, None)

    def test_unresolved_attempt_wins_over_older_success_across_journals(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        fp = bulk.fingerprint({})
        old = Path(td.name) / "old.csv"; new = Path(td.name) / "new.csv"
        for path, status in ((old, "success"), (new, "attempted")):
            with path.open("w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader()
                w.writerow({"subscription_id": "s1", "action": "renew",
                            "payload_fingerprint": fp, "status": status})
        state = bulk.resume_state(old, new)
        self.assertIn(("s1", "renew", fp), state.needs_verification)

    def test_incompatible_results_header_fails_closed(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        output = Path(td.name) / "results.csv"
        output.write_text("some,foreign,header\n1,2,3\n", encoding="utf-8")
        worker = bulk.BulkSubscription(FakeClient(), "pause", {"pause_until": "2026-08-01"},
                                       execute=True, row_delay=0, sleep=lambda _: None)
        with self.assertRaises(ValueError):
            worker.run([{"subscription_id": "s1", "pause_until": "2026-08-01"}], output)

    def test_update_response_missing_requested_field_is_not_success(self):
        class OmitsFieldClient(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                sid = endpoint.split("/")[1]
                return {"id": sid}  # 2xx, right id, but no confirmation of the field
        client = OmitsFieldClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1", "interval": "monthly"}],
                                action="update", payload={})
        self.assertEqual("error", rows[0].status)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_unconfirmed_response_is_not_recorded_as_success(self):
        class NonConfirmingClient(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return {}  # 2xx but no identity/effect confirmation
        client = NonConfirmingClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1", "pause_until": "2026-08-01"}])
        self.assertEqual(1, len(client.calls))
        self.assertEqual("error", rows[0].status)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_response_with_wrong_field_value_is_not_success(self):
        class WrongValueClient(FakeClient):
            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                sid = endpoint.split("/")[1]
                return {"id": sid, "pause_until": "1999-01-01"}  # not what we asked
        client = WrongValueClient()
        rows, _ = self.run_bulk(client, [{"subscription_id": "s1", "pause_until": "2026-08-01"}])
        self.assertEqual("error", rows[0].status)
        self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)

    def test_pause_requires_and_validates_an_effect_confirmation(self):
        class ResponseClient(FakeClient):
            def __init__(self, response):
                super().__init__(); self.response = response

            def mutate(self, method, endpoint, payload):
                self.calls.append((method, endpoint, payload))
                return self.response

        row = {"subscription_id": "s1"}
        cases = (
            ({"id": "s1"}, "error"),
            ({"id": "s1", "status": "paused",
              "pause_until": "2026-08-01"}, "success"),
            ({"id": "s1", "status": "active"}, "error"),
        )
        for response, expected in cases:
            with self.subTest(response=response):
                rows, _ = self.run_bulk(ResponseClient(response), [row])
                self.assertEqual(expected, rows[0].status)
                if expected == "error":
                    self.assertEqual("NEEDS_VERIFICATION", rows[0].error_code)


if __name__ == "__main__": unittest.main()
