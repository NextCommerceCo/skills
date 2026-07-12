import csv
import importlib.util
import tempfile
import unittest
import urllib.error
from pathlib import Path

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
        self.fail_moves, self.moves, self.cancels = set(fail_moves), [], []
    def list_locations(self): return [{"id": 10}, {"id": 20}]
    def list_fos(self, order): return self.orders.get(str(order), [])
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

    def test_unavailable_destination_is_not_moved(self):
        client = FakeClient({"1001": [fo(1, 1001, actions=["move"], available=(30,))]})
        rows, _ = self.run_mover(client, ["1001"])
        self.assertEqual([], client.moves)
        self.assertEqual("LOCATION_UNAVAILABLE", rows[0].action)

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


if __name__ == "__main__":
    unittest.main()
