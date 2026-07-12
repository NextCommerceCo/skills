#!/usr/bin/env python3
"""Safely move Next Commerce fulfillment orders between locations in bulk."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Callable, Iterable


API_VERSION = "2024-04-01"
FIELDS = ["order_number", "original_fo_id", "new_fo_id", "action", "status", "destination"]
COMPLETED_STATUSES = {"success", "skipped"}


class AuthenticatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing authenticated redirect to {newurl}", headers, fp,
        )


class AdminClient:
    def __init__(self, domain: str, token: str, timeout: float = 30.0, *,
                 allow_host: bool = False, min_interval: float = 0.25,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.base = f"https://{normalize_domain(domain, allow_host=allow_host)}/api/admin/"
        self.token = token
        self.timeout = timeout
        self.min_interval, self.clock, self.sleep = min_interval, clock, sleep
        self._last_request_at: float | None = None
        self.opener = urllib.request.build_opener(AuthenticatedRedirectHandler())

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if self._last_request_at is not None:
            delay = self.min_interval - (self.clock() - self._last_request_at)
            if delay > 0:
                self.sleep(delay)
        self._last_request_at = self.clock()
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            urllib.parse.urljoin(self.base, path.lstrip("/")), data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "X-29next-API-Version": API_VERSION,
                     "Accept": "application/json", "Content-Type": "application/json"},
        )
        with self.opener.open(request, timeout=self.timeout) as response:
            payload = response.read()
            return json.loads(payload) if payload else {}

    def list_fos(self, order_number: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"order_number": order_number})
        target = f"fulfillment-orders/?{query}"
        expected = urllib.parse.urlparse(self.base)
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        while target not in seen:
            seen.add(target)
            data = self._request("GET", target)
            if not isinstance(data, dict):
                results.extend(data)
                break
            results.extend(data.get("results", []))
            next_url = data.get("next")
            if not isinstance(next_url, str):
                break
            parsed = urllib.parse.urlparse(next_url)
            if parsed.scheme != "https" or parsed.netloc != expected.netloc:
                break
            target = next_url
        return results

    def get_fo(self, fo_id: str) -> dict[str, Any]:
        return self._request("GET", f"fulfillment-orders/{fo_id}/")

    def available_locations(self, fo_id: str) -> Any:
        return self._request("GET", f"fulfillment-orders/{fo_id}/available-locations/")

    def list_locations(self) -> Any:
        return self._request("GET", "locations/")

    def request_cancellation(self, fo_id: str) -> Any:
        return self._request("POST", f"fulfillment-orders/{fo_id}/cancellation-request/", {})

    def move(self, fo_id: str, destination: int) -> Any:
        return self._request("POST", f"fulfillment-orders/{fo_id}/move/", {"new_location_id": destination})


@dataclass
class Result:
    order_number: str
    original_fo_id: str = ""
    new_fo_id: str = ""
    action: str = ""
    status: str = ""
    destination: str = ""


def normalize_domain(raw: str, *, allow_host: bool = False) -> str:
    value = raw.strip().removeprefix("https://").removeprefix("http://").strip("/").lower()
    if not value or any(char in value for char in "/?#@:"):
        raise ValueError("--store must be a hostname without a path, port, or credentials")
    if "." not in value:
        if not value.replace("-", "").isalnum():
            raise ValueError("--store must be a valid store subdomain")
        return f"{value}.29next.store"
    if value.endswith(".29next.store") or allow_host:
        return value
    raise ValueError("refusing non-29next store host; use --allow-host only for an intentional custom admin domain")


def rows_from(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    if isinstance(value, dict):
        for key in ("results", "locations", "available_locations"):
            if isinstance(value.get(key), list):
                return rows_from(value[key])
    return []


def location_ids(value: Any) -> set[int]:
    found: set[int] = set()
    for row in rows_from(value):
        raw = row.get("id", row.get("location_id"))
        try:
            found.add(int(raw))
        except (TypeError, ValueError):
            pass
    return found


def assigned_id(fo: dict[str, Any]) -> int | None:
    raw = (fo.get("assigned_location") or {}).get("id")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def supported(fo: dict[str, Any], action: str) -> bool:
    return action in (fo.get("supported_actions") or [])


def new_fo_id(response: Any) -> str:
    if isinstance(response, dict):
        moved = response.get("moved_fulfillment_order") or {}
        return str(moved.get("id") or "")
    return ""


class BulkMover:
    def __init__(self, client: Any, source: int, destination: int, *, execute: bool = False,
                 poll_attempts: int = 6, poll_delay: float = 2.0, order_delay: float = 0.5,
                 sleep: Callable[[float], None] = time.sleep):
        self.client, self.source, self.destination = client, source, destination
        self.execute, self.poll_attempts, self.poll_delay = execute, poll_attempts, poll_delay
        self.order_delay = order_delay
        self.sleep = sleep
    def _destination_availability(self, fo: dict[str, Any]) -> bool | None:
        embedded = fo.get("available_locations") or fo.get("supported_locations")
        ids = location_ids(embedded)
        if not ids:
            try:
                ids = location_ids(self.client.available_locations(str(fo.get("id"))))
            except Exception:
                return None
        if not ids:
            return None
        return self.destination in ids

    def _move(self, order: str, fo: dict[str, Any], action: str) -> Result:
        fid = str(fo.get("id") or "")
        availability = self._destination_availability(fo)
        if availability is not True:
            action = "LOCATION_UNAVAILABLE" if availability is False else "LOCATION_UNVERIFIED"
            return Result(order, fid, action=action, status="error", destination=str(self.destination))
        if not self.execute:
            return Result(order, fid, action=f"WOULD_{action}", status="skipped", destination=str(self.destination))
        response = self.client.move(fid, self.destination)
        return Result(order, fid, new_fo_id(response), action, "success", str(self.destination))

    def process_order(self, order: str) -> Result:
        try:
            fos = self.client.list_fos(order)
            source_fos = [fo for fo in fos if assigned_id(fo) == self.source]
            if not fos:
                return Result(order, action="NOT_FOUND", status="skipped", destination=str(self.destination))
            if not source_fos and any(assigned_id(fo) == self.destination for fo in fos):
                return Result(order, action="ALREADY_MOVED", status="skipped", destination=str(self.destination))
            if len(source_fos) != 1:
                return Result(order, action="MANUAL_REVIEW", status="error", destination=str(self.destination))
            fo = source_fos[0]
            fid, state = str(fo.get("id") or ""), str(fo.get("status") or "")
            if (state == "canceled" and fo.get("request_status") == "cancel_accepted"
                    and supported(fo, "move")):
                return self._move(order, fo, "CANCEL+MOVED")
            if state == "open" and supported(fo, "move"):
                return self._move(order, fo, "MOVED")
            if state == "processing":
                if str(fo.get("request_status") or "") in {
                    "cancel_rejected", "cancellation_rejected"
                }:
                    return Result(order, fid, action="CANCEL_REJECTED", status="error",
                                  destination=str(self.destination))
                availability = self._destination_availability(fo)
                if availability is not True:
                    action = "LOCATION_UNAVAILABLE" if availability is False else "LOCATION_UNVERIFIED"
                    return Result(order, fid, action=action, status="error", destination=str(self.destination))
                if not self.execute:
                    return Result(order, fid, action="WOULD_CANCEL+MOVE", status="skipped", destination=str(self.destination))
                if str(fo.get("request_status") or "") not in {
                    "cancel_pending", "cancel_requested", "cancellation_pending", "cancellation_requested"
                }:
                    self.client.request_cancellation(fid)
                latest = fo
                for attempt in range(self.poll_attempts):
                    latest = self.client.get_fo(fid)
                    request_status = latest.get("request_status")
                    if request_status in {"cancel_rejected", "cancellation_rejected"}:
                        return Result(order, fid, action="CANCEL_REJECTED", status="error", destination=str(self.destination))
                    if request_status == "cancel_accepted" and supported(latest, "move"):
                        return self._move(order, latest, "CANCEL+MOVED")
                    if attempt + 1 < self.poll_attempts:
                        self.sleep(self.poll_delay)
                return Result(order, fid, action="CANCEL_PENDING", status="error", destination=str(self.destination))
            return Result(order, fid, action=f"SKIPPED_{state.upper() or 'UNKNOWN'}", status="skipped", destination=str(self.destination))
        except Exception as exc:
            code = getattr(exc, "code", None)
            action = f"HTTP_ERROR_{code}" if code else f"ERROR_{type(exc).__name__}"
            return Result(order, action=action, status="error", destination=str(self.destination))

    def run(self, orders: Iterable[str], output: Path, completed: set[str] | None = None) -> list[Result]:
        completed = completed or set()
        orders = list(orders)
        output.parent.mkdir(parents=True, exist_ok=True)
        write_header = not output.exists() or output.stat().st_size == 0
        results: list[Result] = []
        with output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if write_header:
                writer.writeheader(); handle.flush()
            for order in orders:
                if order in completed:
                    continue
                result = self.process_order(order)
                writer.writerow(asdict(result)); handle.flush()
                results.append(result)
                print(f"Order {order}: {result.action}", flush=True)
                if self.order_delay:
                    self.sleep(self.order_delay)
        return results


def read_orders(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        key = next((k for k in (reader.fieldnames or []) if k.lower().replace(" ", "_") == "order_number"), None)
        if key is None:
            raise ValueError("input CSV must contain an Order Number or order_number column")
        return [str(row[key]).strip() for row in reader if str(row.get(key, "")).strip()]


def resume_completed(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            row["order_number"] for row in csv.DictReader(handle)
            if row.get("status") in COMPLETED_STATUSES
            and not row.get("action", "").startswith("WOULD_")
        }


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", required=True, help="Store subdomain or domain")
    p.add_argument("--allow-host", action="store_true",
                   help="Allow an intentional custom admin hostname outside .29next.store")
    p.add_argument("--input", required=True, type=Path, help="CSV containing order_number")
    p.add_argument("--source", required=True, type=int, help="Source location ID")
    p.add_argument("--destination", required=True, type=int, help="Destination location ID")
    p.add_argument("--results", required=True, type=Path, help="Append-only results CSV")
    p.add_argument("--resume", type=Path, help="Prior results CSV whose completed orders are skipped")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="execute", action="store_false",
                      help="List planned actions without mutating (default)")
    mode.add_argument("--execute", action="store_true",
                      help="Issue cancellation and move requests")
    p.set_defaults(execute=False)
    p.add_argument("--poll-attempts", type=int, default=6)
    p.add_argument("--poll-delay", type=float, default=2.0)
    p.add_argument("--order-delay", type=float, default=0.5)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if args.source == args.destination:
        parser().error("--source and --destination must be different")
    token = os.environ.get("NEXT_ADMIN_API_TOKEN")
    if not token:
        print("NEXT_ADMIN_API_TOKEN is required", file=sys.stderr)
        return 2
    try:
        client = AdminClient(args.store, token, allow_host=args.allow_host)
    except ValueError as exc:
        parser().error(str(exc))
    mover = BulkMover(client, args.source, args.destination,
                      execute=args.execute, poll_attempts=args.poll_attempts,
                      poll_delay=args.poll_delay, order_delay=args.order_delay)
    rows = mover.run(read_orders(args.input), args.results, resume_completed(args.resume))
    return 1 if any(row.status == "error" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
