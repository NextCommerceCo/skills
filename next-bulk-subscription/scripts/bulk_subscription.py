#!/usr/bin/env python3
"""Safely apply allowlisted Next Commerce subscription actions in bulk."""
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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

API_VERSION = "2024-04-01"
FIELDS = ["subscription_id", "order_id", "customer_id", "action", "status",
          "error_code", "error_message"]
COMPLETED_STATUSES = {"success"}
# action: (method, endpoint template, allowed payload fields)
ACTIONS = {
    "pause": ("POST", "subscriptions/{id}/pause/", frozenset({"pause_until"})),
    "resume": ("POST", "subscriptions/{id}/resume/", frozenset()),
    "cancel": ("POST", "subscriptions/{id}/cancel/", frozenset({
        "cancel_reason", "cancel_reason_other_message", "send_cancel_notification"})),
    "retry": ("POST", "subscriptions/{id}/retry/", frozenset()),
    "update": ("PATCH", "subscriptions/{id}/", frozenset({
        "next_renewal_date", "interval", "interval_count", "payment_details",
        "shipping_address", "billing_address"})),
}


class AuthenticatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing authenticated redirect to {newurl}", headers, fp,
        )


def normalize_domain(raw: str) -> str:
    value = raw.strip().removeprefix("https://").removeprefix("http://").strip("/").lower()
    if not value or any(char in value for char in "/?#@:"): raise ValueError("invalid --store")
    if "." not in value: value = f"{value}.29next.store"
    if not value.endswith(".29next.store"): raise ValueError("refusing non-29next store host")
    return value


class AdminClient:
    def __init__(self, domain: str, token: str, *, min_interval: float = 0.25,
                 timeout: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.base, self.token = f"https://{normalize_domain(domain)}/api/admin/", token
        self.min_interval, self.timeout, self.clock, self.sleep = min_interval, timeout, clock, sleep
        self._last = None
        self.opener = urllib.request.build_opener(AuthenticatedRedirectHandler())

    def mutate(self, method: str, path: str, payload: dict[str, Any]) -> Any:
        if self._last is not None:
            delay = self.min_interval - (self.clock() - self._last)
            if delay > 0: self.sleep(delay)
        self._last = self.clock()
        request = urllib.request.Request(urllib.parse.urljoin(self.base, path), method=method,
            data=json.dumps(payload).encode(), headers={"Authorization": f"Bearer {self.token}",
            "X-29next-API-Version": API_VERSION, "Accept": "application/json",
            "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read(); return json.loads(raw) if raw else {}


@dataclass
class Result:
    subscription_id: str
    order_id: str = ""
    customer_id: str = ""
    action: str = ""
    status: str = ""
    error_code: str = ""
    error_message: str = ""


def validate_action(action: str, payload: dict[str, Any]) -> tuple[str, str]:
    if action not in ACTIONS: raise ValueError(f"action is not allowlisted: {action}")
    method, endpoint, allowed = ACTIONS[action]
    extras = set(payload) - allowed
    if extras: raise ValueError(f"payload fields are not allowed for {action}: {', '.join(sorted(extras))}")
    return method, endpoint


class BulkSubscription:
    def __init__(self, client: Any, action: str, payload: dict[str, Any], *, execute: bool = False,
                 sleep: Callable[[float], None] = time.sleep, row_delay: float = 0.26):
        self.method, self.endpoint = validate_action(action, payload)
        self.client, self.action, self.payload, self.execute = client, action, payload, execute
        self.sleep, self.row_delay = sleep, row_delay

    def process(self, row: dict[str, str]) -> Result:
        sid = row["subscription_id"]
        base = Result(sid, row.get("order_id", ""), row.get("customer_id", ""), self.action)
        if not self.execute:
            base.status = "dry_run"; return base
        try:
            self.client.mutate(self.method, self.endpoint.format(id=urllib.parse.quote(sid, safe="")),
                               self.payload)
            base.status = "success"
        except Exception as exc:
            code = getattr(exc, "code", None)
            base.status = "error"
            base.error_code = f"HTTP_{code}" if code else type(exc).__name__
            base.error_message = str(getattr(exc, "reason", "request failed"))[:240]
        return base

    def run(self, rows: Iterable[dict[str, str]], output: Path, completed: set[str] | None = None) -> list[Result]:
        completed = completed or set(); processed: set[str] = set()
        output.parent.mkdir(parents=True, exist_ok=True)
        write_header = not output.exists() or output.stat().st_size == 0; results = []
        with output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if write_header: writer.writeheader(); handle.flush()
            for row in rows:
                sid = row["subscription_id"]
                if sid in completed: continue
                if sid in processed:
                    result = Result(sid, row.get("order_id", ""),
                                    row.get("customer_id", ""), "DUPLICATE", "skipped")
                else:
                    result = self.process(row)
                    if result.status == "success":
                        processed.add(sid)
                writer.writerow(asdict(result)); handle.flush(); results.append(result)
                print(f"Subscription {result.subscription_id}: {result.status}", flush=True)
                if self.row_delay: self.sleep(self.row_delay)
        return results


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        keys = {str(k).lower().replace(" ", "_"): k for k in reader.fieldnames or []}
        sid = keys.get("subscription_id") or keys.get("id") or keys.get("sub_id")
        if not sid: raise ValueError("input CSV requires subscription_id")
        return [{"subscription_id": str(row.get(sid, "")).strip(),
                 "order_id": str(row.get(keys.get("order_id", ""), "")).strip(),
                 "customer_id": str(row.get(keys.get("customer_id", ""), "")).strip()}
                for row in reader if row.get(sid)]


def resume_completed(path: Path | None) -> set[str]:
    if path is None or not path.exists(): return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {row["subscription_id"] for row in csv.DictReader(handle)
                if row.get("status") in COMPLETED_STATUSES}


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", required=True); p.add_argument("--input", required=True, type=Path)
    p.add_argument("--results", required=True, type=Path); p.add_argument("--resume", type=Path)
    p.add_argument("--action", required=True); p.add_argument("--payload", default="{}",
        help="JSON object containing only fields allowlisted for the action")
    p.add_argument("--limit", type=int)
    mode = p.add_mutually_exclusive_group(); mode.add_argument("--dry-run", dest="execute", action="store_false")
    mode.add_argument("--execute", action="store_true"); p.set_defaults(execute=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    token = os.environ.get("NEXT_ADMIN_API_TOKEN")
    if not token: print("NEXT_ADMIN_API_TOKEN is required", file=sys.stderr); return 2
    try:
        payload = json.loads(args.payload)
        if not isinstance(payload, dict): raise ValueError("--payload must be a JSON object")
        worker = BulkSubscription(AdminClient(args.store, token), args.action, payload,
                                  execute=args.execute)
        rows = read_rows(args.input)
    except (ValueError, OSError, json.JSONDecodeError) as exc: parser().error(str(exc))
    rows = rows[:args.limit] if args.limit is not None else rows
    results = worker.run(rows, args.results, resume_completed(args.resume))
    return 1 if any(row.status == "error" for row in results) else 0


if __name__ == "__main__": raise SystemExit(main())
