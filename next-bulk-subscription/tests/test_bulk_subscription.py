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
        sid = endpoint.split("/")[1]
        if sid in self.failures: raise urllib.error.HTTPError("url", 422, "bad", {}, None)
        return {"shipping_address": {"line1": "Secret St"}, "card_last4": "4242"}


class BulkSubscriptionTests(unittest.TestCase):
    def run_bulk(self, client, rows, execute=True, resume=None, action="pause", payload=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); output = Path(td.name) / "results.csv"
        if payload is None:
            payload = {"pause_until": "2026-08-01"}
        worker = bulk.BulkSubscription(client, action, payload, execute=execute,
                                       row_delay=0, sleep=lambda _: None)
        results = worker.run(rows, output, bulk.resume_completed(resume)); return results, output

    def test_non_allowlisted_action_refused(self):
        with self.assertRaisesRegex(ValueError, "not allowlisted"):
            bulk.BulkSubscription(FakeClient(), "delete", {}, execute=True)

    def test_response_pii_never_appears_in_results(self):
        _, output = self.run_bulk(FakeClient(), [{"subscription_id": "s1", "order_id": "o1", "customer_id": "c1"}])
        text = output.read_text(); self.assertNotIn("Secret St", text); self.assertNotIn("4242", text); self.assertNotIn("address", text.lower()); self.assertNotIn("card", text.lower())

    def test_dry_run_zero_mutating_calls(self):
        client = FakeClient(); rows, _ = self.run_bulk(client, [{"subscription_id": "s1"}], execute=False)
        self.assertEqual([], client.calls); self.assertEqual("dry_run", rows[0].status)

    def test_resume_skips_completed(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); prior = Path(td.name) / "prior.csv"
        with prior.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader()
            w.writerow({"subscription_id": "s1", "action": "pause",
                        "payload_fingerprint": bulk.fingerprint(
                            {"pause_until": "2026-08-01"}), "status": "success"})
        client = FakeClient(); self.run_bulk(client, [{"subscription_id": "s1"}, {"subscription_id": "s2"}], resume=prior)
        self.assertEqual(1, len(client.calls)); self.assertIn("s2", client.calls[0][1])

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


if __name__ == "__main__": unittest.main()
