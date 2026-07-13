#!/usr/bin/env python3
"""Safely apply allowlisted Next Commerce subscription actions in bulk."""
from __future__ import annotations

import argparse
import csv
import hashlib
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
FIELDS = ["subscription_id", "order_id", "customer_id", "action",
          "payload_fingerprint", "status", "error_code", "error_message"]
COMPLETED_STATUSES = {"success"}
# action: (method, endpoint template, allowed payload fields)
ACTIONS = {
    "pause": ("POST", "subscriptions/{id}/pause/", frozenset({"pause_until"})),
    "resume": ("POST", "subscriptions/{id}/resume/", frozenset()),
    "cancel": ("POST", "subscriptions/{id}/cancel/", frozenset({
        "cancel_reason", "cancel_reason_other_message", "send_cancel_notification"})),
    "renew": ("POST", "subscriptions/{id}/renew/", frozenset()),
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
    payload_fingerprint: str = ""
    status: str = ""
    error_code: str = ""
    error_message: str = ""


class ResumeState(set[tuple[str, str, str]]):
    def __init__(self, completed: Iterable[tuple[str, str, str]] = (), *,
                 needs_verification: Iterable[tuple[str, str, str]] = ()):
        unresolved = set(needs_verification)
        super().__init__(set(completed) | unresolved)
        self.needs_verification = unresolved


def validate_action(action: str, payload: dict[str, Any]) -> tuple[str, str]:
    if action not in ACTIONS: raise ValueError(f"action is not allowlisted: {action}")
    method, endpoint, allowed = ACTIONS[action]
    extras = set(payload) - allowed
    if extras: raise ValueError(f"payload fields are not allowed for {action}: {', '.join(sorted(extras))}")
    return method, endpoint


def fsync_dir(directory: Path) -> None:
    fd = os.open(str(directory), os.O_RDONLY)
    try:
        os.fsync(fd)
    except OSError:
        pass  # some filesystems disallow directory fsync; best effort
    finally:
        os.close(fd)


def resume_state(*paths: Path | None) -> ResumeState:
    """Merge resume state across every journal that could hold this run's history
    (typically both --resume and the active --results file), so re-running the
    same command cannot repeat a non-idempotent mutation recorded in --results."""
    completed: set[tuple[str, str, str]] = set()
    attempted: set[tuple[str, str, str]] = set()
    for path in paths:
        state = resume_completed(path)
        attempted |= state.needs_verification
        completed |= (set(state) - state.needs_verification)
    attempted -= completed  # a success in any journal resolves an attempt
    return ResumeState(completed, needs_verification=attempted)


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_mutation(resp: Any, sid: str, payload: dict[str, Any],
                    action: str) -> tuple[bool, str]:
    """Confirm a 2xx mutation actually applied before recording success.

    A subscription endpoint can return 2xx yet ignore a field, apply a different
    value, or hand back an unrelated/malformed object. Recording such a row as
    success (terminal for --resume) would silently leave subscriptions unchanged,
    so require the response to confirm the subscription identity. For `update`
    (the field-writing PATCH), every requested field must be present in the
    response and equal to what we asked; for other actions, any echoed field
    that disagrees with the request is also a failure.
    """
    if not isinstance(resp, dict):
        return False, "mutation response was not an object"
    subject = resp
    if resp.get("id") is None and resp.get("subscription_id") is None:
        nested = resp.get("subscription")
        if isinstance(nested, dict):
            subject = nested
    returned_id = subject.get("id", subject.get("subscription_id"))
    if returned_id is None:
        return False, "response did not include a subscription identity to confirm"
    if str(returned_id) != str(sid):
        return False, f"response identity {returned_id} does not match requested {sid}"
    for field, want in payload.items():
        if action == "update" and field not in subject:
            return False, f"update response did not confirm field {field}"
        if field in subject and str(subject[field]) != str(want):
            return False, f"field {field} was not applied as requested"
    return True, ""


def csv_payload_value(raw: Any) -> Any:
    value = str(raw).strip()
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


class BulkSubscription:
    def __init__(self, client: Any, action: str, payload: dict[str, Any], *, execute: bool = False,
                 sleep: Callable[[float], None] = time.sleep, row_delay: float = 0.26):
        self.method, self.endpoint = validate_action(action, payload)
        self.client, self.action, self.payload, self.execute = client, action, payload, execute
        self.sleep, self.row_delay = sleep, row_delay

    def payload_for(self, row: dict[str, Any]) -> dict[str, Any]:
        payload = dict(self.payload)
        allowed = ACTIONS[self.action][2]
        payload.update({field: row[field] for field in allowed if field in row})
        validate_action(self.action, payload)
        return payload

    def process(self, row: dict[str, Any], payload: dict[str, Any] | None = None,
                before_mutation: Callable[[Result], None] | None = None) -> Result:
        sid = row["subscription_id"]
        payload = self.payload_for(row) if payload is None else payload
        base = Result(sid, row.get("order_id", ""), row.get("customer_id", ""),
                      self.action, fingerprint(payload))
        if self.action == "update" and not payload:
            base.status = "error"
            base.error_code = "EMPTY_UPDATE"
            base.error_message = "update payload has no recognized fields"
            return base
        if not self.execute:
            base.status = "dry_run"; return base
        try:
            if before_mutation is not None:
                before_mutation(Result(
                    base.subscription_id, base.order_id, base.customer_id,
                    base.action, base.payload_fingerprint, "attempted",
                ))
            resp = self.client.mutate(
                self.method, self.endpoint.format(id=urllib.parse.quote(sid, safe="")),
                payload)
            ok, reason = verify_mutation(resp, sid, payload, self.action)
            if ok:
                base.status = "success"
            else:
                base.status = "error"
                base.error_code = "NEEDS_VERIFICATION"
                base.error_message = reason
        except Exception as exc:
            code = getattr(exc, "code", None)
            base.status = "error"
            base.error_code = f"HTTP_{code}" if code else type(exc).__name__
            base.error_message = str(getattr(exc, "reason", "request failed"))[:240]
        return base

    def run(self, rows: Iterable[dict[str, Any]], output: Path,
            completed: set[tuple[str, str, str]] | None = None) -> list[Result]:
        if completed is None: completed = set()
        needs_verification = getattr(completed, "needs_verification", set())
        encountered: set[tuple[str, str, str]] = set()
        output.parent.mkdir(parents=True, exist_ok=True)
        new_file = not output.exists() or output.stat().st_size == 0
        if not new_file:
            # Fail closed: an existing journal with a foreign/older header can't be
            # parsed by resume_state, so appending to it would silently lose the
            # at-most-once guarantee for renew/retry.
            with output.open(newline="", encoding="utf-8") as existing:
                header = next(csv.reader(existing), [])
            if header != FIELDS:
                raise ValueError(
                    f"results file {output} has an incompatible header; "
                    "use a fresh --results path or the matching journal"
                )
        results = []
        with output.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader(); handle.flush(); os.fsync(handle.fileno())
                # A new journal's parent-directory entry is not durable until the
                # directory itself is synced; without this a crash could lose the
                # whole journal and let --resume repeat a non-idempotent mutation.
                fsync_dir(output.parent)
            for row in rows:
                sid = row["subscription_id"]
                payload = self.payload_for(row)
                payload_id = fingerprint(payload)
                key = (sid, self.action, payload_id)
                if key in needs_verification:
                    result = Result(
                        sid, row.get("order_id", ""), row.get("customer_id", ""),
                        self.action, payload_id, "error", "NEEDS_VERIFICATION",
                        "prior attempt has no recorded success; verify manually",
                    )
                    writer.writerow(asdict(result)); handle.flush(); results.append(result)
                    print(f"Subscription {result.subscription_id}: {result.status}", flush=True)
                    if self.row_delay: self.sleep(self.row_delay)
                    continue
                if key in completed: continue
                if key in encountered:
                    result = Result(sid, row.get("order_id", ""),
                                    row.get("customer_id", ""), "DUPLICATE",
                                    payload_id, "skipped")
                else:
                    encountered.add(key)
                    def record_attempt(attempt: Result) -> None:
                        # Durably persist the attempt BEFORE the non-idempotent
                        # request so a crash/power loss cannot let --resume repeat
                        # a renew/retry/etc. flush() alone only reaches the OS
                        # cache; fsync() forces it to disk.
                        writer.writerow(asdict(attempt)); handle.flush()
                        os.fsync(handle.fileno())

                    result = self.process(row, payload, record_attempt)
                writer.writerow(asdict(result)); handle.flush(); results.append(result)
                print(f"Subscription {result.subscription_id}: {result.status}", flush=True)
                if self.row_delay: self.sleep(self.row_delay)
        return results


def read_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        keys = {str(k).lower().replace(" ", "_"): k for k in reader.fieldnames or []}
        sid = keys.get("subscription_id") or keys.get("id") or keys.get("sub_id")
        if not sid: raise ValueError("input CSV requires subscription_id")
        payload_keys = {
            field: keys[field]
            for field in set().union(*(details[2] for details in ACTIONS.values()))
            if field in keys
        }
        rows = []
        for source in reader:
            subscription_id = str(source.get(sid, "")).strip()
            if not subscription_id:
                continue
            row: dict[str, Any] = {
                "subscription_id": subscription_id,
                "order_id": str(source.get(keys.get("order_id", ""), "")).strip(),
                "customer_id": str(source.get(keys.get("customer_id", ""), "")).strip(),
            }
            for field, key in payload_keys.items():
                raw = source.get(key, "")
                if raw is not None and str(raw).strip():
                    row[field] = csv_payload_value(raw)
            rows.append(row)
        return rows


def resume_completed(path: Path | None) -> ResumeState:
    if path is None or not path.exists(): return ResumeState()
    completed: set[tuple[str, str, str]] = set()
    attempted: set[tuple[str, str, str]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            key = (row.get("subscription_id", ""), row.get("action", ""),
                   row.get("payload_fingerprint", ""))
            if not all(key): continue
            if row.get("status") in COMPLETED_STATUSES:
                completed.add(key)
                attempted.discard(key)
            elif (row.get("status") or "").lower() == "attempted":
                attempted.add(key)
    return ResumeState(completed, needs_verification=attempted)


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
    try:
        results = worker.run(rows, args.results, resume_state(args.resume, args.results))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); return 2
    return 1 if any(row.status == "error" for row in results) else 0


if __name__ == "__main__": raise SystemExit(main())
