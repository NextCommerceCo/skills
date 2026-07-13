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

MODULE = Path(__file__).parents[1] / "scripts" / "bulk_fulfill.py"
spec = importlib.util.spec_from_file_location("bulk_fulfill", MODULE)
bulk = importlib.util.module_from_spec(spec); sys.modules[spec.name] = bulk
spec.loader.exec_module(bulk)


class FakeClient:
    def __init__(self, failures=()): self.calls, self.failures = [], set(failures)
    def list_fulfillment_orders(self, order):
        return [{"id": f"fo-{order}", "order_number": order, "status": "processing"}]
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

    def test_duplicate_order_and_tracking_is_fulfilled_once(self):
        client = FakeClient()
        input_rows = [
            {"order_number": "1", "tracking_code": "T1", "carrier": "ups"},
            {"order_number": "1", "tracking_code": "T1", "carrier": "ups"},
        ]
        rows, output = self.run_bulk(client, input_rows)
        self.assertEqual(1, len(client.calls))
        self.assertEqual(["FULFILLED", "DUPLICATE"], [row.action for row in rows])
        self.assertEqual("skipped", rows[1].status)
        self.assertIn(("1", "T1"), bulk.resume_completed(output))

    def test_duplicate_after_fulfill_error_is_not_mutated_twice(self):
        client = FakeClient({"fo-1"})
        input_rows = [
            {"order_number": "1", "tracking_code": "T1", "carrier": "ups"},
            {"order_number": "1", "tracking_code": "T1", "carrier": "ups"},
        ]
        rows, _ = self.run_bulk(client, input_rows)
        self.assertEqual(1, len(client.calls))
        self.assertEqual(["HTTP_ERROR_500", "DUPLICATE"],
                         [row.action for row in rows])
        self.assertEqual("skipped", rows[1].status)

    def test_results_exclude_pii_even_when_response_contains_it(self):
        _, output = self.run_bulk(FakeClient(), [{"order_number": "1", "tracking_code": "T1", "carrier": "ups"}])
        text = output.read_text(); self.assertNotIn("address", text.lower()); self.assertNotIn("email", text.lower()); self.assertNotIn("private@example.com", text)

    def test_authenticated_request_refuses_cross_host_redirect(self):
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
            client._request("GET", "fulfillment-orders/")
        self.assertEqual(1, len(seen))
        self.assertEqual("Bearer secret", seen[0].get_header("Authorization"))

    def test_missing_or_mismatched_order_identity_never_fulfills(self):
        for fo in ({"id": "fo-1", "status": "processing"},
                   {"id": "fo-1", "order_number": "other", "status": "processing"}):
            with self.subTest(fo=fo):
                client = FakeClient(); client.list_fulfillment_orders = lambda _order: [fo]
                rows, _ = self.run_bulk(client, [{"order_number": "1",
                                                  "tracking_code": "T1", "carrier": "ups"}])
                self.assertEqual("MALFORMED_RESPONSE", rows[0].action)
                self.assertEqual([], client.calls)

    def test_empty_order_number_is_error_without_lookup(self):
        client = mock.MagicMock()
        rows, _ = self.run_bulk(client, [{"order_number": "   ",
                                          "tracking_code": "T1", "carrier": "ups"}])
        self.assertEqual("INVALID_ORDER_NUMBER", rows[0].action)
        client.list_fulfillment_orders.assert_not_called()

    def test_blank_tracking_is_rejected(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        source = Path(td.name) / "input.csv"
        with source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["order_number", "tracking_code",
                                                        "carrier"])
            writer.writeheader()
            writer.writerow({"order_number": "1", "tracking_code": "   ",
                             "carrier": "ups"})
        client = FakeClient()
        rows, _ = self.run_bulk(client, bulk.read_rows(source))
        self.assertEqual("MISSING_TRACKING", rows[0].action)
        self.assertEqual("error", rows[0].status)
        self.assertEqual([], client.calls)

    def test_empty_tracking_cell_is_retained_and_flagged(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        source = Path(td.name) / "input.csv"
        with source.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["order_number", "tracking_code",
                                                        "carrier"])
            writer.writeheader()
            writer.writerow({"order_number": "1", "tracking_code": "", "carrier": ""})
        parsed = bulk.read_rows(source)
        self.assertEqual(1, len(parsed))  # empty tracking must not drop the row
        client = FakeClient()
        rows, _ = self.run_bulk(client, parsed)
        self.assertEqual("MISSING_TRACKING", rows[0].action)
        self.assertEqual("error", rows[0].status)
        self.assertEqual([], client.calls)

    def test_paginates_before_selecting_eligible_fulfillment_order(self):
        client = bulk.AdminClient("example", "test")
        page_two = "https://example.29next.store/api/admin/fulfillment-orders/?page=2"
        pages = [
            {"results": [{"id": "closed", "order_number": "1", "status": "closed"}],
             "next": page_two},
            {"results": [{"id": "ready", "order_number": "1", "status": "processing"}],
             "next": None},
        ]
        with mock.patch.object(client, "_request", side_effect=pages) as request:
            fos = client.list_fulfillment_orders("1")
        self.assertEqual(["closed", "ready"], [fo["id"] for fo in fos])
        self.assertEqual(page_two, request.call_args_list[1].args[1])

        rows, _ = self.run_bulk(client=mock.Mock(
            list_fulfillment_orders=mock.Mock(return_value=fos),
            fulfill=mock.Mock()), rows=[{"order_number": "1", "tracking_code": "T1",
                                        "carrier": "ups"}], execute=False)
        self.assertEqual("ready", rows[0].fulfillment_id)

    def test_cross_host_pagination_is_error_not_partial_result(self):
        client = bulk.AdminClient("example", "test")
        page = {"results": [{"id": "ready", "order_number": "1", "status": "processing"}],
                "next": "https://evil.example/page=2"}
        with mock.patch.object(client, "_request", return_value=page):
            with self.assertRaises(bulk.MalformedResponse):
                client.list_fulfillment_orders("1")

    def test_repeated_pagination_url_is_error_not_partial_result(self):
        client = bulk.AdminClient("example", "test")
        page_two = "https://example.29next.store/api/admin/fulfillment-orders/?page=2"
        pages = [
            {"results": [{"id": "one"}], "next": page_two},
            {"results": [{"id": "two"}], "next": page_two},
        ]
        with mock.patch.object(client, "_request", side_effect=pages) as request:
            with self.assertRaisesRegex(bulk.MalformedResponse, "already-seen"):
                client.list_fulfillment_orders("1")
        self.assertEqual(2, request.call_count)

    def test_eligible_fulfillment_orders_across_pages_require_manual_review(self):
        client = bulk.AdminClient("example", "test")
        page_two = "https://example.29next.store/api/admin/fulfillment-orders/?page=2"
        pages = [
            {"results": [{"id": "one", "order_number": "1", "status": "open"}],
             "next": page_two},
            {"results": [{"id": "two", "order_number": "1", "status": "processing"}],
             "next": None},
        ]
        with mock.patch.object(client, "_request", side_effect=pages):
            fos = client.list_fulfillment_orders("1")
        fake = mock.Mock(list_fulfillment_orders=mock.Mock(return_value=fos),
                         fulfill=mock.Mock())
        rows, _ = self.run_bulk(fake, [{"order_number": "1", "tracking_code": "T1",
                                        "carrier": "ups"}])
        self.assertEqual("MANUAL_REVIEW", rows[0].action)
        fake.fulfill.assert_not_called()


if __name__ == "__main__": unittest.main()
