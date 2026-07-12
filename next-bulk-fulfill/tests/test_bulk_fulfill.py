import csv
import importlib.util
import io
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).parents[1] / "scripts" / "bulk_fulfill.py"
spec = importlib.util.spec_from_file_location("bulk_fulfill", MODULE)
bulk = importlib.util.module_from_spec(spec); sys.modules[spec.name] = bulk
spec.loader.exec_module(bulk)


class FakeClient:
    def __init__(self, failures=()): self.calls, self.failures = [], set(failures)
    def list_fulfillment_orders(self, order): return [{"id": f"fo-{order}", "status": "processing"}]
    def fulfill(self, fid, tracking, carrier, notify=True):
        self.calls.append((fid, tracking, carrier, notify))
        if fid in self.failures: raise urllib.error.HTTPError("url", 500, "bad", {}, None)
        return {"shipping_address": {"email": "private@example.com"}}


class BulkFulfillTests(unittest.TestCase):
    def run_bulk(self, client, rows, execute=True, carrier_map=None, resume=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        output = Path(td.name) / "results.csv"
        worker = bulk.BulkFulfiller(client, execute=execute, carrier_map=carrier_map or {},
                                    row_delay=0, sleep=lambda _: None)
        results = worker.run(rows, output, bulk.resume_completed(resume))
        return results, output

    def test_missing_env_refuses_before_reading_input(self):
        argv = ["--store", "shop", "--input", "missing.csv", "--results", "out.csv"]
        with mock.patch.dict(bulk.os.environ, {}, clear=True), mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(2, bulk.main(argv))

    def test_dry_run_issues_zero_mutating_calls(self):
        client = FakeClient(); rows, _ = self.run_bulk(client, [{"order_number": "1", "tracking_code": "1Z123", "carrier": "ups"}], execute=False)
        self.assertEqual([], client.calls); self.assertEqual("WOULD_FULFILL", rows[0].action)

    def test_unconfirmed_inferred_carrier_is_not_sent(self):
        client = FakeClient(); rows, _ = self.run_bulk(client, [{"order_number": "1", "tracking_code": "1Z123", "carrier": ""}])
        self.assertEqual([], client.calls); self.assertEqual("UNCONFIRMED_CARRIER:prefix:1Z", rows[0].action)

    def test_resume_skips_completed_rows(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup); prior = Path(td.name) / "prior.csv"
        with prior.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk.FIELDS); w.writeheader()
            w.writerow({"order_number": "1", "tracking_code": "T1", "status": "success"})
        client = FakeClient(); self.run_bulk(client, [{"order_number": "1", "tracking_code": "T1", "carrier": "ups"}, {"order_number": "2", "tracking_code": "T2", "carrier": "ups"}], resume=prior)
        self.assertEqual(["fo-2"], [x[0] for x in client.calls])

    def test_partial_failure_continues(self):
        client = FakeClient({"fo-2"}); rows, _ = self.run_bulk(client, [{"order_number": str(n), "tracking_code": f"T{n}", "carrier": "ups"} for n in (1, 2, 3)])
        self.assertEqual(3, len(client.calls)); self.assertEqual(["success", "error", "success"], [r.status for r in rows])

    def test_results_exclude_pii_even_when_response_contains_it(self):
        _, output = self.run_bulk(FakeClient(), [{"order_number": "1", "tracking_code": "T1", "carrier": "ups"}])
        text = output.read_text(); self.assertNotIn("address", text.lower()); self.assertNotIn("email", text.lower()); self.assertNotIn("private@example.com", text)


if __name__ == "__main__": unittest.main()
