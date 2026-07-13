#!/usr/bin/env python3
"""Safely create Next Commerce fulfillments from a tracking CSV."""
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
FIELDS = ["order_number", "fulfillment_id", "tracking_code", "carrier", "action", "status"]
COMPLETED_STATUSES = {"success"}
VALID_CARRIERS = {"4px", "amazon", "asendia", "australia_post", "china_post",
                  "deutsche_de", "dhl", "dhl_ecommerce", "fedex", "firstmile",
                  "gofo_express", "hermesworld_uk", "myhermes", "ontrac", "other",
                  "royal_mail", "speedx", "swiss_post", "ulala", "uniuni", "ups",
                  "usps", "yunexpress"}


class AuthenticatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> Any:
        raise urllib.error.HTTPError(
            req.full_url, code,
            f"refusing authenticated redirect to {newurl}", headers, fp,
        )


class MalformedResponse(ValueError):
    """The API response cannot safely authorize a fulfillment."""


def normalize_domain(raw: str) -> str:
    value = raw.strip().removeprefix("https://").removeprefix("http://").strip("/").lower()
    if not value or any(char in value for char in "/?#@:"):
        raise ValueError("--store must be a subdomain or .29next.store hostname")
    if "." not in value:
        if not value.replace("-", "").isalnum():
            raise ValueError("--store must be a valid store subdomain")
        return f"{value}.29next.store"
    if not value.endswith(".29next.store"):
        raise ValueError("refusing non-29next store host")
    return value


class AdminClient:
    def __init__(self, domain: str, token: str, *, min_interval: float = 0.25,
                 timeout: float = 30.0, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep):
        self.base = f"https://{normalize_domain(domain)}/api/admin/"
        self.token, self.min_interval, self.timeout = token, min_interval, timeout
        self.clock, self.sleep, self._last = clock, sleep, None
        self.opener = urllib.request.build_opener(AuthenticatedRedirectHandler())

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if self._last is not None:
            delay = self.min_interval - (self.clock() - self._last)
            if delay > 0:
                self.sleep(delay)
        self._last = self.clock()
        request = urllib.request.Request(
            urllib.parse.urljoin(self.base, path.lstrip("/")), method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}",
                     "X-29next-API-Version": API_VERSION, "Accept": "application/json",
                     "Content-Type": "application/json"})
        with self.opener.open(request, timeout=self.timeout) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}

    def list_fulfillment_orders(self, order: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"order_number": order})
        target = f"fulfillment-orders/?{query}"
        expected = urllib.parse.urlparse(self.base)
        seen: set[str] = set()
        results: list[dict[str, Any]] = []
        while True:
            canonical_target = urllib.parse.urljoin(self.base, target)
            if canonical_target in seen:
                raise MalformedResponse(
                    f"pagination link repeats an already-seen page: {target}"
                )
            seen.add(canonical_target)
            data = self._request("GET", target)
            if not isinstance(data, dict):
                raise MalformedResponse("fulfillment-order list response is not an object")
            rows = data.get("results")
            if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
                raise MalformedResponse("fulfillment-order results is not a list of objects")
            results.extend(rows)
            next_url = data.get("next")
            if next_url is None:
                break
            if not isinstance(next_url, str):
                raise MalformedResponse("pagination link is not a string")
            parsed = urllib.parse.urlparse(next_url)
            if parsed.scheme != "https" or parsed.netloc != expected.netloc:
                raise MalformedResponse(
                    f"refusing pagination link outside the store host: {next_url}"
                )
            target = next_url
        return results

    def fulfill(self, fulfillment_id: str, tracking: str, carrier: str,
                notify: bool = True) -> Any:
        return self._request("POST", f"fulfillment-orders/{fulfillment_id}/fulfillments/",
                             {"tracking_info": [{"tracking_code": tracking,
                                                  "carrier": carrier}], "notify": notify})


def verify_fulfillment(resp: Any, fulfillment_id: str, tracking: str) -> bool:
    """Confirm a 2xx fulfillment actually applied before recording success.

    An empty/malformed/unrelated 2xx body would otherwise be recorded terminal
    and permanently skipped on resume even though nothing was fulfilled. Accept
    only a response that echoes the fulfillment-order id or the tracking code.
    """
    if not isinstance(resp, dict):
        return False
    haystack = json.dumps(resp)
    if tracking and tracking in haystack:
        return True
    for key in ("fulfillment_order", "fulfillment_order_id", "id"):
        value = resp.get(key)
        if isinstance(value, dict):
            value = value.get("id")
        if value not in (None, "") and str(value) == str(fulfillment_id):
            return True
    return False


def inferred_carrier(tracking: str) -> tuple[str, str]:
    code = tracking.strip().upper()
    if code.startswith("YT"): return "prefix:YT", "yunexpress"
    if code.startswith("4PX"): return "prefix:4PX", "4px"
    if code.startswith("92") and len(code) >= 20: return "prefix:92-length>=20", "usps"
    if code.startswith("1Z"): return "prefix:1Z", "ups"
    if code.isdigit() and 12 <= len(code) <= 15: return "digits:12-15", "fedex"
    if code.startswith("JD"): return "prefix:JD", "dhl"
    if code.isdigit() and len(code) == 10 and code.startswith("0"): return "digits:10-prefix:0", "dhl"
    return "unmatched", "other"


def load_carrier_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    path = Path(raw)
    value = path.read_text(encoding="utf-8") if path.exists() else raw
    data = json.loads(value)
    if not isinstance(data, dict) or any(v not in VALID_CARRIERS for v in data.values()):
        raise ValueError("--carrier-map must be a JSON object with valid carrier slugs")
    return {str(k): str(v) for k, v in data.items()}


@dataclass
class Result:
    order_number: str
    fulfillment_id: str = ""
    tracking_code: str = ""
    carrier: str = ""
    action: str = ""
    status: str = ""


class ResumeState(set[tuple[str, str]]):
    def __init__(self, completed: Iterable[tuple[str, str]] = (), *,
                 needs_verification: Iterable[tuple[str, str]] = ()):
        unresolved = set(needs_verification)
        super().__init__(set(completed) | unresolved)
        self.needs_verification = unresolved


class BulkFulfiller:
    def __init__(self, client: Any, *, execute: bool = False, notify: bool = True,
                 carrier_map: dict[str, str] | None = None,
                 sleep: Callable[[float], None] = time.sleep, row_delay: float = 0.5):
        self.client, self.execute, self.notify = client, execute, notify
        self.carrier_map, self.sleep, self.row_delay = carrier_map or {}, sleep, row_delay

    def process(self, row: dict[str, str],
                before_mutation: Callable[[Result], None] | None = None) -> Result:
        order = row["order_number"].strip()
        tracking = row["tracking_code"].strip()
        if not order:
            return Result(order, tracking_code=tracking,
                          action="INVALID_ORDER_NUMBER", status="error")
        if not tracking:
            return Result(order, action="MISSING_TRACKING", status="error")
        explicit = row.get("carrier", "").strip().lower()
        pattern, guess = inferred_carrier(tracking)
        carrier = explicit or self.carrier_map.get(pattern, "")
        if explicit and explicit not in VALID_CARRIERS:
            return Result(order, tracking_code=tracking, carrier=explicit,
                          action="INVALID_CARRIER", status="error")
        if not carrier:
            return Result(order, tracking_code=tracking, carrier=guess,
                          action=f"UNCONFIRMED_CARRIER:{pattern}", status="error")
        try:
            fos = self.client.list_fulfillment_orders(order)
            for fo in fos:
                identities = []
                if fo.get("order_number") not in (None, ""):
                    identities.append(fo["order_number"])
                nested = fo.get("order")
                if isinstance(nested, dict):
                    if nested.get("number") not in (None, ""):
                        identities.append(nested["number"])
                if not identities or any(str(value).strip() != order for value in identities):
                    raise MalformedResponse(
                        "fulfillment order does not match requested order"
                    )
            eligible = [fo for fo in fos if fo.get("status") in {"processing", "open"}]
            if not eligible:
                return Result(order, tracking_code=tracking, carrier=carrier,
                              action="NOT_FOUND", status="skipped")
            if len(eligible) != 1 or eligible[0].get("id") in (None, ""):
                return Result(order, tracking_code=tracking, carrier=carrier,
                              action="MANUAL_REVIEW", status="error")
            fid = str(eligible[0]["id"])
            if not self.execute:
                return Result(order, fid, tracking, carrier, "WOULD_FULFILL", "skipped")
            if before_mutation is not None:
                before_mutation(Result(order, fid, tracking, carrier,
                                       "ATTEMPTED", "attempted"))
            resp = self.client.fulfill(fid, tracking, carrier, self.notify)
            if not verify_fulfillment(resp, fid, tracking):
                return Result(order, fid, tracking, carrier,
                              "NEEDS_VERIFICATION", "error")
            return Result(order, fid, tracking, carrier, "FULFILLED", "success")
        except MalformedResponse:
            return Result(order, tracking_code=tracking, carrier=carrier,
                          action="MALFORMED_RESPONSE", status="error")
        except Exception as exc:
            code = getattr(exc, "code", None)
            action = f"HTTP_ERROR_{code}" if code else f"ERROR_{type(exc).__name__}"
            return Result(order, tracking_code=tracking, carrier=carrier,
                          action=action, status="error")

    def run(self, rows: Iterable[dict[str, str]], output: Path,
            completed: set[tuple[str, str]] | None = None) -> list[Result]:
        if completed is None: completed = set()
        needs_verification = getattr(completed, "needs_verification", set())
        in_run_encountered: set[tuple[str, str]] = set()
        output.parent.mkdir(parents=True, exist_ok=True)
        new_file = not output.exists() or output.stat().st_size == 0
        if not new_file:
            # Fail closed on a foreign/older journal header so appending can't
            # silently lose the at-most-once guarantee for fulfillment POSTs.
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
                # Make the new journal's directory entry durable so a crash can't
                # lose the whole journal and let --resume repeat a fulfillment.
                fsync_dir(output.parent)
            for row in rows:
                order = str(row["order_number"]).strip()
                tracking = str(row["tracking_code"]).strip()
                key = (order, tracking)
                if key in needs_verification:
                    result = Result(
                        order,
                        tracking_code=tracking,
                        carrier=str(row.get("carrier", "")).strip().lower(),
                        action="NEEDS_VERIFICATION",
                        status="error",
                    )
                    writer.writerow(asdict(result)); handle.flush(); results.append(result)
                    print(f"Order {result.order_number}: {result.action}", flush=True)
                    if self.row_delay: self.sleep(self.row_delay)
                    continue
                if key in completed: continue
                if key in in_run_encountered:
                    result = Result(
                        order,
                        tracking_code=tracking,
                        carrier=str(row.get("carrier", "")).strip().lower(),
                        action="DUPLICATE",
                        status="skipped",
                    )
                else:
                    in_run_encountered.add(key)
                    def record_attempt(attempt: Result) -> None:
                        # Durably persist the attempt BEFORE the fulfillment POST
                        # so a crash/power loss cannot let --resume create a
                        # duplicate fulfillment. flush() only reaches the OS cache;
                        # fsync() forces it to disk.
                        writer.writerow(asdict(attempt)); handle.flush()
                        os.fsync(handle.fileno())

                    result = self.process(row, record_attempt)
                writer.writerow(asdict(result)); handle.flush(); results.append(result)
                print(f"Order {result.order_number}: {result.action}", flush=True)
                if self.row_delay: self.sleep(self.row_delay)
        return results


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        normalized = {str(k).lower().replace(" ", "_"): k for k in reader.fieldnames or []}
        order_key = normalized.get("order_number") or normalized.get("order_#")
        tracking_key = (normalized.get("tracking_code") or normalized.get("tracking_number")
                        or normalized.get("tracking_no") or normalized.get("tracking_#"))
        carrier_key = normalized.get("carrier")
        if not order_key or not tracking_key:
            raise ValueError("input CSV requires order_number and tracking_code columns")
        # csv.DictReader yields None for cells absent from a short row; coalesce
        # to "" so an absent tracking cell becomes MISSING_TRACKING, never the
        # literal string "None".
        return [{"order_number": str(row.get(order_key) or "").strip(),
                 "tracking_code": str(row.get(tracking_key) or "").strip(),
                 "carrier": (str(row.get(carrier_key) or "").strip() if carrier_key else "")}
                for row in reader if str(row.get(order_key) or "").strip()]


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
    same command cannot repeat a fulfillment recorded only in --results."""
    completed: set[tuple[str, str]] = set()
    attempted: set[tuple[str, str]] = set()
    for path in paths:
        state = resume_completed(path)
        attempted |= state.needs_verification
        completed |= (set(state) - state.needs_verification)
    # Journal ordering across files is unknown, so an unresolved attempt in ANY
    # journal must win over an older success elsewhere: never subtract it out.
    return ResumeState(completed, needs_verification=attempted)


def resume_completed(path: Path | None) -> ResumeState:
    if path is None or not path.exists(): return ResumeState()
    completed: set[tuple[str, str]] = set()
    attempted: set[tuple[str, str]] = set()
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader, [])
        if header and header != FIELDS:
            # Fail closed: a foreign header makes DictReader emit empty keys, so
            # prior successes/attempts would be invisible and re-POSTed.
            raise ValueError(
                f"resume journal {path} has an incompatible header; "
                "point --resume at a matching journal or omit it"
            )
        handle.seek(0)
        for row in csv.DictReader(handle):
            key = (row.get("order_number", ""), row.get("tracking_code", ""))
            if not all(key): continue
            if row.get("status") in COMPLETED_STATUSES:
                completed.add(key)
                attempted.discard(key)
            elif (row.get("action") or "").upper() in {
                "ATTEMPTED", "NEEDS_VERIFICATION"
            }:
                attempted.add(key)
    return ResumeState(completed, needs_verification=attempted)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--store", required=True)
    p.add_argument("--input", required=True, type=Path)
    p.add_argument("--results", required=True, type=Path)
    p.add_argument("--resume", type=Path)
    p.add_argument("--carrier-map", help="JSON object/path confirming inferred pattern-to-carrier mappings")
    p.add_argument("--no-notify", action="store_true")
    p.add_argument("--limit", type=int)
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", dest="execute", action="store_false")
    mode.add_argument("--execute", action="store_true")
    p.set_defaults(execute=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    token = os.environ.get("NEXT_ADMIN_API_TOKEN")
    if not token:
        print("NEXT_ADMIN_API_TOKEN is required", file=sys.stderr); return 2
    try:
        client = AdminClient(args.store, token)
        rows = read_rows(args.input)
        carrier_map = load_carrier_map(args.carrier_map)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        parser().error(str(exc))
    rows = rows[:args.limit] if args.limit is not None else rows
    try:
        results = BulkFulfiller(client, execute=args.execute, notify=not args.no_notify,
                                carrier_map=carrier_map).run(
                                    rows, args.results,
                                    resume_state(args.resume, args.results))
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr); return 2
    return 1 if any(row.status == "error" for row in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
