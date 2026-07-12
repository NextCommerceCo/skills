import csv
import email.message
import io
import importlib.util
import tempfile
import unittest
import urllib.error
import urllib.response
from pathlib import Path
from unittest import mock

MODULE = Path(__file__).parents[1] / "scripts" / "bulk_move.py"
spec = importlib.util.spec_from_file_location("bulk_move", MODULE)
bulk_move = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = bulk_move
spec.loader.exec_module(bulk_move)


def fo(fid, order, status="open", location=10, actions=None, request_status=None, available=(20,)):
    return {"id": fid, "order_number": order, "status": status,
            "assigned_location": {"id": location}, "supported_actions": actions or [],
            "request_status": request_status, "available_locations": [{"id": x} for x in available]}


class FakeClient:
    def __init__(self, orders, polls=None, fail_moves=()):
        self.orders, self.polls = orders, {str(k): list(v) for k, v in (polls or {}).items()}
        self.fail_moves, self.moves, self.cancels, self.lists = set(fail_moves), [], [], []
    def list_fos(self, order): self.lists.append(str(order)); return self.orders.get(str(order), [])
    def available_locations(self, fid): return []
    def request_cancellation(self, fid): self.cancels.append(str(fid)); return {}
    def get_fo(self, fid): return self.polls[str(fid)].pop(0)
    def move(self, fid, destination):
        self.moves.append(str(fid))
        if str(fid) in self.fail_moves:
            raise urllib.error.HTTPError("url", 500, "failure", {}, None)
        return {"moved_fulfillment_order": {"id": f"new-{fid}"}}


class BulkMoveTests(unittest.TestCase):
    def run_mover(self, client, orders, execute=True, resume=None):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        output = Path(td.name) / "results.csv"
        mover = bulk_move.BulkMover(client, 10, 20, execute=execute, poll_attempts=3, poll_delay=0, sleep=lambda _: None)
        rows = mover.run(orders, output, bulk_move.resume_completed(resume))
        return rows, output

    def test_processing_never_accepted_is_not_moved(self):
        pending = fo(1, 1001, "processing", actions=[], request_status="cancel_requested")
        client = FakeClient({"1001": [pending]}, {1: [pending, pending, pending]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.moves)
        self.assertEqual("CANCEL_PENDING", rows[0].action)

    def test_cancellation_accepted_after_two_polls_moves_once(self):
        pending = fo(1, 1001, "processing", actions=[])
        accepted = fo(1, 1001, "canceled", actions=["move"], request_status="cancel_accepted")
        client = FakeClient({"1001": [pending]}, {1: [pending, accepted]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual(["1"], client.moves)
        self.assertEqual("CANCEL+MOVED", rows[0].action)

    def test_pending_cancellation_skips_duplicate_post_then_polls_and_moves(self):
        pending = fo(1, 1001, "processing", actions=[], request_status="cancel_requested")
        accepted = fo(1, 1001, "canceled", actions=["move"], request_status="cancel_accepted")
        client = FakeClient({"1001": [pending]}, {1: [accepted]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.cancels)
        self.assertEqual(["1"], client.moves)
        self.assertEqual("CANCEL+MOVED", rows[0].action)

    def test_rejected_cancellation_is_terminal_without_post_or_poll(self):
        rejected = fo(1, 1001, "processing", actions=[], request_status="cancel_rejected")
        client = FakeClient({"1001": [rejected]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.cancels)
        self.assertEqual([], client.moves)
        self.assertEqual("CANCEL_REJECTED", rows[0].action)

    def test_accepted_canceled_movable_fo_is_resumed(self):
        accepted = fo(1, 1001, "canceled", actions=["move"], request_status="cancel_accepted")
        client = FakeClient({"1001": [accepted]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual(["1"], client.moves)
        self.assertEqual("CANCEL+MOVED", rows[0].action)

    def test_unavailable_destination_is_not_moved(self):
        client = FakeClient({"1001": [fo(1, 1001, actions=["move"], available=(30,))]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.moves)
        self.assertEqual("LOCATION_UNAVAILABLE", rows[0].action)

    def test_each_order_is_listed_only_once_without_location_preflight(self):
        client = FakeClient({"1001": [fo(1, 1001, actions=["move"])]})
        self.run_mover(client, ["1001"])
        self.assertEqual(["1001"], client.lists)

    def test_empty_per_fo_availability_does_not_cancel_or_move(self):
        processing = fo(1, 1001, "processing", actions=[], available=())
        client = FakeClient({"1001": [processing]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.cancels)
        self.assertEqual([], client.moves)
        self.assertEqual("LOCATION_UNVERIFIED", rows[0].action)

    def test_partial_failure_continues(self):
        client = FakeClient({str(n): [fo(n, n, actions=["move"])] for n in (1, 2, 3)}, fail_moves={"2"})
        rows, _ = self.run_mover(client, ["1", "2", "3"])
        self.assertEqual(["1", "2", "3"], client.moves)
        self.assertEqual(["success", "error", "success"], [r.status for r in rows])
        self.assertEqual("HTTP_ERROR_500", rows[1].action)

    def test_resume_skips_completed_order(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        resume = Path(td.name) / "prior.csv"
        with resume.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk_move.FIELDS); w.writeheader()
            w.writerow({"order_number": "1001", "action": "MOVED", "status": "success"})
        client = FakeClient({"1001": [fo(1, 1001, actions=["move"])], "1002": [fo(2, 1002, actions=["move"])]})
        self.run_mover(client, ["1001", "1002"], resume=resume)
        self.assertEqual(["2"], client.moves)

    def test_resume_does_not_treat_dry_run_as_completed(self):
        td = tempfile.TemporaryDirectory(); self.addCleanup(td.cleanup)
        resume = Path(td.name) / "prior.csv"
        with resume.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=bulk_move.FIELDS); w.writeheader()
            w.writerow({"order_number": "1001", "action": "WOULD_MOVED", "status": "skipped"})
        self.assertEqual(set(), bulk_move.resume_completed(resume))

    def test_dry_run_makes_no_mutating_requests(self):
        client = FakeClient({"1001": [fo(1, 1001, "processing", actions=[])]})
        rows, _ = self.run_mover(client, ["1001"], execute=False)
        self.assertEqual([], client.cancels)
        self.assertEqual([], client.moves)
        self.assertEqual("WOULD_CANCEL+MOVE", rows[0].action)

    def test_same_source_and_destination_exits_before_api_calls(self):
        calls = []
        argv = ["--store", "example", "--input", "missing.csv", "--source", "10",
                "--destination", "10", "--results", "results.csv"]
        with mock.patch.object(bulk_move, "AdminClient", side_effect=lambda *args: calls.append(args)), \
                mock.patch.dict(bulk_move.os.environ, {"NEXT_ADMIN_API_TOKEN": "test"}):
            with self.assertRaises(SystemExit) as raised:
                bulk_move.main(argv)
        self.assertEqual(2, raised.exception.code)
        self.assertEqual([], calls)

    def test_store_host_allowlist_and_explicit_override(self):
        self.assertEqual("mystore.29next.store", bulk_move.normalize_domain("mystore"))
        self.assertEqual("mystore.29next.store", bulk_move.normalize_domain("mystore.29next.store"))
        with self.assertRaisesRegex(ValueError, "refusing non-29next"):
            bulk_move.normalize_domain("evil.example")
        self.assertEqual("evil.example", bulk_move.normalize_domain("evil.example", allow_host=True))

    def test_evil_store_exits_before_issuing_any_request(self):
        argv = ["--store", "evil.example", "--input", "missing.csv", "--source", "10",
                "--destination", "20", "--results", "results.csv"]
        with mock.patch.object(bulk_move.urllib.request, "urlopen") as request, \
                mock.patch.dict(bulk_move.os.environ, {"NEXT_ADMIN_API_TOKEN": "test"}):
            with self.assertRaises(SystemExit) as raised:
                bulk_move.main(argv)
        self.assertEqual(2, raised.exception.code)
        request.assert_not_called()

    def test_request_level_rate_limit_sleeps_between_requests(self):
        now, delays = [10.0], []
        def sleep(delay):
            delays.append(delay)
            now[0] += delay
        client = bulk_move.AdminClient("mystore", "test", min_interval=0.25,
                                      clock=lambda: now[0], sleep=sleep)
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = b"{}"
        with mock.patch.object(client.opener, "open", return_value=response):
            client._request("GET", "one/")
            client._request("GET", "two/")
        self.assertEqual([0.25], delays)

    def test_authenticated_request_refuses_cross_host_redirect(self):
        client = bulk_move.AdminClient("mystore", "secret", min_interval=0)
        target = "https://evil.example/collect"
        seen = []

        class RedirectTransport(urllib.request.BaseHandler):
            handler_order = 100

            def https_open(self, request):
                seen.append(request)
                headers = email.message.Message()
                headers["Location"] = target
                response = urllib.response.addinfourl(
                    io.BytesIO(b""), headers, request.full_url, code=302
                )
                response.msg = "Found"
                return response

        client.opener = urllib.request.build_opener(
            bulk_move.AuthenticatedRedirectHandler(), RedirectTransport()
        )
        with self.assertRaisesRegex(urllib.error.HTTPError,
                                    "refusing authenticated redirect to " + target):
            client._request("GET", "fulfillment-orders/")
        self.assertEqual(1, len(seen))
        self.assertEqual("Bearer secret", seen[0].get_header("Authorization"))

    def test_list_fos_paginates_and_multiple_source_fos_require_review(self):
        client = bulk_move.AdminClient("example", "test")
        page_two = "https://example.29next.store/api/admin/fulfillment-orders/?page=2"
        pages = [
            {"results": [fo(1, 1001, actions=["move"])], "next": page_two},
            {"results": [fo(2, 1001, actions=["move"])], "next": None},
        ]
        with mock.patch.object(client, "_request", side_effect=pages) as request:
            fos = client.list_fos("1001")
        self.assertEqual(["1", "2"], [str(item["id"]) for item in fos])
        self.assertEqual(page_two, request.call_args_list[1].args[1])

        fake = FakeClient({"1001": fos})
        rows, _ = self.run_mover(fake, ["1001"])
        self.assertEqual("MANUAL_REVIEW", rows[0].action)
        self.assertEqual([], fake.moves)


if __name__ == "__main__":
    unittest.main()
