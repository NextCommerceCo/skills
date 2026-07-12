import importlib.util
from datetime import datetime, timezone
from pathlib import Path
import sys
import unittest


SCRIPT_PATH = Path(__file__).parents[1] / "scripts" / "next_ops_scan.py"
SPEC = importlib.util.spec_from_file_location("next_ops_scan", SCRIPT_PATH)
scan = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = scan
SPEC.loader.exec_module(scan)


class RecordingClient(scan.AdminClient):
    def __init__(self, pages):
        super().__init__("store.29next.store", "secret")
        self.pages = iter(pages)
        self.requests = []

    def _send(self, req, *, retries=2):
        self.requests.append(req)
        return next(self.pages)


class FakeScanClient:
    def __init__(self, rows_by_status):
        self.rows_by_status = rows_by_status

    def paginate(self, path, params=None, *, max_pages=50):
        return iter(self.rows_by_status.get(params.get("delivery_status"), []))


class NextOpsScanTests(unittest.TestCase):
    def test_pagination_rejects_foreign_or_insecure_next_without_request(self):
        for next_url in (
            "https://attacker.example/api/admin/orders/?page=2",
            "http://store.29next.store/api/admin/orders/?page=2",
        ):
            with self.subTest(next_url=next_url):
                client = RecordingClient([{"results": [], "next": next_url}])
                with self.assertRaises(ValueError):
                    list(client.paginate("orders/"))
                self.assertEqual(len(client.requests), 1)
                self.assertNotEqual(client.requests[0].full_url, next_url)

    def test_pagination_follows_same_host_https_next(self):
        next_url = "https://store.29next.store/api/admin/orders/?page=2"
        client = RecordingClient(
            [
                {"results": [{"number": "1"}], "next": next_url},
                {"results": [{"number": "2"}], "next": None},
            ]
        )
        rows = list(client.paginate("orders/"))
        self.assertEqual([row["number"] for row in rows], ["1", "2"])
        self.assertEqual(client.requests[1].full_url, next_url)
        self.assertEqual(client.requests[1].get_header("Authorization"), "Bearer secret")

    def test_delivery_reason_labels_order_timestamp_honestly(self):
        client = FakeScanClient(
            {"in_transit": [{"number": "10", "delivery_status": "in_transit", "created_at": "2026-07-01T00:00:00Z"}]}
        )
        findings, notes = scan.scan_delivery_tracking(
            client,
            now=datetime(2026, 7, 13, tzinfo=timezone.utc),
            tracking_added_days=5,
            in_transit_days=7,
            delayed_days=3,
            admin_base_url="https://store.29next.store/dashboard",
            max_pages=5,
        )
        self.assertEqual(notes, [])
        self.assertEqual(len(findings), 1)
        self.assertIn("order record timestamp 12 days ago", findings[0].reason)
        self.assertNotIn("delivery status is `in_transit` for at least", findings[0].reason)

    def test_incomplete_and_rejected_reasons_are_observational(self):
        now = datetime(2026, 7, 13, tzinfo=timezone.utc)

        class OrdersClient:
            def __init__(self, row):
                self.row = row

            def paginate(self, path, params=None, *, max_pages=50):
                return iter([self.row])

        incomplete = scan.scan_incomplete_orders(
            OrdersClient({"number": "20", "fulfillment_status": "incomplete", "payment_status": "paid", "created_at": "2026-07-12T00:00:00Z"}),
            now=now, lookback_days=30, idle_days=0,
            admin_base_url="https://store.29next.store/dashboard", max_pages=5,
        )[0]
        rejected = scan.scan_rejected_orders(
            OrdersClient({"number": "21", "fulfillment_status": "rejected", "created_at": "2026-07-12T00:00:00Z"}),
            now=now, lookback_days=30, idle_days=0,
            admin_base_url="https://store.29next.store/dashboard", max_pages=5,
        )[0]
        self.assertIn("Order is incomplete with payment_status `paid`", incomplete.reason)
        self.assertNotIn("usually means", incomplete.reason)
        self.assertIn("Fulfillment was rejected", rejected.reason)
        self.assertNotEqual(rejected.reason, "Shopify or Shop Sync refused the order for fulfillment.")
        self.assertIn("If this store syncs orders to Shopify", incomplete.reason)
        self.assertIn("If this store syncs orders to Shopify", rejected.reason)

    def test_guidance_urls_are_pinned(self):
        self.assertEqual(
            scan.CS_GUIDE_URL,
            "https://docs.nextcommerce.com/docs/manage/orders/order-management",
        )
        self.assertEqual(
            scan.SHOP_SYNC_URL,
            "https://docs.nextcommerce.com/docs/apps/shop-sync",
        )


if __name__ == "__main__":
    unittest.main()
