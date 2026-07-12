import csv
import importlib.util
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path

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
    def run_bulk(self, client, rows, execute=True, resume=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); output = Path(td.name) / "results.csv"
        worker = bulk.BulkSubscription(client, "pause", {"pause_until": "2026-08-01"}, execute=execute, row_delay=0, sleep=lambda _: None)
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
            w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader(); w.writerow({"subscription_id": "s1", "status": "success"})
        client = FakeClient(); self.run_bulk(client, [{"subscription_id": "s1"}, {"subscription_id": "s2"}], resume=prior)
        self.assertEqual(1, len(client.calls)); self.assertIn("s2", client.calls[0][1])

    def test_partial_failure_continues(self):
        client = FakeClient({"s2"}); rows, _ = self.run_bulk(client, [{"subscription_id": x} for x in ("s1", "s2", "s3")])
        self.assertEqual(3, len(client.calls)); self.assertEqual(["success", "error", "success"], [r.status for r in rows])


if __name__ == "__main__": unittest.main()
