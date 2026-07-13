#!/usr/bin/env python3
"""Read-only daily operations risk scan for one Next Commerce store."""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import sys
import time
from email.utils import parsedate_to_datetime
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable


API_VERSION = "2024-04-01"
ADMIN_API_DOCS_URL = "https://developers.nextcommerce.com/docs/admin-api"
CS_GUIDE_URL = "https://docs.nextcommerce.com/docs/manage/orders/order-management"
SHOP_SYNC_URL = "https://docs.nextcommerce.com/docs/apps/shop-sync"
DELIVERY_TRACKING_URL = "https://docs.nextcommerce.com/docs/apps/delivery-tracking"


@dataclass
class Finding:
    queue: str
    severity: str
    order_number: str
    age_days: str
    status: str
    reason: str
    recommended_action: str
    admin_url: str
    docs_url: str


class AuthenticatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Refuse redirects on authenticated requests so the Bearer token can
    never be forwarded to a host the pagination guard did not approve."""

    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing authenticated redirect to {newurl}", headers, fp,
        )


class AdminClient:
    def __init__(self, domain: str, token: str, *, timeout: float = 30.0):
        self.domain = normalize_domain(domain)
        self.token = token
        self.timeout = timeout
        self.notes: list[str] = []
        self.last_url = ""
        self.opener = urllib.request.build_opener(AuthenticatedRedirectHandler())

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "X-29next-API-Version": API_VERSION,
            "Accept": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        qs = ""
        if params:
            qs = "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v is not None}
            )
        url = f"https://{self.domain}/api/admin/{path.lstrip('/')}{qs}"
        self.last_url = url
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._send(req)

    def get_url(self, url: str) -> Any:
        # Resolve a relative `next` link against the last request; pin scheme,
        # host (ignoring the default port), and the /api/admin/ base path before
        # forwarding the bearer token.
        base = self.last_url or f"https://{self.domain}/api/admin/"
        resolved = urllib.parse.urljoin(base, url)
        parsed = urllib.parse.urlparse(resolved)
        if (parsed.scheme != "https" or parsed.hostname != self.domain
                or parsed.port not in (None, 443)
                or not parsed.path.startswith("/api/admin/")):
            raise ValueError(
                "Refusing pagination URL outside the configured store API base: "
                f"expected https://{self.domain}/api/admin/, got {url}"
            )
        self.last_url = resolved
        req = urllib.request.Request(resolved, headers=self._headers(), method="GET")
        return self._send(req)

    def _send(self, req: urllib.request.Request, *, retries: int = 2) -> Any:
        last_err: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with self.opener.open(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    if not body:
                        return None
                    return json.loads(body)
            except urllib.error.HTTPError as e:
                if e.code == 429 or 500 <= e.code < 600:
                    last_err = e
                    time.sleep(retry_delay(e, attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError) as e:
                last_err = e
                time.sleep(1.0 + attempt)
                continue
        if last_err is None:
            raise RuntimeError("request failed without an exception")
        raise last_err

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        max_pages: int = 50,
    ) -> Iterable[dict[str, Any]]:
        params = dict(params or {})
        params.setdefault("limit", 100)
        next_url = None
        page_count = 0
        for _ in range(max_pages):
            page_count += 1
            data = self.get(path, params) if next_url is None else self.get_url(next_url)
            if not isinstance(data, dict):
                return
            for row in data.get("results") or []:
                if isinstance(row, dict):
                    yield row
            next_url = data.get("next")
            if not next_url:
                return
        if next_url:
            self.notes.append(
                f"Scan truncated `{path}` after {page_count} pages. "
                "Re-run with narrower thresholds or a higher page-limit flag "
                "if this store has a very large queue."
            )


def retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    fallback_delay = jittered_backoff(attempt)
    retry_after = error.headers.get("Retry-After")
    if retry_after:
        try:
            delay = float(retry_after)
            return delay if delay > 0 else fallback_delay
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(retry_after)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
                return delay if delay > 0 else fallback_delay
            except (TypeError, ValueError):
                pass
    return fallback_delay


def jittered_backoff(attempt: int) -> float:
    base_delay = min(30.0, 1.0 + (2.0 ** attempt))
    return base_delay + random.uniform(0.0, min(1.0, base_delay * 0.25))


def normalize_domain(raw: str) -> str:
    raw = raw.strip().removeprefix("https://").removeprefix("http://").strip("/").lower()
    if "." not in raw:
        return f"{raw}.29next.store"
    return raw


def parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def age_days(now: datetime, value: Any) -> int | None:
    dt = parse_dt(value)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0, (now - dt).days)


def first_present(row: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def order_number(row: dict[str, Any]) -> str:
    value = first_present(row, ["number", "order_number", "order"])
    return str(value or "")


def order_date(row: dict[str, Any]) -> Any:
    return first_present(row, ["date_placed", "created_at", "date_created"])


def fulfillment_status_matches(row: dict[str, Any], expected: str) -> bool:
    value = row.get("fulfillment_status")
    return isinstance(value, str) and value.lower() == expected


def admin_order_url(admin_base_url: str, number: str) -> str:
    base_url = admin_base_url.rstrip("/")
    return f"{base_url}/orders/{number}/" if number else f"{base_url}/orders/"


def severity_for(age: int | None, warning_days: int, critical_days: int) -> str:
    if age is not None and age >= critical_days:
        return "critical"
    if age is not None and age >= warning_days:
        return "warning"
    return "info"


def scan_incomplete_orders(
    client: AdminClient,
    *,
    now: datetime,
    lookback_days: int,
    idle_days: int,
    admin_base_url: str,
    max_pages: int,
) -> list[Finding]:
    findings: list[Finding] = []
    horizon = now - timedelta(days=lookback_days)
    try:
        orders = list(client.paginate(
            "orders/", {"fulfillment_status": "incomplete"}, max_pages=max_pages))
    except Exception as e:
        client.notes.append(
            f"Incomplete-order queue not scanned: {e.__class__.__name__}: {e}")
        return findings
    for order in orders:
        if not fulfillment_status_matches(order, "incomplete"):
            continue
        placed = parse_dt(order_date(order))
        if placed and placed < horizon:
            continue
        age = age_days(now, order_date(order))
        if age is not None and age < idle_days:
            continue
        number = order_number(order)
        payment_status = str(order.get("payment_status") or "unknown")
        total = first_present(order, ["total_incl_tax", "total", "total_excl_tax"])
        currency = str(order.get("currency") or "")
        amount_hint = f" Total {total} {currency}." if total not in (None, "") else ""
        findings.append(
            Finding(
                queue="refund_review",
                severity=severity_for(age, warning_days=1, critical_days=5),
                order_number=number,
                age_days="" if age is None else str(age),
                status=f"incomplete/payment:{payment_status}",
                reason=(
                    f"Order is incomplete with payment_status `{payment_status}`."
                    " If this store syncs orders to Shopify via Shop Sync, a common cause is a canceled Shopify order."
                    + amount_hint
                ),
                recommended_action="Open Order Details, review Payment Summary, and use the Refund button if money is owed back.",
                admin_url=admin_order_url(admin_base_url, number),
                docs_url=CS_GUIDE_URL,
            )
        )
    return findings


def scan_rejected_orders(
    client: AdminClient,
    *,
    now: datetime,
    lookback_days: int,
    idle_days: int,
    admin_base_url: str,
    max_pages: int,
) -> list[Finding]:
    findings: list[Finding] = []
    horizon = now - timedelta(days=lookback_days)
    try:
        orders = list(client.paginate(
            "orders/", {"fulfillment_status": "rejected"}, max_pages=max_pages))
    except Exception as e:
        client.notes.append(
            f"Rejected-order queue not scanned: {e.__class__.__name__}: {e}")
        return findings
    for order in orders:
        if not fulfillment_status_matches(order, "rejected"):
            continue
        placed = parse_dt(order_date(order))
        if placed and placed < horizon:
            continue
        age = age_days(now, order_date(order))
        if age is not None and age < idle_days:
            continue
        number = order_number(order)
        findings.append(
            Finding(
                queue="rejected_order_review",
                severity=severity_for(age, warning_days=1, critical_days=5),
                order_number=number,
                age_days="" if age is None else str(age),
                status="rejected",
                reason=(
                    "Fulfillment was rejected. If this store syncs orders to Shopify via Shop Sync, "
                    "a common cause is Shopify or Shop Sync refusing the order."
                ),
                recommended_action="Review the rejection cause, correct customer data, stock, or fulfillment settings, then request fulfillment again if appropriate.",
                admin_url=admin_order_url(admin_base_url, number),
                docs_url=SHOP_SYNC_URL,
            )
        )
    return findings


def delivery_timestamp(row: dict[str, Any], status: str) -> tuple[Any, str]:
    delivery_event_timestamp = first_present(
        row,
        [
            "delivery_status_updated_at",
            "delivery_updated_at",
            "delivery_event_at",
            "delivery_event_timestamp",
        ],
    )
    if delivery_event_timestamp is not None:
        return delivery_event_timestamp, "delivery status last updated"
    if status == "delayed":
        return (
            first_present(row, ["updated_at", "date_updated", "created_at", "date_created"]),
            "order record last updated",
        )
    return (
        first_present(row, ["created_at", "date_created", "updated_at", "date_updated"]),
        "order record timestamp",
    )


def delivery_status_matches(row: dict[str, Any], expected: str) -> bool:
    value = row.get("delivery_status")
    return not isinstance(value, str) or value.lower() == expected


def scan_delivery_tracking(
    client: AdminClient,
    *,
    now: datetime,
    tracking_added_days: int,
    in_transit_days: int,
    delayed_days: int,
    admin_base_url: str,
    max_pages: int,
) -> tuple[list[Finding], list[str]]:
    status_thresholds: dict[str, int | None] = {
        "tracking_added": tracking_added_days,
        "in_transit": in_transit_days,
        "failed_delivery": None,
        "delayed": delayed_days,
    }
    findings: list[Finding] = []
    notes: list[str] = []
    for status, threshold_days in status_thresholds.items():
        try:
            rows = list(
                client.paginate("orders/", {"delivery_status": status}, max_pages=max_pages)
            )
        except urllib.error.HTTPError as e:
            notes.append(
                f"Delivery Tracking status `{status}` not scanned: API returned HTTP {e.code}. "
                "Confirm Delivery Tracking is installed and the token has orders read access."
            )
            continue
        except Exception as e:
            notes.append(f"Delivery Tracking status `{status}` not scanned: {e.__class__.__name__}: {e}")
            continue

        for row in rows:
            if not delivery_status_matches(row, status):
                continue
            timestamp, timestamp_label = delivery_timestamp(row, status)
            age = age_days(now, timestamp)
            if threshold_days is not None and age is not None and age < threshold_days:
                continue
            number = order_number(row)
            tracking_code = first_present(row, ["tracking_code", "tracking_number"])
            reason = f"Order delivery status is `{status}`"
            if age is not None:
                reason += f"; {timestamp_label} {age} days ago"
            if tracking_code:
                reason += f" (tracking {tracking_code})"
            reason += "."
            if status == "failed_delivery":
                severity = "critical"
            else:
                severity = severity_for(age, threshold_days, threshold_days * 2)
            findings.append(
                Finding(
                    queue="delivery_risk_review",
                    severity=severity,
                    order_number=number,
                    age_days="" if age is None else str(age),
                    status=status,
                    reason=reason,
                    recommended_action="Contact the customer, carrier, or 3PL; reship, refund, or document the next step before the customer disputes.",
                    admin_url=admin_order_url(admin_base_url, number),
                    docs_url=DELIVERY_TRACKING_URL,
                )
            )
    return findings, notes


def write_csv(path: Path, findings: list[Finding]) -> None:
    fieldnames = [
        "queue",
        "severity",
        "order_number",
        "age_days",
        "status",
        "reason",
        "recommended_action",
        "admin_url",
        "docs_url",
    ]
    with path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for finding in findings:
            writer.writerow(finding.__dict__)


def write_summary(path: Path, findings: list[Finding], notes: list[str], domain: str) -> None:
    severity_rank = {"critical": 0, "warning": 1, "info": 2}
    findings = sorted(
        findings,
        key=lambda f: (severity_rank.get(f.severity, 9), f.queue, -(int(f.age_days or 0))),
    )
    counts: dict[str, int] = {}
    for finding in findings:
        counts[finding.queue] = counts.get(finding.queue, 0) + 1

    lines = [
        "# NEXT Ops Scan Summary",
        "",
        f"- Store: `{domain}`",
        f"- Generated: `{datetime.now(timezone.utc).isoformat()}`",
        f"- Findings: {len(findings)}",
        "",
        "## Queue Counts",
        "",
    ]
    if counts:
        for queue, count in sorted(counts.items()):
            lines.append(f"- `{queue}`: {count}")
    else:
        lines.append("- No matching risk queues found.")

    if notes:
        lines.extend(["", "## Scan Notes", ""])
        for note in notes:
            lines.append(f"- {note}")

    lines.extend(["", "## Findings", ""])
    if not findings:
        lines.append("No action rows found for the current thresholds.")
    else:
        for idx, finding in enumerate(findings, start=1):
            age = f", age {finding.age_days}d" if finding.age_days else ""
            lines.extend(
                [
                    f"### {idx}. {finding.severity.upper()} - {finding.queue} - Order {finding.order_number or 'unknown'}",
                    "",
                    f"- Status: `{finding.status}`{age}",
                    f"- Reason: {finding.reason}",
                    f"- Next step: {finding.recommended_action}",
                    f"- Admin: {finding.admin_url}",
                    f"- Docs: {finding.docs_url}",
                    "",
                ]
            )

    path.write_text("\n".join(lines) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Read-only Next Commerce ops scan")
    parser.add_argument("--domain", default=os.getenv("NEXT_STORE_DOMAIN"), help="Store domain or subdomain")
    parser.add_argument("--token", default=os.getenv("NEXT_ADMIN_API_TOKEN"), help="Admin API token")
    parser.add_argument(
        "--admin-base-url",
        default=os.getenv("NEXT_ADMIN_BASE_URL"),
        help="Dashboard base URL used for order links; defaults to https://<domain>/dashboard",
    )
    parser.add_argument("--out-dir", default="next-ops-scan-output")
    parser.add_argument("--lookback-days", type=int, default=30)
    parser.add_argument("--incomplete-idle-days", type=int, default=0)
    parser.add_argument("--rejected-idle-days", type=int, default=1)
    parser.add_argument("--tracking-added-days", type=int, default=5)
    parser.add_argument("--in-transit-days", type=int, default=7)
    parser.add_argument("--delayed-days", type=int, default=3)
    parser.add_argument("--orders-max-pages", type=int, default=50)
    parser.add_argument(
        "--delivery-max-pages",
        type=int,
        default=None,
        help="Maximum Orders List pages to scan for each delivery_status queue",
    )
    parser.add_argument(
        "--fulfillments-max-pages",
        type=int,
        default=25,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.domain:
        print("Missing store domain. Set NEXT_STORE_DOMAIN or pass --domain.", file=sys.stderr)
        return 2
    if not args.token:
        print("Missing token. Set NEXT_ADMIN_API_TOKEN or pass --token.", file=sys.stderr)
        return 2

    domain = normalize_domain(args.domain)
    admin_base_url = args.admin_base_url or f"https://{domain}/dashboard"
    client = AdminClient(domain, args.token)
    delivery_max_pages = (
        args.delivery_max_pages
        if args.delivery_max_pages is not None
        else args.fulfillments_max_pages
    )
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        client.get("orders/", {"limit": 1})
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            print(
                f"Token validation failed: HTTP {e.code}. Confirm the token is current "
                "and includes orders read access.",
                file=sys.stderr,
            )
        elif e.code == 404:
            print(
                f"Admin API validation failed: HTTP 404 for {client.last_url}. "
                "The NEXT Admin API uses /api/admin/ plus "
                "Authorization: Bearer <token>; /api/v1/ paths or "
                "Authorization: Token commonly return storefront HTML 404 pages. "
                f"Confirm the store domain and see {ADMIN_API_DOCS_URL}.",
                file=sys.stderr,
            )
        else:
            print(f"Token/domain validation failed: HTTP {e.code}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Token/domain validation failed: {e.__class__.__name__}: {e}", file=sys.stderr)
        return 1

    now = datetime.now(timezone.utc)
    notes: list[str] = []
    findings: list[Finding] = []
    findings.extend(
        scan_incomplete_orders(
            client,
            now=now,
            lookback_days=args.lookback_days,
            idle_days=args.incomplete_idle_days,
            admin_base_url=admin_base_url,
            max_pages=args.orders_max_pages,
        )
    )
    findings.extend(
        scan_rejected_orders(
            client,
            now=now,
            lookback_days=args.lookback_days,
            idle_days=args.rejected_idle_days,
            admin_base_url=admin_base_url,
            max_pages=args.orders_max_pages,
        )
    )
    delivery_findings, delivery_notes = scan_delivery_tracking(
        client,
        now=now,
        tracking_added_days=args.tracking_added_days,
        in_transit_days=args.in_transit_days,
        delayed_days=args.delayed_days,
        admin_base_url=admin_base_url,
        max_pages=delivery_max_pages,
    )
    findings.extend(delivery_findings)
    notes.extend(delivery_notes)
    notes.extend(client.notes)

    csv_path = out_dir / "next_ops_scan_results.csv"
    summary_path = out_dir / "next_ops_scan_summary.md"
    write_csv(csv_path, findings)
    write_summary(summary_path, findings, notes, domain)

    print(f"Wrote {summary_path}")
    print(f"Wrote {csv_path}")
    print(f"Findings: {len(findings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
