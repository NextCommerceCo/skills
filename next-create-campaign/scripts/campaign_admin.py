#!/usr/bin/env python3
"""Provision a Campaigns App campaign over the NEXT Admin API.

Backs the `/next-create-campaign` skill and is normally run through
`next-create-campaign.sh`. Seven subcommands, in the usual order:

    discover  --store <slug>                      read-only store snapshot
    metadata  --store <slug> [--apply]            audit or create the campaign metadata definitions
    recommend --discovery ... --hero ... --ctc ... build campaign-plan.json
    plan      --plan campaign-plan.json           validate + print requests + hash
    apply     --plan ... --yes --plan-sha256 ...  create campaign/packages/shipping/offers
    verify    --manifest ... --plan ...           read back + Cart API calculate
    teardown  --manifest ... --plan ... --yes     delete what THIS run created

References: references/admin-api-contract.md (API contract and file schemas) and
references/offer-doctrine.md (the reasoning `recommend` encodes).

Credentials: the Admin API token comes from {STORE}_NEXT_ADMIN_API_TOKEN in the
environment, else from that one line of .env in the current directory (the file
is read as text, never executed), else from NEXT_ADMIN_API_TOKEN. It is never
taken from the command line.

Safety properties (each has a unit test in tests/test_campaign_admin.py):
- The Admin token only ever travels to https://<slug>.29next.store, where <slug>
  is a single DNS label supplied on the command line. Pagination `next` links and
  redirects to any other origin abort the run.
- Run files land in a run directory that must be gitignored when it sits inside a
  git repository (checked with git check-ignore before the first write, and
  refused when git cannot answer). The run manifest, the only file holding the
  campaign api_key, is written atomically with mode 600.
- `apply` refuses to write without --yes and a --plan-sha256 matching the plan
  file on disk. A two-phase journal (pending -> created) plus reconcile-by-identity
  on --resume means a lost response never duplicates or orphans a resource.
- `teardown` deletes only ids recorded in the manifest, after reading each one back
  and checking its identity.
- Requests are paced under the documented 4 req/s limit; only GETs are retried.

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import namedtuple
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

API_VERSION = "2024-04-01"
TIMEOUT = 30
MIN_INTERVAL = 0.3          # seconds between requests: 4 req/s documented limit
MAX_PAGES = 1000
MAX_GET_RETRIES = 3
MAX_RETRY_AFTER = 30
TRANSIENT_STATUSES = (429, 500, 502, 503, 504)
STORE_DOMAIN = "29next.store"
CART_API_ORIGIN = "https://campaigns.apps.29next.com"
SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
DECIMAL_RE = re.compile(r"^\d{1,8}(?:\.\d{1,2})?$")
CODE_RE = re.compile(r"^[A-Z0-9]{1,64}$")
PRICE_ROUNDINGS = (None, "0.00", "0.95", "0.97", "0.99")
BENEFIT_TYPES = ("package_percentage", "shipping_percentage", "order_percentage")
CONDITION_TYPES = ("any", "count")
OFFER_TYPES = ("offer", "voucher")
DEFAULT_TIERS = (50, 55, 60)
DEFAULT_EXIT_PCT = 10

PROG = "next-create-campaign.sh"
RUNS_DIR_NAME = "next-create-campaign-runs"
MANIFEST_NAME = "run-manifest.json"
DOTENV_NAME = ".env"
GENERIC_TOKEN_ENV = "NEXT_ADMIN_API_TOKEN"
TOKEN_SUFFIX = "_NEXT_ADMIN_API_TOKEN"
CAMPAIGN_SCOPES = "campaigns:read, campaigns:write, catalogue:read, gateways:read"
METADATA_SCOPES = "metadata:read, metadata:write"


class CampaignAdminError(Exception):
    """Operator-facing failure. The CLI prints the message and exits 1."""


class HttpStatusError(CampaignAdminError):
    """A GET that did not answer 200. `status` is the HTTP status (None for a
    network failure), so callers can branch on it and build messages that never
    carry the response body."""

    def __init__(self, message: str, status, path: str):
        super().__init__(message)
        self.status = status
        self.path = path


# --------------------------------------------------------------------------- #
# Slug, origin, credentials
# --------------------------------------------------------------------------- #

def validate_slug(slug: str) -> str:
    if not isinstance(slug, str) or not SLUG_RE.match(slug):
        raise CampaignAdminError(
            f"store slug {slug!r} is not a single lowercase DNS label "
            "(letters, digits, hyphens; no dots, slashes or '@'); "
            f"pass the bare subdomain (mystore) or mystore.{STORE_DOMAIN}"
        )
    return slug


def normalize_store(value) -> str:
    """Accept mystore, mystore.29next.store or https://mystore.29next.store/...
    and return the bare subdomain. Anything else still fails validate_slug."""
    if not isinstance(value, str):
        return validate_slug(value)
    s = value.strip()
    had_scheme = False
    for scheme in ("https://", "http://"):
        if s.lower().startswith(scheme):
            s, had_scheme = s[len(scheme):], True
            break
    suffix = "." + STORE_DOMAIN
    host, sep, _ = s.partition("/")
    if sep and (had_scheme or host.lower().endswith(suffix)):
        s = host
    if s.lower().endswith(suffix):
        s = s[: -len(suffix)]
    return validate_slug(s.lower())


def slug_to_origin(slug: str) -> str:
    return f"https://{validate_slug(slug)}.{STORE_DOMAIN}"


def slug_to_env(slug: str) -> str:
    return validate_slug(slug).upper().replace("-", "_") + TOKEN_SUFFIX


def check_origin_binding(doc: dict, label: str) -> str:
    """A saved file must carry a slug and the origin derived from that slug."""
    slug = doc.get("store_slug")
    origin = doc.get("store_origin")
    expected = slug_to_origin(slug) if isinstance(slug, str) else None
    if expected is None or origin != expected:
        raise CampaignAdminError(
            f"{label}: store_origin {origin!r} does not match the origin derived "
            f"from store_slug {slug!r} ({expected!r}); refusing to use it"
        )
    return origin


def _dotenv_value(path: Path, name: str):
    """The value of the one `name=` line in a dotenv file, or None when there is no
    such line. The file is read as text; every other line is ignored and nothing
    in it is executed."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line.startswith(name + "="):
            continue
        value = line[len(name) + 1:].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        return value
    return None


def load_token(slug: str, env: dict | None = None, dotenv: Path | None = None) -> str:
    """Admin token for this store. Order: {STORE}_NEXT_ADMIN_API_TOKEN in the
    environment, then that one line of .env in the current directory, then
    NEXT_ADMIN_API_TOKEN. Store-specific first, so a generic token exported for
    one store is never preferred over the named store's own credential."""
    env = os.environ if env is None else env
    name = slug_to_env(slug)
    dotenv = Path.cwd() / DOTENV_NAME if dotenv is None else Path(dotenv)
    value, source = env.get(name), name
    if not value and dotenv.is_file():
        found = _dotenv_value(dotenv, name)
        if found is not None:
            if not found:
                raise CampaignAdminError(f"{name} in {dotenv} is empty; paste the token after '=' and save the file")
            value, source = found, f"{name} in {dotenv}"
    if not value:
        value, source = env.get(GENERIC_TOKEN_ENV), GENERIC_TOKEN_ENV
    if not value:
        raise CampaignAdminError(
            f"credential missing: set {name} in the environment, add a {name}=<token> line to "
            f"{dotenv}, or set {GENERIC_TOKEN_ENV}. Never pass the token on the command line "
            "or paste it into chat."
        )
    if value.startswith("<"):
        raise CampaignAdminError(f"{source} still holds a placeholder; paste the real token in its place")
    return value


# --------------------------------------------------------------------------- #
# HTTP client
# --------------------------------------------------------------------------- #

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl} refused", headers, fp)


def _urllib_transport(req: urllib.request.Request, timeout: int):
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read().decode(errors="replace")


class Client:
    """Same-origin, paced, redirect-refusing JSON client.

    `transport(req, timeout) -> (status, headers, text)` and `clock`/`sleep`
    are injectable so tests run with no network and no real waiting.
    """

    def __init__(self, origin: str, token: str | None, *, auth_scheme: str = "bearer",
                 send_version_header: bool = True, transport=_urllib_transport,
                 clock=time.monotonic, sleep=time.sleep):
        self.origin = origin.rstrip("/")
        self.token = token
        self.auth_scheme = auth_scheme
        self.send_version_header = send_version_header
        self._transport = transport
        self._clock = clock
        self._sleep = sleep
        self._last_done = None
        self.calls = 0

    # -- url handling ------------------------------------------------------ #
    def _absolute(self, path_or_url: str) -> str:
        if path_or_url.startswith(("http://", "https://")):
            url = path_or_url
        else:
            url = self.origin + ("" if path_or_url.startswith("/") else "/") + path_or_url
        p = urllib.parse.urlsplit(url)
        if p.scheme != "https" or p.username or p.password or p.port:
            raise CampaignAdminError(f"refusing non-https or credentialed URL: {url}")
        if f"{p.scheme}://{p.netloc}" != self.origin:
            raise CampaignAdminError(
                f"refusing cross-origin request: {url} is not on {self.origin}"
            )
        return url

    def _headers(self, body, send_version_header=None) -> dict:
        h = {"Accept": "application/json"}
        if body is not None:
            h["Content-Type"] = "application/json"
        if self.token:
            h["Authorization"] = f"Bearer {self.token}" if self.auth_scheme == "bearer" else self.token
        version = self.send_version_header if send_version_header is None else send_version_header
        if version:
            h["X-29next-API-Version"] = API_VERSION
        return h

    def _pace(self):
        if self._last_done is not None:
            wait = MIN_INTERVAL - (self._clock() - self._last_done)
            if wait > 0:
                self._sleep(wait)

    # -- public ------------------------------------------------------------ #
    def request(self, method: str, path_or_url: str, body=None, *, send_version_header=None):
        """Return (status, parsed_json_or_text). Never raises on HTTP status.
        `send_version_header` overrides the client default for this one call."""
        url = self._absolute(path_or_url)
        attempts = MAX_GET_RETRIES + 1 if method == "GET" else 1
        for attempt in range(attempts):
            self._pace()
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(url, data=data, method=method,
                                         headers=self._headers(body, send_version_header))
            try:
                status, headers, text = self._transport(req, TIMEOUT)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                status, headers, text = None, {}, f"ERR: {type(e).__name__}: {getattr(e, 'reason', e)}"
            finally:
                self._last_done = self._clock()
                self.calls += 1
            if method == "GET" and (status is None or status in TRANSIENT_STATUSES) and attempt < attempts - 1:
                # Retry transient failures: network errors (status None) and the
                # documented transient HTTP statuses. Retry-After is clamped to a
                # finite, non-negative range so a hostile or malformed value
                # (negative, NaN, inf, or an RFC 7231 HTTP-date) never reaches
                # time.sleep(); an unparseable value falls back to 1s.
                retry_after = headers.get("Retry-After") or headers.get("retry-after")
                delay = 1.0
                if retry_after:
                    try:
                        parsed = float(retry_after)
                        if math.isfinite(parsed):
                            delay = min(max(parsed, 0.0), MAX_RETRY_AFTER)
                    except (ValueError, TypeError):
                        delay = 1.0
                self._sleep(delay)
                continue
            if status is not None and 300 <= status < 400:
                raise CampaignAdminError(f"{method} {url} answered {status}; redirects are refused")
            try:
                return status, json.loads(text) if text else None
            except json.JSONDecodeError:
                return status, text
        return status, text  # pragma: no cover

    def get_ok(self, path: str):
        status, body = self.request("GET", path)
        if status in (401, 403):
            raise HttpStatusError(
                f"GET {path} returned {status}: the token was rejected by {self.origin}. Check the API "
                "key was created on this store (Dashboard > Settings > API Access) with "
                f"{CAMPAIGN_SCOPES}, {METADATA_SCOPES}.", status, path)
        if status != 200:
            raise HttpStatusError(f"GET {path} returned {status}: {_short(body)}", status, path)
        return body

    def paginate(self, path: str) -> list:
        """Follow DRF `next` links (same origin only). Handles list or envelope."""
        out, url, seen = [], path, set()
        for _ in range(MAX_PAGES):
            if url in seen:
                raise CampaignAdminError(f"pagination loop: server repeated next URL {url}")
            seen.add(url)
            body = self.get_ok(url)
            if isinstance(body, dict):
                out.extend(body.get("results") or body.get("data") or [])
                url = body.get("next")
            elif isinstance(body, list):
                out.extend(body)
                url = None
            else:
                url = None
            if not url:
                return out
        raise CampaignAdminError(f"pagination exceeded {MAX_PAGES} pages for {path}")


def _short(body, n: int = 300) -> str:
    s = body if isinstance(body, str) else json.dumps(body)
    return s if len(s) <= n else s[:n] + "..."


# --------------------------------------------------------------------------- #
# Files
# --------------------------------------------------------------------------- #

def utcnow() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_instant(x):
    """Parse an ISO-8601 timestamp to an aware datetime. A value with no offset is
    treated as UTC, so a naive/aware comparison never raises TypeError."""
    if not x:
        return None
    try:
        dt = datetime.fromisoformat(str(x).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _at_or_after(a, b) -> bool:
    """True if instant a is the same as or later than instant b (timezone-aware)."""
    da, db = _parse_instant(a), _parse_instant(b)
    return da is not None and db is not None and da >= db


def same_instant(a, b) -> bool:
    """Compare two ISO-8601 timestamps as instants. The API echoes the same moment
    in different timezone offsets (create response in +02:00, retrieve in -07:00),
    so a string compare is wrong; parse both and compare the moment."""
    if a == b:
        return True
    da, db = _parse_instant(a), _parse_instant(b)
    return da is not None and db is not None and da == db


def sha256_file(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_write_json(path: Path, data, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):  # not on Windows; mode 600 is not enforced there
            os.fchmod(fd, mode)  # restrict before any secret bytes are written
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _git_ignored(path: Path, repo_root: Path):
    """True if git says the path is ignored, False if git says it is not, None if
    git could not answer. check-ignore exits 0 (ignored), 1 (not ignored) or 128
    (fatal: not a repository, dubious ownership...); the last is never a verdict
    for the path guarding the campaign api_key."""
    try:
        r = subprocess.run(["git", "-C", str(repo_root), "check-ignore", "-q", str(path)],
                           capture_output=True)
    except OSError:
        return None
    if r.returncode == 0:
        return True
    if r.returncode == 1:
        return False
    return None


def _nearest_existing(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return p.parent if p.is_file() else p


def _repo_marker(physical: Path):
    """The nearest directory at or above `physical` holding a .git entry (the
    directory, or the file a worktree or submodule uses), or None. Filesystem only,
    so the answer does not depend on git being installed."""
    p = _nearest_existing(physical)
    for d in (p, *p.parents):
        if (d / ".git").exists():
            return d
    return None


def _git_toplevel(physical: Path):
    """The root of the repository containing `physical`, or None when no repository
    does. When a .git entry is present but git cannot confirm the root, refuse: the
    caller is about to write a file that can hold the campaign api_key."""
    marker = _repo_marker(physical)
    if marker is None:
        return None
    try:
        r = subprocess.run(["git", "-C", str(_nearest_existing(physical)), "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True)
    except OSError:
        r = None
    if r is None or r.returncode != 0 or not r.stdout.strip():
        raise CampaignAdminError(
            f"cannot confirm {physical} is safe to write: a .git entry was found at {marker} but git "
            "could not be asked; refusing to write run files that can hold the campaign api_key. "
            "Install git, or pass --out <dir outside any repository>."
        )
    return Path(r.stdout.strip()).resolve()


def resolve_out_dir(out_arg, default, manifest_name: str = MANIFEST_NAME) -> Path:
    """Where a run's files go: --out when given, else `default`. When the physical
    location is inside a git repository, the manifest filename there must be
    gitignored (asked about the concrete file that will hold the api_key) and no
    in-repo path component may be a symlink. Outside any repository it is allowed."""
    out = Path(out_arg).expanduser() if out_arg else Path(default)
    if not out.is_absolute():
        out = Path.cwd() / out
    physical = out.resolve()
    top = _git_toplevel(physical)
    if top is not None:
        # A symlink among the components under the repo could make the gitignore
        # verdict apply to a different location than where writes land. Only the
        # in-repo components matter here; system-dir symlinks (e.g. /var) do not.
        cur = out
        while True:
            try:
                in_repo = cur.resolve().is_relative_to(top)  # py3.9+
            except (AttributeError, OSError):  # pragma: no cover
                in_repo = str(cur.resolve()).startswith(str(top))
            if not in_repo:
                break
            if cur.is_symlink():
                raise CampaignAdminError(f"output path {out} traverses a symlink ({cur}); refusing for secret safety")
            if cur.parent == cur:
                break
            cur = cur.parent
        verdict = _git_ignored(physical / manifest_name, top)
        if verdict is None:
            raise CampaignAdminError(
                f"cannot confirm {out} is gitignored (git could not be asked); "
                "refusing to write run files that can hold the campaign api_key"
            )
        if not verdict:
            raise CampaignAdminError(
                f"output directory {out} is inside the git repository at {top} but not gitignored; "
                f'add "{RUNS_DIR_NAME}/" to {top}/.gitignore or pass --out <dir outside the repository> '
                "(run files can hold the campaign api_key)"
            )
    out.mkdir(parents=True, exist_ok=True)
    return out


def run_dir_for(input_path) -> Path:
    """Default run directory for a subcommand that reads a run file: that file's own
    directory, so a run's discovery, plan, manifest and report stay together."""
    p = Path(input_path).expanduser()
    return (p if p.is_absolute() else Path.cwd() / p).parent


def guard_manifest_path(manifest_arg) -> Path:
    """Checks for a manifest the operator names, before anything writes to it: it
    must not be a symlink, and its directory passes the run-directory gitignore
    guard, asked about this exact filename."""
    p = Path(manifest_arg).expanduser()
    if p.is_symlink():
        raise CampaignAdminError(f"manifest {p} is a symlink; refusing to write through it")
    resolve_out_dir(None, run_dir_for(p), manifest_name=p.name)
    return p


def _same_file(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def load_json(path) -> dict:
    return load_json_and_hash(path)[0]


def load_json_and_hash(path):
    """Read the file once; return (parsed, sha256). Hashing and parsing the same
    bytes closes the gap where a concurrent save could make the shown plan and the
    approval hash disagree."""
    p = Path(path)
    if not p.exists():
        raise CampaignAdminError(f"file not found: {p}")
    raw = p.read_bytes()
    try:
        return json.loads(raw), hashlib.sha256(raw).hexdigest()
    except json.JSONDecodeError as e:
        raise CampaignAdminError(f"{p} is not valid JSON: {e}")


# --------------------------------------------------------------------------- #
# Campaign metadata definitions (references/offer-doctrine.md, Metadata definitions)
# --------------------------------------------------------------------------- #

MetadataField = namedtuple("MetadataField", "name object key type max_length enable_filter")

# Order fields are stamped onto each order by the Campaigns API -> Admin API flow;
# attribution fields are attached by the Campaign Cart SDK.
METADATA_FIELDS = [
    #             name                    object         key                     type       max_length  enable_filter
    MetadataField("Campaign ID",          "order",       "nc_campaign_id",       "text",    255,  True),
    MetadataField("Campaign Name",        "order",       "nc_campaign_name",     "text",    255,  True),
    MetadataField("Conversion Timestamp", "attribution", "conversion_timestamp", "integer", None, False),
    MetadataField("Device",               "attribution", "device",               "text",    500,  False),
    MetadataField("Device Type",          "attribution", "device_type",          "text",    50,   True),
    MetadataField("Domain",               "attribution", "domain",               "text",    500,  False),
    MetadataField("Landing Page",         "attribution", "landing_page",         "text",    1000, False),
    MetadataField("Referrer",             "attribution", "referrer",             "text",    2000, False),
    MetadataField("SDK Version",          "attribution", "sdk_version",          "text",    50,   False),
    MetadataField("Timestamp",            "attribution", "timestamp",            "integer", None, False),
    MetadataField("User IP",              "attribution", "user_ip",              "text",    100,  False),
]
REQUIRED_METADATA_KEYS = [f.key for f in METADATA_FIELDS]
METADATA_PATH = "/api/admin/metadata/"
METADATA_ERROR_REASONS = {
    401: "token rejected or missing the metadata:read permission",
    403: "token rejected or missing the metadata:read permission",
    404: "metadata endpoint not available on this store",
    405: "metadata endpoint not available on this store",
}


def metadata_payload(f: MetadataField) -> dict:
    body = {"name": f.name, "object": f.object, "key": f.key, "type": f.type,
            "enable_export": True, "enable_filter": f.enable_filter}
    if f.max_length is not None:
        body["validations"] = [{"type": "maximum_length", "value": f.max_length}]
    return body


def metadata_audit(client: Client) -> dict:
    """Compare the store's metadata definitions with METADATA_FIELDS.

    Keyed by `key` alone because a metadata key is unique per store, not per object:
    a key defined under another object is a conflict, never "missing" (a POST for it
    would only answer 400). Raises HttpStatusError when the list cannot be read."""
    present = {}
    for d in client.paginate(METADATA_PATH):
        if isinstance(d, dict) and d.get("key"):
            present[d["key"]] = d.get("object")
    missing, conflicts = [], []
    for f in METADATA_FIELDS:
        if f.key not in present:
            missing.append(f)
        elif present[f.key] and present[f.key] != f.object:
            conflicts.append({"key": f.key, "defined_on": present[f.key], "needs": f.object})
    return {"present": present, "missing": missing, "conflicts": conflicts}


def metadata_error_for(e: Exception) -> dict:
    """A persistable description of a failed metadata audit: the HTTP status and a
    fixed reason. Never the response body or the exception text, which can echo
    whatever the server sent."""
    if isinstance(e, HttpStatusError):
        if e.status is None:
            return {"status": None, "reason": "network error"}
        return {"status": e.status, "reason": METADATA_ERROR_REASONS.get(e.status, f"HTTP {e.status}")}
    return {"status": None, "reason": "metadata listing stopped by a client safety check"}


def metadata_provision(client: Client, slug: str, apply_changes: bool) -> int:
    """Print the audit; with apply_changes, POST each missing definition. Never edits
    an existing definition. Returns 1 if a POST failed or a conflict remains."""
    audit = metadata_audit(client)
    present, missing, conflicts = audit["present"], audit["missing"], audit["conflicts"]
    missing_keys = {f.key for f in missing}
    conflict_keys = {c["key"] for c in conflicts}
    print(f"Store: {client.origin}")
    print(f"Existing definitions: {len(present)}")
    for f in METADATA_FIELDS:
        label = "MISSING" if f.key in missing_keys else (
            f"on {present.get(f.key)}" if f.key in conflict_keys else "exists")
        print(f"  [{label:<16}] {f.object:<11} {f.key}")
    if conflicts:
        print("Conflicts (fix these in Settings > Metadata; this tool never edits an existing definition):")
        for c in conflicts:
            print(f"  {c['key']}: defined on {c['defined_on']}, needs {c['needs']}")
    if not missing:
        if not conflicts:
            print(f"All {len(METADATA_FIELDS)} definitions present. Nothing to do.")
        return 1 if conflicts else 0
    if not apply_changes:
        print(f"(dry run: {len(missing)} missing; re-run with --apply to create them; the key needs metadata:write)")
        return 1 if conflicts else 0
    print(f"Creating {len(missing)} definitions (POST without the version header)...")
    created, failed = 0, 0
    for f in missing:
        # The version header must be absent on this POST: a definition created with it
        # answers 201 but is invisible in the dashboard and the versioned GET.
        status, resp = client.request("POST", METADATA_PATH, metadata_payload(f), send_version_header=False)
        if status in (200, 201):
            created += 1
            print(f"  [created] {f.object:<11} {f.key}")
        else:
            failed += 1
            print(f"  [FAILED {status}] {f.object:<11} {f.key}: {_short(resp)}")
    print(f"Done: {created} created, {failed} failed.")
    if failed or conflicts:
        return 1
    print(f"Saved discovery files are now stale. Re-run: {PROG} discover --store {slug}")
    return 0


# --------------------------------------------------------------------------- #
# discover
# --------------------------------------------------------------------------- #

def discover(client: Client, slug: str) -> dict:
    store = client.get_ok("/api/admin/store/")
    gateway_groups = client.paginate("/api/admin/gateway-groups/")
    shipping_methods = client.paginate("/api/admin/shipping-methods/")
    products = client.paginate("/api/admin/products/")
    campaigns = client.paginate("/api/admin/campaigns/?page_size=100")

    offers_supported = "unknown"
    if campaigns:
        status, _ = client.request("GET", f"/api/admin/campaigns/{campaigns[0]['id']}/offers/")
        offers_supported = True if status == 200 else (False if status in (404, 405) else "unknown")

    try:
        audit = metadata_audit(client)
        metadata_missing = [f.key for f in audit["missing"]]
        metadata_conflicts = audit["conflicts"]
        metadata_checked, metadata_error = True, None
    except CampaignAdminError as e:
        metadata_missing, metadata_conflicts, metadata_checked = [], [], False
        metadata_error = metadata_error_for(e)

    return {
        "store_slug": slug,
        "store_origin": client.origin,
        "discovered_at": utcnow(),
        "store": {
            "name": store.get("name"),
            "available_currencies": [c["code"] for c in store.get("available_currencies", [])],
            "available_languages": [l["code"] for l in store.get("available_languages", [])],
        },
        "gateway_groups": [
            {
                "id": g["id"], "name": g.get("name"),
                "currencies": [c["code"] for c in g.get("available_currencies", [])],
                "payment_methods": [m["code"] for m in g.get("available_payment_methods", [])],
                "express_payment_methods": [m["code"] for m in g.get("available_express_payment_methods", [])],
            } for g in gateway_groups
        ],
        "shipping_methods": [
            {"code": s.get("code"), "name": s.get("name"),
             "prices": s.get("prices", []), "countries": [c.get("code") for c in s.get("countries", [])]}
            for s in shipping_methods
        ],
        "products": [
            {
                "id": p["id"], "title": p.get("title"), "structure": p.get("structure"),
                "enable_subscription": p.get("enable_subscription", False),
                "variants": [
                    {"id": v["id"], "title": v.get("title"), "sku": v.get("sku"),
                     "purchase_availability": v.get("purchase_availability"),
                     "prices": v.get("prices", [])}
                    for v in p.get("variants", [])
                ],
            } for p in products
        ],
        "campaigns": [
            {"id": c["id"], "name": c.get("name"), "currency": c.get("currency"),
             "language": c.get("language"), "created_at": c.get("created_at")}
            for c in campaigns
        ],
        "offers_supported": offers_supported,
        "metadata_checked": metadata_checked,
        "metadata_missing": metadata_missing,
        "metadata_conflicts": metadata_conflicts,
        "metadata_error": metadata_error,
    }


def print_discovery(d: dict) -> None:
    s = d["store"]
    print(f"Store: {s['name']}  ({d['store_origin']})")
    print(f"  currencies: {', '.join(s['available_currencies'])}   languages: {', '.join(s['available_languages'])}")
    print("Gateway groups:")
    for g in d["gateway_groups"]:
        print(f"  [{g['id']}] {g['name']}: currencies={g['currencies']} methods={g['payment_methods']} express={g['express_payment_methods']}")
    print("Shipping methods:")
    for m in d["shipping_methods"]:
        print(f"  {m['code']}: {m['name']} prices={m['prices']}")
    print("Products:")
    for p in d["products"]:
        print(f"  [{p['id']}] {p['title']}")
        for v in p["variants"]:
            price = next((x.get("price") for x in v["prices"]), None)
            print(f"      variant {v['id']}  {v['sku']}  {v['title']}  price={price}  {v['purchase_availability']}")
    print(f"Existing campaigns: {len(d['campaigns'])}")
    for c in d["campaigns"]:
        print(f"  [{c['id']}] {c['name']} {c['currency']}/{c['language']}")
    print(f"Offers API supported: {d['offers_supported']}")
    if d["metadata_checked"]:
        missing = d.get("metadata_missing") or []
        conflicts = d.get("metadata_conflicts") or []
        if not missing and not conflicts:
            print("Campaign metadata definitions: all present")
        if missing:
            print(f"Campaign metadata definitions: MISSING {missing}; "
                  f"run {PROG} metadata --store {d['store_slug']} --apply")
        for c in conflicts:
            print(f"Campaign metadata conflict: {c['key']} is defined on {c['defined_on']}, "
                  f"needs {c['needs']}; fix it in Settings > Metadata")
    else:
        err = d.get("metadata_error") or {}
        print(f"Campaign metadata definitions: not checked "
              f"(status {err.get('status')}: {err.get('reason', 'the metadata read failed')})")


# --------------------------------------------------------------------------- #
# recommend
# --------------------------------------------------------------------------- #

def D(x) -> Decimal:
    try:
        return Decimal(str(x))
    except InvalidOperation:
        raise CampaignAdminError(f"not a decimal: {x!r}")


def money(x: Decimal) -> str:
    return str(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def landed_unit(anchor: Decimal, pct: Decimal, rounding: str | None) -> Decimal:
    """Engine behaviour (offer doctrine: Rounding and stacking): the per-unit discount is
    rounded to cents, then subtracted. A price_rounding value pins the cents of
    the result; that step is engine-defined and confirmed by `verify` against
    carts/calculate, not assumed."""
    discount = (anchor * pct / Decimal(100)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    unit = anchor - discount
    if rounding:
        cents = Decimal(rounding)
        unit = unit.to_integral_value(rounding="ROUND_FLOOR") + cents
    return unit


def short_name(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def code_from(title: str, pct) -> str:
    stem = re.sub(r"[^A-Z0-9]", "", title.upper())[:40] or "OFFER"
    return f"{stem}{int(pct)}"


def _parse_kv(spec: str, parts: int, label: str) -> list:
    bits = spec.split(":")
    if len(bits) != parts:
        raise CampaignAdminError(f"--{label} expects {parts} colon-separated values, got {spec!r}")
    return bits


def _resolve_gateway_group(discovery: dict, currency: str, requested):
    """Auto-select only when exactly one group lists the currency; otherwise the
    operator must name one. Never guesses between candidates."""
    eligible = [g for g in discovery["gateway_groups"] if currency in g["currencies"]]
    if requested is not None:
        group = next((g for g in discovery["gateway_groups"] if g["id"] == requested), None)
        if group is None:
            raise CampaignAdminError(f"gateway group {requested} not found on the store")
        if currency not in group["currencies"]:
            raise CampaignAdminError(f"gateway group {group['id']} ({group['name']}) does not list {currency}")
        return group
    if len(eligible) == 1:
        return eligible[0]
    if not eligible:
        raise CampaignAdminError(f"no gateway group on the store lists {currency}")
    opts = ", ".join(f"[{g['id']}] {g['name']}" for g in eligible)
    raise CampaignAdminError(f"{len(eligible)} gateway groups list {currency}; pass --gateway-group: {opts}")


def recommend(discovery: dict, a: argparse.Namespace) -> dict:
    check_origin_binding(discovery, "discovery")
    store = discovery["store"]
    currency = (a.currency or (store["available_currencies"][0] if store["available_currencies"] else "USD")).upper()
    language = (a.language or (store["available_languages"][0] if store["available_languages"] else "en")).lower()
    if currency not in store["available_currencies"]:
        raise CampaignAdminError(f"currency {currency} is not enabled on the store ({store['available_currencies']})")
    if language not in store["available_languages"]:
        raise CampaignAdminError(f"language {language} is not enabled on the store ({store['available_languages']})")
    if a.ctc not in ("low", "high"):
        raise CampaignAdminError("--ctc must be low or high (operator judgement, never inferred)")
    if not DECIMAL_RE.match(str(a.anchor_price)):
        raise CampaignAdminError(f"--anchor-price {a.anchor_price!r} must be a positive decimal with at most 2 places")
    anchor = D(a.anchor_price)
    if anchor <= 0:
        raise CampaignAdminError(f"--anchor-price must be greater than 0; got {a.anchor_price}")

    group = _resolve_gateway_group(discovery, currency, a.gateway_group)

    methods = [m.strip() for m in a.payment_methods.split(",")] if a.payment_methods else list(group["payment_methods"])
    express = [m.strip() for m in a.express_methods.split(",")] if a.express_methods else list(group["express_payment_methods"])
    bad = [m for m in methods if m not in group["payment_methods"]]
    if bad:
        raise CampaignAdminError(f"payment methods {bad} are not offered by gateway group {group['id']} ({group['payment_methods']})")
    bad = [m for m in express if m not in group["express_payment_methods"]]
    if bad:
        raise CampaignAdminError(f"express methods {bad} are not offered by gateway group {group['id']} ({group['express_payment_methods']})")

    # hero product
    hero = next((p for p in discovery["products"] if p["id"] == a.hero), None)
    if hero is None:
        raise CampaignAdminError(f"hero product {a.hero} not found in discovery")
    variants = [v for v in hero["variants"] if v.get("purchase_availability", "available") == "available"]
    if not variants:
        raise CampaignAdminError(f"hero product {a.hero} has no purchasable variants")
    hero_title = short_name(hero["title"])
    # The API appends " - {variant}" to the package name for variant packages,
    # so the plan sends the bare product name and records the variant separately.
    packages, keys_by_variant = [], {}
    for v in variants:
        key = f"hero-{v['id']}"
        keys_by_variant[v["id"]] = key
        packages.append({
            "key": key, "role": "hero", "name": hero_title, "variant_title": v.get("title"),
            "product_id": hero["id"], "product_variant_ids": [v["id"]], "price": money(anchor),
        })
    hero_keys = [p["key"] for p in packages]

    # shipping (required, explicit)
    if not a.shipping:
        raise CampaignAdminError("at least one --shipping <code>:<price> is required")
    codes = {m["code"] for m in discovery["shipping_methods"]}
    shipping = []
    for spec in a.shipping:
        code, price = _parse_kv(spec, 2, "shipping")
        if code not in codes:
            raise CampaignAdminError(f"shipping code {code!r} is not configured on the store ({sorted(codes)})")
        if not DECIMAL_RE.match(price):
            raise CampaignAdminError(f"shipping price {price!r} is not a decimal")
        shipping.append({"shipping_method": code, "price": money(D(price))})

    # bumps / upsells: explicit variant, price, pct; nothing inferred
    variant_index = {v["id"]: (p, v) for p in discovery["products"] for v in p["variants"]}

    def add_extra(spec: str, role: str):
        parts = _parse_kv(spec, 2 if role == "bump" else 3, role)
        vid = int(parts[0])
        if vid not in variant_index:
            raise CampaignAdminError(f"{role} variant {vid} not found in discovery")
        if not DECIMAL_RE.match(parts[1]):
            raise CampaignAdminError(f"{role} price {parts[1]!r} is not a decimal")
        p, v = variant_index[vid]
        if v.get("purchase_availability", "available") != "available":
            raise CampaignAdminError(f"{role} variant {vid} is not purchasable "
                                     f"(purchase_availability={v.get('purchase_availability')!r}); pick an available variant")
        title = short_name(p["title"])
        pkg = {"key": f"{role}-{vid}", "role": role, "name": title, "variant_title": v.get("title"),
               "product_id": p["id"], "product_variant_ids": [vid], "price": money(D(parts[1]))}
        packages.append(pkg)
        return pkg, (D(parts[2]) if role == "upsell" else None), title

    offers, landed, rationale, handoff, blockers = [], [], [], [], []
    rounding = a.rounding if a.rounding else None
    if rounding not in PRICE_ROUNDINGS:
        raise CampaignAdminError(f"--rounding must be one of {PRICE_ROUNDINGS[1:]}")

    offers_supported = discovery.get("offers_supported", "unknown")
    if offers_supported is False:
        handoff.append("Offers API not available on this store: create tier/voucher offers in the dashboard (Offers & Discounts).")

    def whole_pct(x, what):
        try:
            d = D(x)
        except CampaignAdminError:
            raise CampaignAdminError(f"{what} percentage must be a whole number in [1, 99]; got {x}")
        # A 100% discount yields a zero-priced unit, which this tool never creates on
        # purpose; reject it here so it fails at plan time, not at checkout.
        if d != d.to_integral_value() or not (Decimal(0) < d < Decimal(100)):
            raise CampaignAdminError(f"{what} percentage must be a whole number in [1, 99]; got {x}")
        return int(d)

    tiers = [whole_pct(t, "tier") for t in (a.tiers.split(",") if a.tiers else DEFAULT_TIERS)]
    if a.ctc == "low":
        rationale.append("Low CTC: one package per variant at the anchor price; Buy 1/2/3 automatic tier offers "
                         "(offer doctrine: Cost-to-consumer). Tiers are offers, never quantity packages "
                         "(offer doctrine: Naming and scoping).")
        for qty, pct in enumerate(tiers, start=1):
            unit = landed_unit(anchor, D(pct), rounding)
            if unit <= 0:
                raise CampaignAdminError(f"Buy {qty} tier at {pct}% yields a non-positive unit price ({unit}); "
                                         "lower the discount or raise the anchor")
            landed.append({"tier": f"Buy {qty}", "kind": "tier", "qty": qty,
                           "offer_key": f"tier-{qty}" if offers_supported is not False else None,
                           "package_keys": list(hero_keys),
                           "anchor": money(anchor), "pct": pct,
                           "unit_after": money(unit), "order_total": money(unit * qty)})
            if offers_supported is not False:
                offers.append({
                    "key": f"tier-{qty}", "name": f"{hero_title} - Buy {qty} - {pct}%",
                    "offer_type": "offer", "code": None,
                    "condition": {"type": "count", "value": qty, "package_keys": list(hero_keys)},
                    "benefit": {"type": "package_percentage", "value": money(D(pct)), "price_rounding": rounding},
                })
    else:
        rationale.append("High CTC: single-unit packages, no quantity tiers; AOV comes from low-friction bumps "
                         "and post-purchase upsells (offer doctrine: Cost-to-consumer).")
        landed.append({"tier": "Buy 1", "kind": "single", "qty": 1, "offer_key": None,
                       "package_keys": list(hero_keys),
                       "anchor": money(anchor), "pct": 0,
                       "unit_after": money(anchor), "order_total": money(anchor)})

    for spec in a.bump or []:
        pkg, _, _ = add_extra(spec, "bump")
        rationale.append(f"Checkout bump {pkg['name']} at {pkg['price']} (operator-specified).")
    for spec in a.upsell or []:
        pkg, pct, title = add_extra(spec, "upsell")
        pct = Decimal(whole_pct(pct, "upsell"))
        unit = landed_unit(D(pkg["price"]), pct, rounding)
        landed.append({"tier": f"Upsell {pkg['name']}", "kind": "upsell", "qty": 1,
                       "offer_key": f"upsell-{pkg['key']}" if offers_supported is not False else None,
                       "package_keys": [pkg["key"]],
                       "anchor": pkg["price"], "pct": int(pct),
                       "unit_after": money(unit), "order_total": money(unit)})
        if offers_supported is not False:
            offers.append({
                "key": f"upsell-{pkg['key']}", "name": f"{pkg['name']} - {int(pct)}%",
                "offer_type": "voucher", "code": code_from(title, pct),
                "condition": {"type": "any", "value": None, "package_keys": [pkg["key"]]},
                "benefit": {"type": "package_percentage", "value": money(pct), "price_rounding": rounding},
            })
        rationale.append(f"Upsell {pkg['name']} uses a voucher (site offers do not apply post-purchase).")

    def _is_zero(x):
        try:
            return D(x) == 0
        except CampaignAdminError:
            return False
    exit_pct = DEFAULT_EXIT_PCT if a.exit is None else (0 if _is_zero(a.exit) else whole_pct(a.exit, "exit"))
    if exit_pct and offers_supported is not False:
        offers.append({
            "key": "exit-pop", "name": f"{hero_title} - Exit - {exit_pct}%",
            "offer_type": "voucher", "code": a.exit_code or code_from(hero_title, exit_pct),
            "condition": {"type": "any", "value": None, "package_keys": list(hero_keys)},
            "benefit": {"type": "package_percentage", "value": money(D(exit_pct)), "price_rounding": rounding},
        })
        rationale.append(f"Exit-pop voucher for an additional {exit_pct}% applies on top of the tier price "
                         "(offer doctrine: Rounding and stacking).")
    if a.free_shipping and offers_supported is not False:
        offers.append({
            "key": "free-shipping", "name": f"{hero_title} - Free Shipping",
            "offer_type": "offer", "code": None,
            "condition": {"type": "any", "value": None, "package_keys": list(hero_keys)},
            "benefit": {"type": "shipping_percentage", "value": "100.00", "price_rounding": None},
        })

    if not discovery.get("metadata_checked", True):
        err = discovery.get("metadata_error") or {}
        status, reason = err.get("status"), err.get("reason") or "the metadata read failed"
        hint = (f" The API key needs {METADATA_SCOPES}; create a key that has them, then re-run discover."
                if status in (401, 403) else
                " Re-run discover and confirm the 11 definitions exist before apply.")
        blockers.append(f"Campaign metadata definitions could not be audited (status {status}: {reason}).{hint}")
    else:
        if discovery.get("metadata_missing"):
            blockers.append("Campaign metadata definitions missing on the store: "
                            f"{discovery['metadata_missing']}. Run \"{PROG} metadata --store "
                            f"{discovery['store_slug']} --apply\" (the key needs metadata:write), "
                            "then re-run discover, then recommend again.")
        for c in discovery.get("metadata_conflicts") or []:
            blockers.append(f"Metadata key {c['key']} is defined on {c['defined_on']} but campaigns need it on "
                            f"{c['needs']}; correct it in Settings > Metadata, then re-run discover. "
                            "This tool never edits an existing definition.")
    if a.name and any(c.get("name") == a.name for c in discovery.get("campaigns", [])):
        blockers.append(f"A campaign named {a.name!r} already exists on the store; choose another name.")

    handoff += [
        "Allowed Domains (Development/Production) are set in the dashboard: Campaign > Settings.",
        "PayPal account (if any) is linked in the dashboard; the API takes paypal_account_id only.",
        "Build the funnel with the campaign api_key from the run manifest and the package ids below.",
    ]
    if a.countries:
        countries = [c.strip().upper() for c in a.countries.split(",")]
    else:
        chosen = {sm["shipping_method"] for sm in shipping}
        countries = sorted({c for m in discovery["shipping_methods"] if m["code"] in chosen
                            for c in (m.get("countries") or []) if c})
    return {
        "store_slug": discovery["store_slug"],
        "store_origin": discovery["store_origin"],
        "generated_at": utcnow(),
        "ctc": a.ctc,
        "campaign": {
            "name": a.name or hero_title,
            "currency": currency, "language": language,
            "payment_gateway_group_id": group["id"],
            "additional_currencies": [],
            "available_payment_methods": methods,
            "available_express_payment_methods": express,
            "available_shipping_countries": countries,
            "statement_descriptor": a.statement_descriptor or None,
        },
        "packages": packages,
        "shipping_methods": shipping,
        "offers": offers,
        "landed_prices": landed,
        "rationale": rationale,
        "blockers": blockers,
        "waivers": [],
        "handoff": handoff,
    }


# --------------------------------------------------------------------------- #
# plan validation + request rendering
# --------------------------------------------------------------------------- #

IMAGE_FIELDS = ("src", "file_name")
MAX_IMAGE_SRC = 2048
MAX_IMAGE_FILE_NAME = 100


def _image_errors(key, img) -> list:
    """Rules for a package's optional image override.

    The plan carries a URL, never base64: the manifest that records what a run did
    is mode 600 and holds the campaign api_key, and it has to stay readable by a
    human. A rejected image is terminal (there is no in-place repair once the plan
    hash is fixed), so anything cheap to catch locally is caught here rather than
    spent on a server round trip."""
    errs = []
    if not isinstance(img, dict):
        return [f"package {key}: image must be an object with an https src (got {img!r})"]
    if "attachment" in img:
        errs.append(f"package {key}: image.attachment is not supported; plans carry an https src only")
    extra = sorted(k for k in img if k not in IMAGE_FIELDS and k != "attachment")
    if extra:
        errs.append(f"package {key}: image has unsupported field(s) {extra}; only src and file_name are allowed")

    src = img.get("src")
    if not isinstance(src, str) or not src:
        errs.append(f"package {key}: image.src is required and must be a string")
    else:
        if len(src) > MAX_IMAGE_SRC:
            errs.append(f"package {key}: image.src exceeds {MAX_IMAGE_SRC} characters")
        if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in src):
            errs.append(f"package {key}: image.src contains whitespace or control characters")
        else:
            try:
                # Both accessors parse the authority, and either can raise: `hostname` on a
                # malformed IPv6 literal, `port` on a non-numeric or out-of-range port. The
                # parse is the check — a bad port would otherwise reach the server and come
                # back as a terminal 400 with no in-place repair.
                u = urllib.parse.urlsplit(src)
                host = u.hostname
                u.port
            except ValueError as exc:
                # Carry the real reason. "must be a plain https URL" is baffling when the
                # scheme is fine and the port is the problem.
                errs.append(f"package {key}: image.src {src!r} is not a parseable URL ({exc})")
            else:
                if u.scheme != "https" or not host or u.username or u.password:
                    # `hostname`, not `netloc`: "https://:8080/a.png" has a truthy netloc and no host.
                    errs.append(f"package {key}: image.src {src!r} must be a plain https URL "
                                "(no http, data:, relative paths or credentials)")

    if "file_name" in img:
        fn = img["file_name"]
        if not isinstance(fn, str) or not fn.strip():
            errs.append(f"package {key}: image.file_name must be a non-empty string")
        elif len(fn) > MAX_IMAGE_FILE_NAME or fn in (".", "..") or any(c in fn for c in ("/", "\\", "\0")):
            errs.append(f"package {key}: image.file_name must be a bare file name "
                        f"(no path separators, <= {MAX_IMAGE_FILE_NAME} chars)")
    return errs


def validate_plan(plan: dict) -> list:
    errs = []
    try:
        check_origin_binding(plan, "plan")
    except CampaignAdminError as e:
        errs.append(str(e))
    c = plan.get("campaign", {})
    for f in ("name", "currency", "language", "payment_gateway_group_id"):
        if c.get(f) in (None, ""):
            errs.append(f"campaign.{f} is required")
    if len(str(c.get("name", ""))) > 200:
        errs.append("campaign.name exceeds 200 characters")
    if c.get("statement_descriptor") and len(c["statement_descriptor"]) > 255:
        errs.append("campaign.statement_descriptor exceeds 255 characters")
    if type(c.get("payment_gateway_group_id")) is not int:
        errs.append("campaign.payment_gateway_group_id must be an integer")

    keys = set()
    for p in plan.get("packages", []):
        k = p.get("key")
        if not k or k in keys:
            errs.append(f"package key missing or duplicate: {k!r}")
        keys.add(k)
        vids = p.get("product_variant_ids") or []
        if len(vids) != 1 or type(vids[0]) is not int:
            errs.append(f"package {k}: product_variant_ids must be exactly one integer (got {vids!r}); "
                        "the API creates one package per variant id and only the first is journalled")
        if not p.get("name") or len(p["name"]) > 200:
            errs.append(f"package {k}: name missing or >200 chars")
        if re.match(r"^\d+\s*x\s", p.get("name", ""), re.I):
            errs.append(f"package {k}: quantity-style name {p['name']!r} is deprecated; tiers are offers")
        if type(p.get("product_id")) is not int:
            errs.append(f"package {k}: product_id must be an integer")
        if not DECIMAL_RE.match(str(p.get("price", ""))):
            errs.append(f"package {k}: price {p.get('price')!r} is not a decimal (max 8 digits, 2 places)")
        if p.get("image") is not None:
            errs.extend(_image_errors(k, p["image"]))
    if not plan.get("packages"):
        errs.append("at least one package is required")

    if not plan.get("shipping_methods"):
        errs.append("at least one shipping method is required")
    ship_codes = set()
    for s in plan.get("shipping_methods", []):
        code = s.get("shipping_method")
        if not code:
            errs.append("shipping_method code is required")
        elif code in ship_codes:
            errs.append(f"duplicate shipping method code {code!r}")
        ship_codes.add(code)
        if not DECIMAL_RE.match(str(s.get("price", ""))):
            errs.append(f"shipping {s.get('shipping_method')}: price {s.get('price')!r} is not a decimal")

    names, codes, offer_keys = set(), set(), set()
    for o in plan.get("offers", []):
        k = o.get("key", "?")
        if not o.get("key") or k in offer_keys:
            errs.append(f"offer key missing or duplicate: {k!r}")
        offer_keys.add(k)
        n = o.get("name", "")
        if not n or len(n) > 128:
            errs.append(f"offer {k}: name missing or >128 chars")
        if n in names:
            errs.append(f"offer {k}: duplicate offer name {n!r}")
        names.add(n)
        ot = o.get("offer_type", "offer")
        if ot not in OFFER_TYPES:
            errs.append(f"offer {k}: offer_type {ot!r} invalid")
        if ot == "voucher":
            code = o.get("code") or ""
            if not CODE_RE.match(code):
                errs.append(f"offer {k}: voucher needs an uppercase alphanumeric code (<=64), got {code!r}")
            if code in codes:
                errs.append(f"offer {k}: duplicate voucher code {code!r}")
            codes.add(code)
        cond = o.get("condition", {})
        if cond.get("type") not in CONDITION_TYPES:
            errs.append(f"offer {k}: condition.type {cond.get('type')!r} invalid")
        if cond.get("type") == "count" and not (isinstance(cond.get("value"), int) and cond["value"] >= 1):
            errs.append(f"offer {k}: count condition needs an integer value >= 1")
        if cond.get("all_packages"):
            errs.append(f"offer {k}: all_packages is never allowed (offer doctrine: Naming and scoping)")
        pk = cond.get("package_keys") or []
        if not pk:
            errs.append(f"offer {k}: package_keys must name at least one package")
        for x in pk:
            if x not in keys:
                errs.append(f"offer {k}: package key {x!r} does not resolve")
        ben = o.get("benefit", {})
        if ben.get("type") not in BENEFIT_TYPES:
            errs.append(f"offer {k}: benefit.type {ben.get('type')!r} invalid")
        v = str(ben.get("value", ""))
        if not DECIMAL_RE.match(v) or not (Decimal(0) < D(v) <= Decimal(100)):
            errs.append(f"offer {k}: benefit.value {v!r} must be a percentage in (0, 100]")
        if ben.get("price_rounding") not in PRICE_ROUNDINGS:
            errs.append(f"offer {k}: price_rounding {ben.get('price_rounding')!r} invalid")

    # landed_prices is what verify asserts against the live cart engine, so it is
    # schema-checked like everything else rather than parsed back out of labels.
    for i, l in enumerate(plan.get("landed_prices", [])):
        tag = f"landed_prices[{i}]"
        if l.get("kind") not in ("tier", "single", "upsell"):
            errs.append(f"{tag}: kind must be tier|single|upsell")
        if type(l.get("qty")) is not int or l["qty"] < 1:
            errs.append(f"{tag}: qty must be an integer >= 1")
        for f in ("anchor", "unit_after", "order_total"):
            if not DECIMAL_RE.match(str(l.get(f, ""))):
                errs.append(f"{tag}: {f} {l.get(f)!r} is not a decimal")
        pk = l.get("package_keys") or []
        if not pk or any(x not in keys for x in pk):
            errs.append(f"{tag}: package_keys must name existing packages (got {pk!r})")
        ok_ = l.get("offer_key")
        if ok_ is not None and ok_ not in offer_keys:
            errs.append(f"{tag}: offer_key {ok_!r} does not resolve")
        if l.get("kind") == "upsell" and ok_ is not None:
            up = next((o for o in plan.get("offers", []) if o.get("key") == ok_), None)
            if up and up.get("offer_type") != "voucher":
                errs.append(f"{tag}: upsell offer {ok_!r} must be a voucher")
    return errs


def campaign_body(plan: dict) -> dict:
    c = plan["campaign"]
    body = {"name": c["name"], "currency": c["currency"], "language": c["language"],
            "payment_gateway_group_id": c["payment_gateway_group_id"]}
    for f in ("additional_currencies", "available_payment_methods",
              "available_express_payment_methods", "available_shipping_countries"):
        if c.get(f):
            body[f] = c[f]
    if c.get("statement_descriptor"):
        body["statement_descriptor"] = c["statement_descriptor"]
    if c.get("paypal_account_id"):
        body["paypal_account_id"] = c["paypal_account_id"]
    return body


def package_body(p: dict) -> dict:
    body = {"name": p["name"], "product_id": p["product_id"], "price": p["price"]}
    if p.get("product_variant_ids"):
        body["product_variant_ids"] = p["product_variant_ids"]
    if p.get("price_recurring"):
        body.update(price_recurring=p["price_recurring"], interval=p.get("interval", "month"),
                    interval_count=p.get("interval_count", 1))
    return body


def image_body(p: dict):
    """The image PUT body for a package, or None when the plan sets no override.

    Single source for both `render_requests` and `apply`, so the request list the
    operator approves cannot drift from what actually goes on the wire."""
    img = p.get("image")
    if not img:
        return None
    body = {"src": img["src"]}
    if img.get("file_name"):
        body["file_name"] = img["file_name"]
    return body


def offer_body(o: dict, package_ids: dict) -> dict:
    missing = [k for k in o["condition"]["package_keys"] if k not in package_ids]
    if missing:
        raise CampaignAdminError(
            f"offer {o.get('key', o.get('name'))!r} references package key(s) {missing} "
            "that are not created; cannot build the offer (resume the packages first or fix the plan)")
    cond = {"type": o["condition"]["type"], "all_packages": False,
            "package_ids": [package_ids[k] for k in o["condition"]["package_keys"]]}
    if o["condition"]["type"] == "count":
        cond["value"] = o["condition"]["value"]
    body = {"name": o["name"], "offer_type": o.get("offer_type", "offer"), "condition": cond,
            "benefit": {"type": o["benefit"]["type"], "value": o["benefit"]["value"]}}
    if o["benefit"].get("price_rounding"):
        body["benefit"]["price_rounding"] = o["benefit"]["price_rounding"]
    if body["offer_type"] == "voucher":
        body["code"] = o["code"]
    return body


def render_requests(plan: dict) -> list:
    """Ordered (method, path, body) the apply step will send. Ids shown as placeholders."""
    reqs = [("POST", "/api/admin/campaigns/", campaign_body(plan))]
    for p in plan["packages"]:
        reqs.append(("POST", "/api/admin/campaigns/{campaign_id}/packages/", package_body(p)))
        img = image_body(p)
        if img is not None:
            reqs.append(("PUT", f"/api/admin/campaigns/{{campaign_id}}/packages/<{p['key']}>/image/", img))
    for s in plan["shipping_methods"]:
        reqs.append(("POST", "/api/admin/campaigns/{campaign_id}/shipping-methods/",
                     {"shipping_method": s["shipping_method"], "price": s["price"]}))
    placeholder = {p["key"]: f"<{p['key']}>" for p in plan["packages"]}
    for o in plan.get("offers", []):
        reqs.append(("POST", "/api/admin/campaigns/{campaign_id}/offers/", offer_body(o, placeholder)))
    return reqs


def print_plan(plan: dict, plan_path: Path | None) -> None:
    c = plan["campaign"]
    print(f"Campaign: {c['name']}  {c['currency']}/{c['language']}  gateway group {c['payment_gateway_group_id']}  store {plan['store_origin']}")
    print(f"  payment methods: {c['available_payment_methods']}  express: {c['available_express_payment_methods']}  countries: {c['available_shipping_countries'] or 'all'}")
    print("Packages:")
    for p in plan["packages"]:
        img_spec = p.get("image")
        img = f"  image {img_spec['src']}" if isinstance(img_spec, dict) and img_spec.get("src") else ""
        print(f"  {p['key']:<14} {p['role']:<7} {p['name']}  variant {p['product_variant_ids']} ({p.get('variant_title')})  price {p['price']}{img}")
    print("Shipping methods:")
    for s in plan["shipping_methods"]:
        print(f"  {s['shipping_method']}  {s['price']}")
    print("Offers:")
    for o in plan.get("offers", []):
        cond = o["condition"]
        crit = f"qty>={cond['value']}" if cond["type"] == "count" else "any"
        code = f" code={o['code']}" if o.get("offer_type") == "voucher" else ""
        print(f"  {o['key']:<14} {o.get('offer_type','offer'):<7} {o['name']}  {crit} on {cond['package_keys']}  {o['benefit']['type']} {o['benefit']['value']}%{code}")
    print("Landed prices (confirm these):")
    for l in plan["landed_prices"]:
        print(f"  {l['tier']:<28} qty {l['qty']}  anchor {l['anchor']}  -{l['pct']}%  unit {l['unit_after']}  total {l['order_total']}")
    for r in plan.get("rationale", []):
        print(f"  why: {r}")
    if plan.get("blockers"):
        print("BLOCKERS (apply will refuse):")
        for b in plan["blockers"]:
            print(f"  - {b}")
    if plan.get("waivers"):
        print("Waivers recorded:")
        for w in plan["waivers"]:
            print(f"  - {w}")
    print("Requests apply will send, in order:")
    for i, (m, path, body) in enumerate(render_requests(plan), 1):
        print(f"  {i:>2}. {m} {path}  {json.dumps(body)}")
    if plan_path:
        sha = sha256_file(plan_path)
        print(f"Plan SHA-256: {sha}")
        print(f"Approve with: {PROG} apply --plan {plan_path} --yes --plan-sha256 {sha}")


# --------------------------------------------------------------------------- #
# apply (two-phase journal + reconcile)
# --------------------------------------------------------------------------- #

class Manifest:
    def __init__(self, path: Path, data: dict):
        self.path = Path(path)
        self.data = data

    @classmethod
    def new(cls, path: Path, plan: dict, plan_sha: str) -> "Manifest":
        return cls(path, {
            "store_slug": plan["store_slug"], "store_origin": plan["store_origin"],
            "plan_sha256": plan_sha, "run_id": str(uuid.uuid4()), "started_at": utcnow(),
            "campaign": {"status": "pending", "key": "campaign", "name": plan["campaign"]["name"]},
            "packages": [], "shipping_methods": [], "offers": [],
        })

    @classmethod
    def load(cls, path: Path) -> "Manifest":
        return cls(path, load_json(path))

    def save(self):
        atomic_write_json(self.path, self.data, mode=0o600)

    def entry(self, section: str, key: str) -> dict | None:
        if section == "campaign":
            return self.data["campaign"]
        return next((e for e in self.data[section] if e.get("key") == key), None)

    def mark(self, section: str, key: str, **fields):
        e = self.entry(section, key)
        if e is None:
            e = {"key": key}
            self.data[section].append(e)
        e.update(fields)
        self.save()
        return e


def _created(client: Client, method: str, path: str, body: dict, what: str) -> dict:
    """POST and return the created object. Package create answers with a one-element
    list (one package per variant id sent); everything else answers with an object."""
    status, resp = client.request(method, path, body)
    if isinstance(resp, list) and len(resp) == 1 and isinstance(resp[0], dict):
        resp = resp[0]
    if status not in (200, 201) or not isinstance(resp, dict):
        raise CampaignAdminError(f"{what}: {method} {path} returned {status}: {_short(resp)}")
    return resp


def reconcile(client: Client, man: Manifest, plan: dict) -> None:
    """Claim anything left `pending` by a lost response, by identity only."""
    camp = man.data["campaign"]
    if camp.get("status") == "pending":
        day = man.data["started_at"][:10]
        q = urllib.parse.urlencode({"name": camp["name"], "created_date_from": day, "page_size": 100})
        started = man.data["started_at"]
        now = utcnow()
        # No writable per-campaign marker exists on this API and the name is the only
        # identifier, so bound ownership to the run window [started_at, now] on top of the
        # exact-name and exactly-one-candidate checks. The residual (another operator
        # creating the identical name on the same store inside this window) is documented
        # in references/admin-api-contract.md (Reconcile residual).
        cands = [c for c in client.paginate(f"/api/admin/campaigns/?{q}")
                 if c.get("name") == camp["name"]
                 and _at_or_after(c.get("created_at"), started)
                 and _at_or_after(now, c.get("created_at"))]
        if len(cands) == 1:
            full = client.get_ok(f"/api/admin/campaigns/{cands[0]['id']}/")
            man.mark("campaign", "campaign", status="created", id=full["id"], api_key=full.get("api_key"),
                     created_at=full.get("created_at"), reconciled=True)
        elif not cands:
            man.mark("campaign", "campaign", status="absent")
        else:
            ids = [c["id"] for c in cands]
            raise CampaignAdminError(f"pending campaign {camp['name']!r} matches {len(cands)} candidates {ids}; resolve by hand")
    if man.data["campaign"].get("status") != "created":
        return
    cid = man.data["campaign"]["id"]

    def _pending(section):
        return any(e.get("status") == "pending" for e in man.data[section])
    # Only read back the sections that actually have something to reconcile; a
    # resume with nothing pending costs zero paginated GETs here.
    pkgs = client.paginate(f"/api/admin/campaigns/{cid}/packages/") if _pending("packages") else []
    ships = client.paginate(f"/api/admin/campaigns/{cid}/shipping-methods/") if _pending("shipping_methods") else []
    offers = client.paginate(f"/api/admin/campaigns/{cid}/offers/") if (plan.get("offers") and _pending("offers")) else []
    plan_pk = {p["key"]: p for p in plan["packages"]}
    for e in man.data["packages"]:
        if e.get("status") == "pending":
            p = plan_pk[e["key"]]
            vid = p["product_variant_ids"][0] if p.get("product_variant_ids") else None
            hit = [x for x in pkgs if x.get("product_variant_id") == vid and str(x.get("name", "")).startswith(p["name"])]
            if len(hit) == 1:
                man.mark("packages", e["key"], status="created", id=hit[0]["id"],
                         name=hit[0].get("name"), product_variant_id=hit[0].get("product_variant_id"),
                         reconciled=True)
            elif not hit:
                man.mark("packages", e["key"], status="absent")
            else:
                raise CampaignAdminError(f"pending package {e['key']} matches {len(hit)} remote packages; resolve by hand")
    for e in man.data["shipping_methods"]:
        if e.get("status") == "pending":
            hit = [x for x in ships if x.get("shipping_method") == e["key"]]
            if len(hit) == 1:
                man.mark("shipping_methods", e["key"], status="created", id=hit[0]["id"], reconciled=True)
            elif not hit:
                man.mark("shipping_methods", e["key"], status="absent")
            else:
                raise CampaignAdminError(f"pending shipping method {e['key']} matches {len(hit)} remote entries")
    plan_of = {o["key"]: o for o in plan.get("offers", [])}
    for e in man.data["offers"]:
        if e.get("status") == "pending":
            hit = [x for x in offers if x.get("name") == plan_of[e["key"]]["name"]]
            if len(hit) == 1:
                man.mark("offers", e["key"], status="created", id=hit[0]["id"],
                         name=hit[0].get("name"), reconciled=True)
            elif not hit:
                man.mark("offers", e["key"], status="absent")
            else:
                raise CampaignAdminError(f"pending offer {e['key']} matches {len(hit)} remote offers")


def apply(client: Client, plan: dict, plan_sha: str, manifest_path: Path, resume_path: Path | None) -> Manifest:
    errs = validate_plan(plan)
    if errs:
        raise CampaignAdminError("plan is invalid:\n  - " + "\n  - ".join(errs))
    if plan.get("blockers"):
        raise CampaignAdminError("plan has blockers; clear them and re-run recommend:\n  - " + "\n  - ".join(plan["blockers"]))

    if resume_path:
        man = Manifest.load(resume_path)
        if manifest_path.exists() and not _same_file(manifest_path, resume_path):
            other = load_json(manifest_path)
            if other.get("run_id") != man.data.get("run_id"):
                raise CampaignAdminError(
                    f"{manifest_path} already holds a different run ({other.get('run_id')}); refusing to "
                    "overwrite it. Resume into the run directory of the manifest being resumed (the "
                    "default), or pass --out <that directory>.")
        # Writes go to the gitignored out-dir manifest, never back to an arbitrary
        # --resume path that might be tracked (the manifest holds the campaign api_key).
        man.path = manifest_path
        check_origin_binding(man.data, "manifest")
        if man.data["store_slug"] != plan["store_slug"]:
            raise CampaignAdminError("manifest store_slug does not match the plan")
        if man.data["plan_sha256"] != plan_sha:
            raise CampaignAdminError("manifest plan_sha256 does not match this plan file; resume needs the same plan")
        if man.data["campaign"].get("status") == "created":
            live = client.get_ok(f"/api/admin/campaigns/{man.data['campaign']['id']}/")
            if live.get("name") != man.data["campaign"].get("name"):
                raise CampaignAdminError("manifest campaign name does not match the live campaign; refusing to resume")
            if not same_instant(live.get("created_at"), man.data["campaign"].get("created_at")):
                raise CampaignAdminError("manifest campaign created_at does not match the live campaign; refusing to resume")
        reconcile(client, man, plan)
    else:
        if manifest_path.exists():
            raise CampaignAdminError(f"{manifest_path} already exists; pass --resume {manifest_path} or move it aside")
        q = urllib.parse.urlencode({"name": plan["campaign"]["name"], "page_size": 100})
        if any(c.get("name") == plan["campaign"]["name"] for c in client.paginate(f"/api/admin/campaigns/?{q}")):
            raise CampaignAdminError(f"a campaign named {plan['campaign']['name']!r} already exists on the store; "
                                     "rename it in the plan or resume from that run's manifest")
        man = Manifest.new(manifest_path, plan, plan_sha)
        man.save()

    # campaign
    camp = man.data["campaign"]
    if camp.get("status") != "created":
        body = campaign_body(plan)
        man.mark("campaign", "campaign", status="pending", intent=body, started_at=utcnow())
        resp = _created(client, "POST", "/api/admin/campaigns/", body, "create campaign")
        man.mark("campaign", "campaign", status="created", id=resp["id"], api_key=resp.get("api_key"),
                 created_at=resp.get("created_at"), intent=None)
        print(f"created campaign {resp['id']} {plan['campaign']['name']!r} (api_key stored in the run manifest)")
    cid = man.data["campaign"]["id"]

    # packages, each followed immediately by its optional image override
    images_unsupported, image_failures = False, []
    image_set_ok = any(e.get("image_status") == "set" for e in man.data["packages"])
    for p in plan["packages"]:
        e = man.entry("packages", p["key"])
        if not (e and e.get("status") == "created"):
            body = package_body(p)
            man.mark("packages", p["key"], status="pending", intent=body)
            resp = _created(client, "POST", f"/api/admin/campaigns/{cid}/packages/", body, f"create package {p['key']}")
            e = man.mark("packages", p["key"], status="created", id=resp["id"], name=resp.get("name"),
                         product_variant_id=resp.get("product_variant_id"),
                         image_at_create=resp.get("image"), intent=None)
            print(f"created package {resp['id']} {p['name']!r}")

        # Outside the created-guard above on purpose: a resumed run whose package
        # already exists must still be able to land its image.
        img = image_body(p)
        if img is None:
            continue
        st_img = e.get("image_status")
        if st_img in ("set", "unsupported", "failed"):
            # Terminal. `unsupported` means the endpoint is absent; `failed` means the
            # server rejected this src, and the src cannot change without changing the
            # plan hash, so a retry would reproduce the same rejection forever.
            images_unsupported = images_unsupported or st_img == "unsupported"
            if st_img == "failed":
                image_failures.append(f"{p['key']}: {e.get('image_error')}")
            continue
        if images_unsupported:
            man.mark("packages", p["key"], image_status="unsupported", image_intent=None)
            continue

        man.mark("packages", p["key"], image_status="pending", image_intent=img)
        status, resp = client.request("PUT", f"/api/admin/campaigns/{cid}/packages/{e['id']}/image/", img)
        if status in (404, 405):
            # 405 is unambiguous: the route exists and will not take a PUT. A 404 is not
            # — it is equally "this package id is gone". Only conclude the store lacks the
            # endpoint while nothing has proved otherwise; once one PUT has landed, the
            # route demonstrably exists and a 404 is about that package alone.
            if status == 405 or not image_set_ok:
                man.mark("packages", p["key"], image_status="unsupported", image_intent=None)
                images_unsupported = True
            else:
                # `unsupported` would be a lie here — it is documented as "this store has
                # no image endpoint", and one has already answered. This is a per-package
                # failure: terminal like any other, reported, and never store-wide.
                detail = "404 after another package's image landed; the package id is likely gone"
                man.mark("packages", p["key"], image_status="failed", image_intent=None,
                         image_error=detail)
                image_failures.append(f"{p['key']}: {detail}; not retried")
        elif status is None or status == 429 or status >= 500:
            # Not a verdict on the request: the write may well have landed. Every 5xx
            # counts, not just the five the GET retry loop lists, because the promise in
            # SKILL.md is that a server-side failure is resumable. A PUT is a replace and
            # a resume runs against the identical plan, so leaving this `pending` makes it
            # retryable without any risk of a duplicate.
            man.mark("packages", p["key"], image_error=_short(resp))
            image_failures.append(f"{p['key']}: no answer from the image endpoint "
                                  f"(left pending, --resume will retry): {_short(resp)}")
        elif status != 200 or not isinstance(resp, dict) or not resp.get("image"):
            man.mark("packages", p["key"], image_status="failed", image_intent=None, image_error=_short(resp))
            image_failures.append(f"{p['key']}: PUT returned {status}: {_short(resp)}")
        else:
            man.mark("packages", p["key"], image_status="set", image_intent=None,
                     image=resp.get("image"), image_error=None)
            image_set_ok = True
            print(f"set image on package {e['id']} {p['key']!r}")

    # shipping
    for s in plan["shipping_methods"]:
        key = s["shipping_method"]
        e = man.entry("shipping_methods", key)
        if e and e.get("status") == "created":
            continue
        body = {"shipping_method": key, "price": s["price"]}
        man.mark("shipping_methods", key, status="pending", intent=body)
        resp = _created(client, "POST", f"/api/admin/campaigns/{cid}/shipping-methods/", body, f"create shipping {key}")
        man.mark("shipping_methods", key, status="created", id=resp["id"], intent=None)
        print(f"created shipping method {resp['id']} {key!r} at {s['price']}")

    # offers
    package_ids = {e["key"]: e["id"] for e in man.data["packages"] if e.get("status") == "created"}
    offers_unsupported = False
    for o in plan.get("offers", []):
        e = man.entry("offers", o["key"])
        if e and e.get("status") in ("created", "unsupported"):
            # `unsupported` is terminal: a resume must not re-POST it into the same
            # 404/405, which would loop forever. It stays a dashboard hand-off.
            offers_unsupported = offers_unsupported or (e.get("status") == "unsupported")
            continue
        if offers_unsupported:
            man.mark("offers", o["key"], status="unsupported", intent=None)
            continue
        body = offer_body(o, package_ids)
        man.mark("offers", o["key"], status="pending", intent=body)
        status, resp = client.request("POST", f"/api/admin/campaigns/{cid}/offers/", body)
        if status in (404, 405):
            man.mark("offers", o["key"], status="unsupported", intent=None)
            offers_unsupported = True
            continue
        if status not in (200, 201) or not isinstance(resp, dict):
            raise CampaignAdminError(f"create offer {o['key']}: POST returned {status}: {_short(resp)}")
        man.mark("offers", o["key"], status="created", id=resp["id"], name=resp.get("name"), intent=None)
        print(f"created offer {resp['id']} {o['name']!r}")
    man.data["completed_at"] = utcnow()
    man.save()

    # Everything that degraded is reported together: one problem must never hide
    # another, and the run itself completed, so this is a report, not a rollback.
    problems = []
    if offers_unsupported:
        problems.append(
            "offers endpoint answered 404/405: this store does not accept offers over the API yet. "
            "Campaign, packages and shipping are created and journalled (offers marked unsupported); "
            "create the offers in the dashboard (Offers & Discounts), or teardown. A --resume will "
            "not retry the unsupported offers.")
    if images_unsupported:
        problems.append(
            "package image endpoint answered 404/405: this store's build predates package images. "
            "Every resource is created; packages keep whatever image the catalogue supplied at create "
            "time. Set the overrides in the dashboard. A --resume will not retry them.")
    if image_failures:
        problems.append(
            "package image override(s) did not land:\n    - " + "\n    - ".join(image_failures) +
            "\n  The packages exist and are usable. Anything left pending is retried by --resume; a "
            "rejected src is terminal, because changing it changes the plan hash, which --resume "
            "refuses and a fresh run refuses as a duplicate campaign. Set that image in the dashboard, "
            "or teardown and recreate from a corrected plan.")
    if problems:
        raise CampaignAdminError("\n  ".join(problems))
    return man


# --------------------------------------------------------------------------- #
# teardown
# --------------------------------------------------------------------------- #

def teardown(client: Client, man: Manifest, plan: dict, plan_sha: str, confirm) -> None:
    check_origin_binding(man.data, "manifest")
    if man.data["store_slug"] != plan["store_slug"] or man.data["plan_sha256"] != plan_sha:
        raise CampaignAdminError("manifest does not belong to this plan (slug or plan hash differs)")
    camp = man.data["campaign"]
    if camp.get("status") not in ("created", "deleting", "deleted"):
        print("nothing to tear down: the campaign was never created")
        return
    cid = camp.get("id")
    plan_pk = {p["key"]: p for p in plan["packages"]}
    plan_of = {o["key"]: o for o in plan.get("offers", [])}

    # Phase 1: read everything back and verify identity BEFORE any DELETE.
    todo = []  # (section, key, path)
    status, live = client.request("GET", f"/api/admin/campaigns/{cid}/")
    if status == 404:
        man.mark("campaign", "campaign", status="deleted")
        print(f"campaign {cid} already gone; nothing left to delete")
        return
    if status != 200 or live.get("name") != camp.get("name") or not same_instant(live.get("created_at"), camp.get("created_at")):
        raise CampaignAdminError(f"campaign {cid} identity mismatch (name/created_at); refusing to delete anything")

    for section, path_part, ident in (
        ("offers", "offers", lambda e, x: x.get("name") == plan_of.get(e["key"], {}).get("name")),
        ("shipping_methods", "shipping-methods", lambda e, x: x.get("shipping_method") == e["key"]),
        ("packages", "packages", lambda e, x: x.get("product_variant_id") == e.get("product_variant_id")
         and x.get("name") == (e.get("name") or plan_pk.get(e["key"], {}).get("name"))),
    ):
        for e in man.data[section]:
            if e.get("status") not in ("created", "deleting") or not e.get("id"):
                continue
            path = f"/api/admin/campaigns/{cid}/{path_part}/{e['id']}/"
            st, x = client.request("GET", path)
            if st == 404:
                man.mark(section, e["key"], status="deleted")
                continue
            if st != 200 or not ident(e, x):
                raise CampaignAdminError(f"{section} {e['key']} (id {e['id']}) identity mismatch; aborting before any delete")
            todo.append((section, e["key"], path))
    todo.append(("campaign", "campaign", f"/api/admin/campaigns/{cid}/"))

    print(f"Teardown target: {man.data['store_origin']} campaign {cid} {camp.get('name')!r}")
    print(f"  {sum(1 for t in todo if t[0]=='offers')} offers, {sum(1 for t in todo if t[0]=='shipping_methods')} shipping methods, "
          f"{sum(1 for t in todo if t[0]=='packages')} packages, then the campaign")
    if not confirm():
        raise CampaignAdminError("teardown not confirmed (pass --yes)")

    # Phase 2: delete, journalling deleting -> deleted.
    for section, key, path in todo:
        man.mark(section, key, status="deleting")
        st, body = client.request("DELETE", path)
        if st in (200, 202, 204, 404):
            man.mark(section, key, status="deleted")
            print(f"deleted {section} {key}")
        else:
            raise CampaignAdminError(f"DELETE {path} returned {st}: {_short(body)}; rerun teardown to continue")
    man.data["torn_down_at"] = utcnow()
    man.save()


# --------------------------------------------------------------------------- #
# verify
# --------------------------------------------------------------------------- #

def _num(x) -> Decimal | None:
    try:
        return D(x) if x is not None else None
    except CampaignAdminError:
        return None


def _plan_has_free_shipping(plan: dict) -> bool:
    return any(o.get("offer_type", "offer") == "offer"
               and o["benefit"]["type"] == "shipping_percentage"
               and D(o["benefit"]["value"]) == Decimal(100)
               and o["condition"]["type"] == "any"
               for o in plan.get("offers", []))


def _cart_cases_from_plan(plan: dict, ids: dict) -> list:
    """Build (name, lines, vouchers, expected_subtotal) cases for carts/calculate
    straight from the structured landed_prices rows. Each row names its
    package_keys and offer_key, so nothing is parsed back out of display labels.
    Rows whose packages were not created are skipped (the admin read-back has
    already recorded that failure)."""
    offers = {o["key"]: o for o in plan.get("offers", [])}
    exit_offer = offers.get("exit-pop")
    cases = []
    for l in plan.get("landed_prices", []):
        pkg_ids = [ids[k] for k in l["package_keys"] if ids.get(k)]
        if not pkg_ids:
            continue
        qty, total = l["qty"], D(l["order_total"])
        if l["kind"] in ("tier", "single"):
            cases.append((f"{l['tier']} single variant", [{"package_id": pkg_ids[0], "quantity": qty}], [], total))
            if qty > 1 and len(pkg_ids) > 1:
                lines = [{"package_id": pkg_ids[i % len(pkg_ids)], "quantity": 1} for i in range(qty)]
                cases.append((f"{l['tier']} mixed variants", lines, [], total))
            if exit_offer:
                unit = landed_unit(D(l["unit_after"]), D(exit_offer["benefit"]["value"]),
                                   exit_offer["benefit"].get("price_rounding"))
                cases.append((f"{l['tier']} + exit voucher", [{"package_id": pkg_ids[0], "quantity": qty}],
                              [exit_offer["code"]], unit * qty))
        elif l["kind"] == "upsell":
            # Upsell vouchers apply post-purchase (?upsell=true skips site offers).
            up = offers.get(l.get("offer_key")) if l.get("offer_key") else None
            if up and up.get("offer_type") == "voucher":
                cases.append((f"{l['tier']} voucher", [{"package_id": pkg_ids[0], "quantity": 1}],
                              [up["code"]], D(l["unit_after"])))
    return cases


def verify(client: Client, cart: Client, man: Manifest, plan: dict, plan_sha: str) -> dict:
    errs = validate_plan(plan)
    if errs:
        raise CampaignAdminError("plan is invalid, cannot verify:\n  - " + "\n  - ".join(errs))
    check_origin_binding(man.data, "manifest")
    if man.data["plan_sha256"] != plan_sha:
        raise CampaignAdminError("manifest plan_sha256 does not match this plan file")
    camp = man.data["campaign"]
    if camp.get("status") != "created":
        raise CampaignAdminError("campaign is not in created state; nothing to verify")
    cid = camp["id"]
    checks, cases = [], []

    def check(name, ok, detail=""):
        checks.append({"check": name, "result": "PASS" if ok else "FAIL", "detail": detail})

    live = client.get_ok(f"/api/admin/campaigns/{cid}/")
    c = plan["campaign"]
    check("campaign.currency", live.get("currency") == c["currency"], str(live.get("currency")))
    check("campaign.language", live.get("language") == c["language"], str(live.get("language")))
    check("campaign.gateway_group", live.get("payment_gateway_group_id") == c["payment_gateway_group_id"], str(live.get("payment_gateway_group_id")))
    live_methods = sorted(m["code"] for m in live.get("available_payment_methods", []))
    check("campaign.payment_methods", live_methods == sorted(c["available_payment_methods"]), str(live_methods))
    live_express = sorted(m["code"] for m in live.get("available_express_payment_methods", []))
    check("campaign.express_methods", live_express == sorted(c.get("available_express_payment_methods", [])), str(live_express))
    live_countries = sorted(x["code"] for x in live.get("available_shipping_countries", []))
    check("campaign.shipping_countries", live_countries == sorted(c.get("available_shipping_countries", [])), str(live_countries))
    live_addl = sorted(live.get("additional_currencies", []))
    check("campaign.additional_currencies", live_addl == sorted(c.get("additional_currencies", [])), str(live_addl))
    check("campaign.statement_descriptor", (live.get("statement_descriptor") or "") == (c.get("statement_descriptor") or ""), str(live.get("statement_descriptor")))
    check("campaign.api_key_present", bool(live.get("api_key")))

    pkgs = {p["id"]: p for p in client.paginate(f"/api/admin/campaigns/{cid}/packages/")}
    ids = {}
    # The loop below walks the manifest, so a planned package that never reached the
    # journal would otherwise produce no row at all and fail nothing.
    journalled = {e.get("key") for e in man.data["packages"]}
    for p in plan["packages"]:
        if p["key"] not in journalled:
            check(f"package {p['key']} journalled", False, "no manifest entry for this planned package")
    for e in man.data["packages"]:
        if e.get("status") != "created":
            check(f"package {e['key']}", False, f"status {e.get('status')}")
            continue
        p = next(x for x in plan["packages"] if x["key"] == e["key"])
        live_p = pkgs.get(e.get("id"))
        ids[e["key"]] = e.get("id")
        if not live_p:
            check(f"package {e['key']}", False, "missing remotely")
            continue
        price = next((x.get("price") for x in live_p.get("prices", []) if x.get("currency") == c["currency"]), None)
        check(f"package {e['key']} price", _num(price) == D(p["price"]), f"{price} vs {p['price']}")
        check(f"package {e['key']} variant", live_p.get("product_variant_id") == p["product_variant_ids"][0], str(live_p.get("product_variant_id")))
        check(f"package {e['key']} purchasable", live_p.get("product_purchase_availability") == "available", str(live_p.get("product_purchase_availability")))
        # Presence covers every package, not just overridden ones: package create
        # inherits the catalogue image and swallows a failed fetch, so this row is the
        # only thing that surfaces a package that silently ended up with no picture.
        check(f"package {e['key']} image", bool(live_p.get("image")), str(live_p.get("image") or "none"))
        if p.get("image"):
            # Never compare against the src that was sent: the returned URL is a
            # server-built thumbnail. The receipt this run recorded is the only
            # evidence that our PUT is the image now live.
            check(f"package {e['key']} image override",
                  e.get("image_status") == "set" and live_p.get("image") == e.get("image"),
                  f"{e.get('image_status')} {live_p.get('image')}")

    ships = {s["id"]: s for s in client.paginate(f"/api/admin/campaigns/{cid}/shipping-methods/")}
    shipping_id, shipping_price = None, Decimal(0)
    for e in man.data["shipping_methods"]:
        s = next(x for x in plan["shipping_methods"] if x["shipping_method"] == e["key"])
        live_s = ships.get(e.get("id"))
        if not live_s:
            check(f"shipping {e['key']}", False, "missing remotely")
            continue
        price = next((x.get("price") for x in live_s.get("prices", []) if x.get("currency") == c["currency"]), None)
        check(f"shipping {e['key']} price", _num(price) == D(s["price"]), f"{price} vs {s['price']}")
        if shipping_id is None:
            shipping_id, shipping_price = e["id"], D(s["price"])

    if plan.get("offers"):
        # The offers LIST omits condition.packages; only the per-offer RETRIEVE
        # carries the scope, so read each offer back by id.
        for e in man.data["offers"]:
            o = next(x for x in plan["offers"] if x["key"] == e["key"])
            if e.get("status") != "created" or not e.get("id"):
                check(f"offer {e['key']}", False, f"status {e.get('status')}")
                continue
            st, live_o = client.request("GET", f"/api/admin/campaigns/{cid}/offers/{e['id']}/")
            if st != 200 or not isinstance(live_o, dict):
                check(f"offer {e['key']}", False, f"retrieve returned {st}")
                continue
            unresolved = [k for k in o["condition"]["package_keys"] if k not in ids]
            if unresolved:
                check(f"offer {e['key']} scope", False, f"package key(s) {unresolved} were never created")
                continue
            want = sorted(ids[k] for k in o["condition"]["package_keys"])
            got = sorted(p.get("id") for p in (live_o.get("condition") or {}).get("packages", []))
            check(f"offer {e['key']} scope", got == want and not (live_o.get("condition") or {}).get("all_packages"), f"{got} vs {want}")
            check(f"offer {e['key']} benefit", _num((live_o.get("benefit") or {}).get("value")) == D(o["benefit"]["value"]), str((live_o.get("benefit") or {}).get("value")))
            check(f"offer {e['key']} type", live_o.get("offer_type") == o.get("offer_type", "offer"), str(live_o.get("offer_type")))
            if o.get("offer_type") == "voucher":
                check(f"offer {e['key']} code", live_o.get("code") == o["code"], str(live_o.get("code")))

    # Pricing truth: carts/calculate with the campaign key.
    free_shipping = _plan_has_free_shipping(plan)
    if not any(p["role"] == "hero" and ids.get(p["key"]) for p in plan["packages"]):
        check("hero packages present", False, "no created hero package ids in the manifest")
    cart_cases = _cart_cases_from_plan(plan, ids)

    for name, lines, vouchers, expected in cart_cases:
        body = {"lines": lines}
        if vouchers:
            body["vouchers"] = vouchers
        if shipping_id is not None:
            body["shipping_method"] = shipping_id
        status, resp = cart.request("POST", "/api/v1/carts/calculate/", body)
        got_total = _num((resp or {}).get("total")) if isinstance(resp, dict) else None
        ship_component = Decimal(0) if free_shipping else (shipping_price if shipping_id is not None else Decimal(0))
        want_total = expected + ship_component
        units = sum(l["quantity"] for l in lines)
        tol = Decimal("0.01") * units  # documented per-unit rounding bound (offer doctrine: Rounding and stacking)
        delta = None if got_total is None else (got_total - want_total)
        ok = status == 200 and delta is not None and abs(delta) <= tol
        cases.append({"case": name, "result": "PASS" if ok else "FAIL", "status": status,
                      "expected_total": money(want_total), "got_total": None if got_total is None else money(got_total),
                      "delta": None if delta is None else money(delta),
                      "tolerance": money(tol), "units": units,
                      "request": body, "response_keys": sorted(resp.keys()) if isinstance(resp, dict) else None,
                      "error": None if ok else _short(resp)})

    result = "PASS" if all(x["result"] == "PASS" for x in checks + cases) else "FAIL"
    return {"manifest_run_id": man.data["run_id"], "plan_sha256": plan_sha, "verified_at": utcnow(),
            "admin_checks": checks, "calculate_cases": cases, "result": result}


def print_verify(report: dict) -> None:
    for x in report["admin_checks"]:
        print(f"  {x['result']}  {x['check']}  {x['detail']}")
    for x in report["calculate_cases"]:
        extra = "" if x["result"] == "PASS" else f"  ({x['error']})"
        print(f"  {x['result']}  calculate {x['case']}: expected {x['expected_total']} got {x['got_total']}{extra}")
    print(f"VERIFY: {report['result']}")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _client_for(slug: str) -> Client:
    return Client(slug_to_origin(slug), load_token(slug))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog=PROG, description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("discover", help="read-only store snapshot")
    d.add_argument("--store", required=True, help="store subdomain, e.g. mystore or mystore.29next.store")
    d.add_argument("--out")

    r = sub.add_parser("recommend", help="build campaign-plan.json from discovery + operator inputs")
    r.add_argument("--discovery", required=True)
    r.add_argument("--hero", type=int, required=True, help="hero product id")
    r.add_argument("--ctc", required=True, choices=["low", "high"])
    r.add_argument("--anchor-price", required=True, help="package price tiers discount from")
    r.add_argument("--shipping", action="append", required=True, help="<code>:<price>, repeatable")
    r.add_argument("--name", help="campaign name (default: hero product title)")
    r.add_argument("--gateway-group", type=int)
    r.add_argument("--payment-methods")
    r.add_argument("--express-methods")
    r.add_argument("--currency")
    r.add_argument("--language")
    r.add_argument("--countries", help="comma-separated ISO alpha-2")
    r.add_argument("--tiers", help="comma-separated percentages for Buy 1/2/3 (default 50,55,60)")
    r.add_argument("--exit", help="exit-pop voucher percentage (default 10; 0 disables)")
    r.add_argument("--exit-code")
    r.add_argument("--bump", action="append", help="<variant_id>:<price>, repeatable")
    r.add_argument("--upsell", action="append", help="<variant_id>:<price>:<pct>, repeatable")
    r.add_argument("--free-shipping", action="store_true")
    r.add_argument("--rounding", help="price_rounding for tier/voucher offers; one of "
                   + ", ".join(PRICE_ROUNDINGS[1:]))
    r.add_argument("--statement-descriptor")
    r.add_argument("--out")

    p = sub.add_parser("plan", help="validate and print the requests + approval hash")
    p.add_argument("--plan", required=True)
    p.add_argument("--check-store", action="store_true", help="also check the store for a same-named campaign")

    a = sub.add_parser("apply", help="create the campaign (gated)")
    a.add_argument("--plan", required=True)
    a.add_argument("--yes", action="store_true")
    a.add_argument("--plan-sha256")
    a.add_argument("--resume", help="run-manifest.json of an interrupted run")
    a.add_argument("--out")

    v = sub.add_parser("verify", help="read back + carts/calculate")
    v.add_argument("--manifest", required=True)
    v.add_argument("--plan", required=True)
    v.add_argument("--out")

    t = sub.add_parser("teardown", help="delete what this run created")
    t.add_argument("--manifest", required=True)
    t.add_argument("--plan", required=True)
    t.add_argument("--yes", action="store_true")

    m = sub.add_parser("metadata", help="audit (default) or create the campaign metadata definitions")
    m.add_argument("--store", required=True, help="store subdomain, e.g. mystore or mystore.29next.store")
    m.add_argument("--apply", action="store_true", help="create the missing definitions (needs metadata:write)")

    args = ap.parse_args(argv)
    try:
        return _dispatch(args)
    except CampaignAdminError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


def _dispatch(args) -> int:
    if args.cmd == "discover":
        slug = normalize_store(args.store)
        client = _client_for(slug)
        out = resolve_out_dir(args.out, Path.cwd() / RUNS_DIR_NAME / slug)
        disc = discover(client, slug)
        path = out / "discovery.json"
        atomic_write_json(path, disc)
        print_discovery(disc)
        print(f"wrote {path}")
        return 0

    if args.cmd == "recommend":
        disc = load_json(args.discovery)
        plan = recommend(disc, args)
        errs = validate_plan(plan)
        if errs:
            raise CampaignAdminError("generated plan failed validation (bug or bad input):\n  - " + "\n  - ".join(errs))
        out = resolve_out_dir(args.out, run_dir_for(args.discovery))
        if (out / MANIFEST_NAME).exists():
            raise CampaignAdminError(
                f"{out} already holds a run ({MANIFEST_NAME}); its plan is the only file that can resume or "
                "tear that campaign down. Pass --out <new directory> for another campaign.")
        path = out / "campaign-plan.json"
        atomic_write_json(path, plan)
        print_plan(plan, path)
        print(f"wrote {path}")
        return 0

    if args.cmd == "plan":
        path = Path(args.plan)
        plan = load_json(path)
        errs = validate_plan(plan)
        if errs:
            print("PLAN INVALID:\n  - " + "\n  - ".join(errs))
            return 1
        if args.check_store:
            client = _client_for(plan["store_slug"])
            q = urllib.parse.urlencode({"name": plan["campaign"]["name"], "page_size": 100})
            if any(c.get("name") == plan["campaign"]["name"] for c in client.paginate(f"/api/admin/campaigns/?{q}")):
                print(f"BLOCKER: a campaign named {plan['campaign']['name']!r} already exists on the store")
                return 1
        print_plan(plan, path)
        return 0

    if args.cmd == "apply":
        path = Path(args.plan)
        plan, sha = load_json_and_hash(path)
        if not args.yes or args.plan_sha256 != sha:
            print_plan(plan, path)
            print("\nNOT APPLIED: pass --yes --plan-sha256 <hash above> to approve this exact plan.", file=sys.stderr)
            return 2
        client = _client_for(plan["store_slug"])
        out = resolve_out_dir(args.out, run_dir_for(args.plan))
        man = apply(client, plan, sha, out / MANIFEST_NAME, Path(args.resume) if args.resume else None)
        camp = man.data["campaign"]
        print(f"\nDONE: campaign {camp['id']} {camp['name']!r} on {plan['store_origin']}")
        print(f"  api_key stored in {man.path} (mode 600); not echoed here")
        for e in man.data["packages"]:
            print(f"  package {e['key']}: id {e.get('id')}  (data-next-package-id=\"{e.get('id')}\")")
        for e in man.data["offers"]:
            print(f"  offer {e['key']}: id {e.get('id')}")
        for h in plan.get("handoff", []):
            print(f"  next: {h}")
        return 0

    if args.cmd == "verify":
        plan_path = Path(args.plan)
        plan = load_json(plan_path)
        man = Manifest.load(Path(args.manifest))
        client = _client_for(plan["store_slug"])
        out = resolve_out_dir(args.out, run_dir_for(args.plan))
        cart = Client(CART_API_ORIGIN, man.data["campaign"].get("api_key"), auth_scheme="raw", send_version_header=False)
        report = verify(client, cart, man, plan, sha256_file(plan_path))
        atomic_write_json(out / "verify-report.json", report)
        print_verify(report)
        return 0 if report["result"] == "PASS" else 1

    if args.cmd == "teardown":
        plan_path = Path(args.plan)
        plan = load_json(plan_path)
        # Teardown saves status changes back to this file (a campaign GET answering 404
        # writes before any confirmation), so it gets the run-directory guard first.
        guard_manifest_path(args.manifest)
        man = Manifest.load(Path(args.manifest))
        client = _client_for(plan["store_slug"])

        def confirm():
            if args.yes:
                return True
            if sys.stdin.isatty():
                return input("Type DELETE to confirm: ").strip() == "DELETE"
            return False
        teardown(client, man, plan, sha256_file(plan_path), confirm)
        return 0

    if args.cmd == "metadata":
        slug = normalize_store(args.store)
        return metadata_provision(_client_for(slug), slug, args.apply)
    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
