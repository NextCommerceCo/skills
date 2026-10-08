#!/usr/bin/env python3
"""Provision a Campaigns App campaign over the NEXT Admin API.

Backs the `/next-campaigns-create` skill and is normally run through
`next-campaigns-create.sh`. Eight subcommands, in the usual order:

    discover  --store <slug>                      read-only store snapshot
    metadata  --store <slug> [--apply]            audit or create the campaign metadata definitions
    recommend --discovery ... --hero ... --ctc ... [--offer-type quantity|bxgy|gwp] build campaign-plan.json
    plan      --plan campaign-plan.json           validate + print requests + hash
    apply     --plan ... --yes --plan-sha256 ...  create campaign/packages/shipping/offers
    verify    --manifest ... --plan ...           read back + Cart API calculate
    edit      --manifest ... --plan ... --changes campaign-edit.json
                                                  change what THIS run created, in place (gated)
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
- `edit` writes only to ids recorded in the manifest, after the same read-back and
  identity check. It refuses without --yes and an --edit-sha256 that covers the
  plan, the change list and the live state it read; saves each object's
  before-image in a receipt before the first write; re-reads each object just
  before its write and again after it; and never retries a write.
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
from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation
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
CODE_STEM_MAX = 12
# A short name starts with a letter: a leading number reads as a catalogue
# number, which code_stem drops from generated names for the same reason.
CODE_STEM_RE = re.compile(r"^[A-Z][A-Z0-9]{0,%d}$" % (CODE_STEM_MAX - 1))
# Generic nouns that say nothing about which product a code belongs to. Extend
# here and in references/offer-doctrine.md together.
GENERIC_CODE_WORDS = frozenset({"ORNAMENT", "ORNAMENTS", "CHRISTMAS", "XMAS", "CALENDAR", "CALENDARS"})
PRICE_ROUNDINGS = (None, "0.00", "0.95", "0.97", "0.99")
BENEFIT_TYPES = ("package_percentage", "shipping_percentage", "order_percentage")
CONDITION_TYPES = ("any", "count")
OFFER_TYPES = ("offer", "voucher")
OFFER_KINDS = ("quantity", "bxgy", "gwp")
DEFAULT_TIERS = (50, 55, 60)
DEFAULT_EXIT_PCT = 10

PROG = "next-campaigns-create.sh"
RUNS_DIR_NAME = "next-campaigns-create-runs"
MANIFEST_NAME = "run-manifest.json"
DOTENV_NAME = ".env"
GENERIC_TOKEN_ENV = "NEXT_ADMIN_API_TOKEN"
TOKEN_SUFFIX = "_NEXT_ADMIN_API_TOKEN"
# Every scope the engine's requests need, per the published Admin API spec
# (2024-04-01). store:read covers GET /store/, the first request discover sends.
REQUIRED_SCOPES = ("store:read, campaigns:read, campaigns:write, catalogue:read, gateways:read, "
                   "metadata:read, metadata:write")
METADATA_SCOPES = "metadata:read, metadata:write"
# Path prefix -> (scope for GET, scope for writes). NO_SCOPE marks an endpoint the
# spec lists with an empty oauth2 scope list: any valid token may call it.
NO_SCOPE = ""
_SCOPE_TABLE = (
    ("/api/admin/store/", "store:read", None),
    ("/api/admin/gateway-groups/", "gateways:read", None),
    ("/api/admin/products/", "catalogue:read", "catalogue:write"),
    ("/api/admin/metadata/", "metadata:read", "metadata:write"),
    ("/api/admin/campaigns/", "campaigns:read", "campaigns:write"),
    ("/api/admin/shipping-methods/", NO_SCOPE, None),
)


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
        if value[:1] in ("'", '"'):
            if len(value) < 2 or value[-1] != value[0]:
                # Name the line, never the value: it is a credential.
                raise CampaignAdminError(f"the {name} line in {path} has an unterminated quote; fix it in a text editor")
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
                f"GET {path} returned {status}: the token was rejected by {self.origin}."
                + auth_hint(status, "GET", path), status, path)
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


def scope_for(method: str, path_or_url: str):
    """The scope a request needs: a scope string, NO_SCOPE for an endpoint the spec
    marks scopeless, or None when the path is not one this engine knows. Absolute
    URLs (pagination `next` links) and query strings are reduced to the path first."""
    path = urllib.parse.urlsplit(path_or_url).path
    best = None
    for prefix, read, write in _SCOPE_TABLE:
        if path.startswith(prefix) and (best is None or len(prefix) > len(best[0])):
            best = (prefix, read, write)
    if best is None:
        return None
    return best[1] if method.upper() == "GET" else best[2]


def auth_hint(status, method: str, path_or_url: str) -> str:
    """Why a 401/403 happened and what fixes it; empty for any other status."""
    if status not in (401, 403):
        return ""
    scope = scope_for(method, path_or_url)
    if scope is None:
        need = ""
    elif scope == NO_SCOPE:
        need = (f" {method} on this endpoint needs no scope, so the token itself was rejected "
                "(wrong store, revoked, or mistyped).")
    else:
        need = f" {method} on this endpoint needs the {scope} permission."
    return (f"{need} Create the API key on this store under Dashboard > Settings > API Access with "
            f"all of: {REQUIRED_SCOPES}. Retrying the same key does not help.")


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


def json_bytes(data) -> bytes:
    """The exact bytes atomic_write_json writes, so a file's hash can be journalled
    before the file is replaced."""
    return (json.dumps(data, indent=2, sort_keys=False) + "\n").encode()


def canonical_sha256(obj) -> str:
    """SHA-256 of an in-memory value, independent of key order and whitespace."""
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def atomic_write_json(path: Path, data, mode: int = 0o600) -> None:
    atomic_write_bytes(path, json_bytes(data), mode)


def atomic_write_bytes(path: Path, raw: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):  # not on Windows; mode 600 is not enforced there
            os.fchmod(fd, mode)  # restrict before any secret bytes are written
        with os.fdopen(fd, "wb") as f:
            f.write(raw)
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
    present, objects = {}, {}
    for d in client.paginate(METADATA_PATH):
        if isinstance(d, dict) and d.get("key"):
            present[d["key"]] = d.get("object")
            objects.setdefault(d["key"], set()).add(d.get("object"))
    missing, conflicts = [], []
    for f in METADATA_FIELDS:
        if f.key not in present:
            missing.append(f)
            continue
        # Every object the key appears under is checked, so a listing that repeats
        # a key under a second object is still reported rather than overwritten.
        wrong = sorted(o for o in objects[f.key] if isinstance(o, str) and o and o != f.object)
        if wrong:
            conflicts.append({"key": f.key, "defined_on": ", ".join(wrong), "needs": f.object})
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
            print(f"  [FAILED {status}] {f.object:<11} {f.key}: {_short(resp)}"
                  + auth_hint(status, "POST", METADATA_PATH))
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


def check_rounding_discount(offer: str, before: Decimal, pct: Decimal, rounding: str | None) -> Decimal:
    """Return landed_unit(before, pct, rounding); raise when price_rounding lifts
    the discounted unit to or above the price it discounts from. The floor-then-add
    step can undo a small discount (20.50 at 1% is 20.29, pinned to 20.95), and the
    offer would still be named and sold as a discount."""
    unit = landed_unit(before, pct, rounding)
    if rounding and unit >= before:
        raw = landed_unit(before, pct, None)
        raise CampaignAdminError(
            f"{offer}: {_pct_for_landed(pct)}% off {money(before)} is {money(raw)} unrounded, but price_rounding "
            f"{rounding} lands it at {money(unit)}, not below {money(before)}; pass a different "
            f"--rounding ending ({', '.join(PRICE_ROUNDINGS[1:])}) or omit --rounding")
    return unit


BXGY_QTY_MAX = 99


def bxgy_percentage(paid: int, free: int) -> Decimal:
    """Percentage-off-all-units that matches a true free-unit deal at exactly paid+free
    units, before per-unit rounding. The Campaigns App has no free-qty benefit, so
    recommend encodes buy-X-get-Y this way and labels it an approximation."""
    if type(paid) is not int or type(free) is not int or paid < 1 or free < 1:
        raise CampaignAdminError(
            f"buy-X-get-Y needs paid and free quantities that are integers >= 1; got paid={paid!r} free={free!r}")
    if paid > BXGY_QTY_MAX or free > BXGY_QTY_MAX:
        raise CampaignAdminError(
            f"buy-X-get-Y paid and free quantities must be <= {BXGY_QTY_MAX}; got paid={paid} free={free}")
    return Decimal(free) * Decimal(100) / Decimal(paid + free)


def true_bxgy_payable(anchor: Decimal, paid: int, free: int, qty: int) -> Decimal:
    """What a repeating free-unit deal would charge: complete deals of (paid+free),
    leftover units at full price. The engine cannot do this; it applies one % to
    every in-scope unit once the count is met."""
    if qty < 1:
        raise CampaignAdminError(f"quantity must be >= 1; got {qty!r}")
    deal = paid + free
    deals, rem = divmod(qty, deal)
    return anchor * (deals * paid + rem)


def _pct_for_landed(pct: Decimal):
    """Landed `pct` for BXGY rows: int when whole (matches quantity tiers), else a
    money string so fractional rates such as 33.33 stay exact. See admin-api-contract."""
    if pct == pct.to_integral_value():
        return int(pct)
    return money(pct)


def _bxgy_landed_row(tier, kind, qty, offer_key, hero_keys, anchor, pct, unit,
                     paid, free, note=None) -> dict:
    full = anchor * qty
    payable = unit * qty
    savings = full - payable
    row = {
        "tier": tier, "kind": kind, "qty": qty, "offer_key": offer_key,
        "package_keys": list(hero_keys),
        "anchor": money(anchor), "pct": _pct_for_landed(pct),
        "unit_after": money(unit), "order_total": money(payable),
        "paid_qty": paid, "free_qty": free, "total_qty": qty,
        "full_retail": money(full), "payable": money(payable),
        "savings": money(savings), "effective_pct": money(pct),
        "effective_unit": money(unit),
        "approximation": True,
    }
    if note:
        row["note"] = note
    return row


def short_name(title: str) -> str:
    return re.sub(r"\s+", " ", title).strip()


def code_stem(title: str) -> str:
    """The short product name a voucher code starts with: the title's distinctive
    words, without catalogue numbers, years or generic nouns, trimmed from the
    front to CODE_STEM_MAX characters. A bare number would run into the
    percentage suffix (SNAPSHOT2025 + 10), so number-only words never count."""
    words = [w for w in re.split(r"[^A-Z0-9]+", title.upper())
             if w and not w.isdigit() and w not in GENERIC_CODE_WORDS]
    if not words:
        raise CampaignAdminError(
            f"product title {title!r} has no distinctive words for a voucher code; "
            f"pass --short-name <product_id>:<NAME> (A-Z0-9, at most {CODE_STEM_MAX} characters)")
    while len("".join(words)) > CODE_STEM_MAX and len(words) > 1:
        words.pop(0)
    return "".join(words)[:CODE_STEM_MAX]


def code_from(stem: str, pct) -> str:
    """{STEM}{PCT}, the percentage as a whole number rounded down (57.5 -> 57)."""
    return f"{stem}{int(Decimal(str(pct)).to_integral_value(ROUND_FLOOR))}"


def _parse_short_names(specs, products) -> dict:
    known = {p["id"] for p in products}
    out = {}
    for spec in specs or []:
        pid_s, sep, name = spec.partition(":")
        try:
            pid = int(pid_s)
        except ValueError:
            pid = None
        name = name.strip().upper()
        if not sep or pid is None or not CODE_STEM_RE.match(name):
            raise CampaignAdminError(
                f"--short-name expects <product_id>:<NAME> with NAME A-Z0-9, starting with a letter, at most {CODE_STEM_MAX} "
                f"characters, got {spec!r}")
        if pid not in known:
            raise CampaignAdminError(f"--short-name product {pid} not found in discovery")
        if pid in out:
            raise CampaignAdminError(f"--short-name product {pid} given twice")
        out[pid] = name
    return out


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
    short_names = _parse_short_names(getattr(a, "short_name", None), discovery["products"])
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
        raise CampaignAdminError("at least one --shipping <code>:<price>[:<key>] is required")
    codes = {m["code"] for m in discovery["shipping_methods"]}
    shipping = []
    for spec in a.shipping:
        # <code>:<price>[:<key>]. The platform allows one campaign shipping method
        # per store code, so a code appears once; the optional key only names it.
        bits = spec.split(":")
        if len(bits) not in (2, 3):
            raise CampaignAdminError(f"--shipping expects <code>:<price>[:<key>], got {spec!r}")
        code, price = bits[0], bits[1]
        if code not in codes:
            raise CampaignAdminError(f"shipping code {code!r} is not configured on the store ({sorted(codes)})")
        if not DECIMAL_RE.match(price):
            raise CampaignAdminError(f"shipping price {price!r} is not a decimal")
        entry = {"shipping_method": code, "price": money(D(price))}
        if len(bits) == 3:
            if not bits[2]:
                raise CampaignAdminError(f"--shipping {spec!r}: the key after the price is empty")
            entry["key"] = bits[2]
        if any(s["shipping_method"] == code for s in shipping):
            raise CampaignAdminError(
                f"shipping code {code!r} given twice; the Campaigns API allows one campaign shipping method "
                "per store code. A different price per bundle needs a different store shipping method, "
                "or one method plus a shipping discount offer")
        if any(ship_key(s) == ship_key(entry) for s in shipping):
            raise CampaignAdminError(f"shipping key {ship_key(entry)!r} given twice")
        shipping.append(entry)

    # bumps / upsells: explicit variant, price, pct; nothing inferred
    variant_index = {v["id"]: (p, v) for p in discovery["products"] for v in p["variants"]}
    seen_upsell_variants = set()

    def add_extra(spec: str, role: str):
        """(package, pct, title, reused). An upsell reuses any hero or bump package
        for the same variant at the same price instead of creating a twin. Every
        bump is built before any upsell (they are separate argument lists), so the
        flag order on the command line does not matter. A bump always gets its own
        package so it never counts toward hero tiers."""
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
        price = money(D(parts[1]))
        # Validated here, before the reuse check compares it: NaN or Infinity would
        # otherwise escape as an InvalidOperation traceback.
        pct = Decimal(whole_pct(parts[2], "upsell")) if role == "upsell" else None
        if role == "bump":
            held = next((x for x in packages if x.get("product_variant_ids") == [vid]), None)
            if held is not None:
                raise CampaignAdminError(
                    f"bump variant {vid} is already package {held['key']}; the Campaigns API allows one "
                    "package per variant, and a bump on a hero variant would count toward the hero "
                    "quantity tiers. Choose a different variant or product for the bump")
        if role == "upsell":
            # Tracked apart from package roles: a reused hero or bump package keeps its
            # role, so a role check could never see the second occurrence.
            if vid in seen_upsell_variants:
                raise CampaignAdminError(f"upsell variant {vid} given twice; pass each upsell variant once")
            seen_upsell_variants.add(vid)
            # Match the (variant, price) pair across every package, not the variant
            # first: a differently priced hero must not hide an exact bump match.
            same = next((x for x in packages if x.get("product_variant_ids") == [vid]
                         and D(x["price"]) == D(price)), None)
            if same is not None:
                rationale.append(f"Upsell {title} ({v.get('title')}) reuses package {same['key']}: same variant "
                                 "at the same price, so no second package is created.")
                return same, pct, title, True
            held = next((x for x in packages if x.get("product_variant_ids") == [vid]), None)
            if held is not None:
                # One package per variant: the price difference has to come from the
                # voucher. Suggest the whole percentage that lands nearest the target.
                target = landed_unit(D(price), pct, None) if Decimal(0) < pct < Decimal(100) else None
                hint = ""
                if target is not None and D(held["price"]) > target:
                    exact = (D(held["price"]) - target) / D(held["price"]) * 100
                    whole = int(exact.to_integral_value(rounding=ROUND_HALF_UP))
                    if 0 < whole < 100:
                        hint = (f" To land near {money(target)}, pass --upsell {vid}:{held['price']}:{whole} "
                                f"(lands at {money(landed_unit(D(held['price']), Decimal(whole), None))}).")
                raise CampaignAdminError(
                    f"upsell variant {vid} is already package {held['key']} at {held['price']}; the Campaigns "
                    f"API allows one package per variant, so the upsell reuses that package at its price and "
                    f"the voucher percentage sets the upsell price.{hint}")
        pkg = {"key": f"{role}-{vid}", "role": role, "name": title, "variant_title": v.get("title"),
               "product_id": p["id"], "product_variant_ids": [vid], "price": price}
        packages.append(pkg)
        return pkg, pct, title, False

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

    offer_kind = getattr(a, "offer_type", None) or "quantity"
    if offer_kind not in OFFER_KINDS:
        raise CampaignAdminError(f"--offer-type must be one of {', '.join(OFFER_KINDS)}; got {offer_kind!r}")
    paid_qty = getattr(a, "paid_qty", None)
    free_qty = getattr(a, "free_qty", None)
    gift_specs = getattr(a, "gift", None) or []
    gift_mode = getattr(a, "gift_mode", None) or "auto"
    if gift_mode not in ("auto", "select"):
        raise CampaignAdminError("--gift-mode must be auto (funnel silent-add) or select (customer picks)")

    if offer_kind == "quantity":
        if paid_qty is not None or free_qty is not None:
            raise CampaignAdminError(
                "quantity offers use --tiers (Buy 1/2/3 percentages), not --paid-qty/--free-qty; "
                "pass --offer-type bxgy for a buy-X-get-Y approximation")
    elif offer_kind == "bxgy":
        if a.ctc == "high":
            raise CampaignAdminError(
                "buy-X-get-Y is a quantity structure; --ctc high refuses quantity deals. "
                "Use --ctc low, or --offer-type quantity for a high-CTC single-unit plan")
        if a.tiers is not None:
            raise CampaignAdminError(
                "--tiers is the quantity-offer ladder and cannot be combined with --offer-type bxgy: "
                "the engine applies the highest matching package_percentage, so a Buy 1 percentage "
                "would mask a lower BXGY rate. Omit --tiers for Buy 1 at list plus the BXGY offer")
        if paid_qty is None or free_qty is None:
            raise CampaignAdminError("--offer-type bxgy requires --paid-qty and --free-qty")
        bxgy_percentage(paid_qty, free_qty)
    else:
        if not gift_specs:
            raise CampaignAdminError(
                "--offer-type gwp requires at least one --gift <variant_id>:<price>[:<qty>]")
        if paid_qty is not None or free_qty is not None:
            raise CampaignAdminError(
                "gift-with-purchase cannot discount a different product based on hero quantity "
                "(condition and benefit share one package list). Use --offer-type bxgy for "
                "same-product buy-X-get-Y, or --offer-type quantity --gift ... to add a gift "
                "beside quantity tiers")
        if a.tiers is not None:
            raise CampaignAdminError(
                "--tiers is for --offer-type quantity; to combine quantity tiers with a gift, "
                "use --offer-type quantity and pass --gift")

    if offer_kind == "quantity":
        tiers = [whole_pct(t, "tier") for t in (a.tiers.split(",") if a.tiers else DEFAULT_TIERS)]
        if a.ctc == "low":
            rationale.append("Low CTC: one package per variant at the anchor price; Buy 1/2/3 automatic tier offers "
                             "(offer doctrine: Cost-to-consumer). Tiers are offers, never quantity packages "
                             "(offer doctrine: Naming and scoping).")
            for qty, pct in enumerate(tiers, start=1):
                unit = check_rounding_discount(f"{hero_title} - Buy {qty} - {pct}%", anchor, D(pct), rounding)
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
    elif offer_kind == "bxgy":
        pct = D(money(bxgy_percentage(paid_qty, free_qty)))
        total_qty = paid_qty + free_qty
        unit = check_rounding_discount(
            f"{hero_title} - Buy {paid_qty} get {free_qty} free (~{money(pct)}%)", anchor, pct, rounding)
        if unit <= 0:
            raise CampaignAdminError(
                f"buy {paid_qty} get {free_qty} free at {money(pct)}% yields a non-positive unit price ({unit}); "
                "lower the free quantity or raise the anchor")
        landed_total = unit * total_qty
        paid_total = anchor * paid_qty
        if landed_total == paid_total:
            match = (f"At exactly {total_qty} equal-priced units it lands at {money(landed_total)}, which "
                     f"matches paying for the paid quantity; it is not Nth-unit-free.")
        else:
            match = (f"At exactly {total_qty} equal-priced units it lands at {money(landed_total)}, not the "
                     f"{money(paid_total)} that paying for {paid_qty} at the anchor would cost: the percentage is held to "
                     "2 decimals, the discount is rounded to cents, and price_rounding (when set) moves the "
                     "unit price. Customer copy must quote the landed total, not 'for the price of "
                     f"{paid_qty}'. It is not Nth-unit-free.")
        rationale.append(
            f"Buy {paid_qty} get {free_qty} free is an approximation: the Campaigns App has no free-unit "
            f"benefit, so this is a count-{total_qty} automatic offer at {money(pct)}% off every in-scope unit "
            f"(offer doctrine: Buy-X-get-Y approximation). " + match)
        rationale.append(
            "Which unit is free is not merchant-chosen: the engine applies the same percentage to every "
            "matching unit, including mixed-price variants (proportional-off-all, not cheapest-free or "
            "most-expensive-free).")
        extra_qty = total_qty + 1
        extra_engine = unit * extra_qty
        extra_true = true_bxgy_payable(anchor, paid_qty, free_qty, extra_qty)
        rationale.append(
            f"The offer does not repeat per extra qualifying set. Qty {extra_qty} still gets {money(pct)}% off "
            f"all {extra_qty} units (engine {money(extra_engine)}) rather than one complete deal plus leftover "
            f"units at full price (true repeating BOGO {money(extra_true)}).")
        # Buy 1 at list uses the standard landed shape (no BXGY columns); only the
        # deal and over-qty rows carry paid_qty / free_qty / approximation fields.
        landed.append({"tier": "Buy 1", "kind": "single", "qty": 1, "offer_key": None,
                       "package_keys": list(hero_keys),
                       "anchor": money(anchor), "pct": 0,
                       "unit_after": money(anchor), "order_total": money(anchor)})
        offer_key = f"bxgy-{paid_qty}-{free_qty}" if offers_supported is not False else None
        landed.append(_bxgy_landed_row(
            f"Buy {paid_qty} get {free_qty} free", "tier", total_qty, offer_key,
            hero_keys, anchor, pct, unit, paid_qty, free_qty))
        landed.append(_bxgy_landed_row(
            f"Buy {extra_qty} (engine; not true repeat)", "tier", extra_qty, offer_key,
            hero_keys, anchor, pct, unit, paid_qty, free_qty,
            note=f"engine applies {money(pct)}% to all units once count {total_qty} is met"))
        if offers_supported is not False:
            offers.append({
                "key": offer_key,
                "name": f"{hero_title} - Buy {paid_qty} get {free_qty} free (~{money(pct)}%)",
                "offer_type": "offer", "code": None,
                "condition": {"type": "count", "value": total_qty, "package_keys": list(hero_keys)},
                "benefit": {"type": "package_percentage", "value": money(pct), "price_rounding": rounding},
            })
    else:
        rationale.append(
            "Gift with purchase: the hero sells at the anchor with no quantity-tier percentages. "
            "A 100% package_percentage scoped only to the gift package makes the gift free when it is "
            "in the cart. The Offers API cannot key that on hero quantity or spend, cannot auto-add or "
            "auto-remove the gift, and cannot discount the gift based on a different product "
            "(offer doctrine: Gift with purchase).")
        if a.ctc == "low":
            rationale.append(
                "GWP at low CTC: no Buy 1/2/3 ladder on the hero; the gift package is the only "
                "offer-engine hook for this offer type. To keep quantity tiers beside a gift, use "
                "--offer-type quantity --gift ...")
        landed.append({"tier": "Buy 1", "kind": "single", "qty": 1, "offer_key": None,
                       "package_keys": list(hero_keys),
                       "anchor": money(anchor), "pct": 0,
                       "unit_after": money(anchor), "order_total": money(anchor)})

    # A free-shipping threshold is only worth emitting if verify can pin it: it
    # needs a landed row at exactly N units and one at N - 1 (see
    # _free_shipping_coverage). Tier rows run Buy 1..len(tiers) with no gaps, so
    # N in [2, top] guarantees both.
    min_qty = a.free_shipping_min_qty
    if min_qty is not None:
        if a.free_shipping:
            raise CampaignAdminError("pass --free-shipping (every order) or --free-shipping-min-qty N (Buy N+), "
                                     "not both")
        if type(min_qty) is not int or min_qty < 2:
            raise CampaignAdminError("--free-shipping-min-qty must be a whole number >= 2; "
                                     "use --free-shipping alone for free shipping on every order")
        top = max(l["qty"] for l in landed)
        if min_qty > top:
            covered = f"Buy 1..{top}" if top > 1 else "Buy 1"
            raise CampaignAdminError(
                f"--free-shipping-min-qty {min_qty} cannot be verified: landed rows only cover {covered}; "
                "add tiers, lower it, or hand-author the plan and prove it with your own calculate probes")
    free_ship = bool(a.free_shipping) or min_qty is not None

    for spec in a.bump or []:
        pkg, _, _, _ = add_extra(spec, "bump")
        rationale.append(f"Checkout bump {pkg['name']} at {pkg['price']} (operator-specified).")

    # Upsells: one voucher per (product, percentage), scoped to every variant package
    # of that product at that percentage.
    groups = {}
    for spec in a.upsell or []:
        pkg, pct, title, reused = add_extra(spec, "upsell")
        pct = whole_pct(pct, "upsell")
        g = groups.setdefault((pkg["product_id"], pct), {"title": title, "pct": pct, "items": []})
        g["items"].append((pkg, reused))
    groups_per_product = {}
    for (pid, _), g in groups.items():
        groups_per_product[pid] = groups_per_product.get(pid, 0) + 1
    products_per_title = {}
    for (pid, _), g in groups.items():
        products_per_title.setdefault(g["title"], set()).add(pid)

    # Voucher codes are {SHORT NAME}{PCT}. A stem is resolved only for a product
    # whose voucher is actually generated, so an unused hero or an offers-less
    # store never trips the empty-stem error. Two products that land on the same
    # stem or the same code stop the run: the operator picks a --short-name.
    stem_owner, code_owner, stems, voucher_codes = {}, {}, {}, []

    def stem_for(pid, title):
        if pid not in stems:
            source = "short-name" if pid in short_names else "generated"
            stem = short_names.get(pid) or code_stem(title)
            prev = stem_owner.get(stem)
            if prev is not None and prev[0] != pid:
                raise CampaignAdminError(
                    f"products {prev[0]} ({prev[1]!r}) and {pid} ({title!r}) both shorten to voucher code "
                    f"name {stem}; pass --short-name <product_id>:<NAME> for one of them")
            stem_owner[stem] = (pid, title)
            stems[pid] = (stem, source)
            rationale.append(f"Voucher code name {stem} for {title}" +
                             (" (--short-name)." if source == "short-name" else "."))
        return stems[pid]

    def claim_code(pid, title, code):
        prev = code_owner.get(code)
        if prev is not None and prev[0] != pid:
            raise CampaignAdminError(
                f"products {prev[0]} ({prev[1]!r}) and {pid} ({title!r}) would both get voucher code {code}; "
                "pass --short-name <product_id>:<NAME> for one of them")
        code_owner[code] = (pid, title)

    upsell_codes = set()
    for (pid, pct), g in groups.items():
        title, items = g["title"], g["items"]
        key = f"upsell-{pid}-{pct}"
        # Two upsold products can share a title; fold the product id into the
        # offer name only then, so names stay unique in the campaign.
        twin = len(products_per_title[title]) > 1
        offer_name = f"{title} ({pid}) - {pct}%" if twin else f"{title} - {pct}%"
        code = None
        if offers_supported is not False:
            stem, source = stem_for(pid, title)
            code = code_from(stem, pct)
            claim_code(pid, title, code)
            upsell_codes.add(code)
            voucher_codes.append({"offer_key": key, "product_id": pid, "title": title,
                                  "short_name": stem, "generated_code": code, "source": source})
        solo = (groups_per_product[pid] == 1 and len(items) == 1
                and len(products_per_title[title]) == 1)
        who = f"{title} ({pid})" if twin else title
        for pkg, _ in items:
            unit = check_rounding_discount(f"{offer_name} ({pkg['key']})", D(pkg["price"]), Decimal(pct), rounding)
            # Labels become verify case names, so they must be unique: the bare product
            # name only when this product has exactly one upsell package in the plan.
            label = (f"Upsell {title}" if solo else
                     f"Upsell {pkg.get('variant_title') or pkg['name']} - {pct}% ({pkg['key']})")
            landed.append({"tier": label, "kind": "upsell", "qty": 1,
                           "offer_key": key if offers_supported is not False else None,
                           "package_keys": [pkg["key"]],
                           "anchor": pkg["price"], "pct": pct,
                           "unit_after": money(unit), "order_total": money(unit)})
        keys = [pkg["key"] for pkg, _ in items]
        if offers_supported is not False:
            offers.append({
                "key": key, "name": offer_name,
                "offer_type": "voucher", "code": code,
                "condition": {"type": "any", "value": None, "package_keys": keys},
                "benefit": {"type": "package_percentage", "value": money(D(pct)), "price_rounding": rounding},
            })
        voucher = f"one voucher {code}" if code else "one voucher (not created: offers unavailable)"
        rationale.append(f"Upsell {who}: {voucher} scoped to {len(keys)} variant package(s) {keys}; "
                         "variants at one percentage share one voucher (site offers do not apply post-purchase).")
        shared = [pkg["key"] for pkg, reused in items if reused]
        if shared and code:
            handoff.append(
                f"Upsell voucher {code} is scoped to {shared}, which are also sold on the checkout page. "
                "A voucher works on every page, so the code also applies at checkout if a shopper enters "
                "it there, stacking on any checkout offer. Never show this code on the checkout page.")
    # Surface a partial or split upsell rather than let it pass quietly: the operator
    # chooses which variants to upsell, so this informs instead of refusing.
    for pid in groups_per_product:
        pcts = sorted(pct for (gp, pct) in groups if gp == pid)
        title = next(g["title"] for (gp, _), g in groups.items() if gp == pid)
        if len(pcts) > 1:
            rationale.append(f"Upsell {title} is split across {len(pcts)} vouchers because its variants were "
                             f"given different percentages ({pcts}). Give them one percentage for one voucher.")
        product = next((x for x in discovery["products"] if x["id"] == pid), None)
        if product:
            buyable = {v["id"] for v in product["variants"]
                       if v.get("purchase_availability", "available") == "available"}
            given = {vid for (gp, _), g in groups.items() if gp == pid
                     for pkg, _ in g["items"] for vid in pkg["product_variant_ids"]}
            if buyable - given:
                rationale.append(f"Upsell {title} covers {len(given & buyable)} of its {len(buyable)} purchasable "
                                 f"variants; variants {sorted(buyable - given)} are not in any upsell voucher.")
    upsell_labels = [l["tier"] for l in landed if l["kind"] == "upsell"]
    if len(upsell_labels) != len(set(upsell_labels)):
        raise CampaignAdminError(f"upsell landed labels are not unique: {upsell_labels} (bug)")

    gift_keys = []
    for spec in gift_specs:
        bits = spec.split(":")
        if len(bits) not in (2, 3):
            raise CampaignAdminError(f"--gift expects <variant_id>:<price>[:<qty>], got {spec!r}")
        try:
            vid = int(bits[0])
        except ValueError:
            raise CampaignAdminError(f"--gift variant id must be an integer; got {bits[0]!r}")
        if not DECIMAL_RE.match(bits[1]):
            raise CampaignAdminError(f"--gift price {bits[1]!r} is not a decimal")
        gift_price = D(bits[1])
        if gift_price <= 0:
            raise CampaignAdminError(f"--gift price must be greater than 0; got {bits[1]!r}")
        qty = 1
        if len(bits) == 3:
            try:
                qty = int(bits[2])
            except ValueError:
                raise CampaignAdminError(f"--gift qty must be an integer >= 1; got {bits[2]!r}")
            if qty < 1:
                raise CampaignAdminError(f"--gift qty must be an integer >= 1; got {bits[2]!r}")
        if vid not in variant_index:
            raise CampaignAdminError(f"gift variant {vid} not found in discovery")
        p, v = variant_index[vid]
        if v.get("purchase_availability", "available") != "available":
            raise CampaignAdminError(
                f"gift variant {vid} is not purchasable "
                f"(purchase_availability={v.get('purchase_availability')!r}); pick an available variant")
        used = {x for pkg in packages for x in (pkg.get("product_variant_ids") or [])}
        if vid in used:
            raise CampaignAdminError(
                f"gift variant {vid} is already a hero, bump, upsell or gift package; "
                "the 100% gift offer must not share a variant with a paid item")
        title = short_name(p["title"])
        pkg = {"key": f"gift-{vid}", "role": "gift", "name": title, "variant_title": v.get("title"),
               "product_id": p["id"], "product_variant_ids": [vid], "price": money(gift_price)}
        packages.append(pkg)
        gift_keys.append(pkg["key"])
        unit = landed_unit(gift_price, Decimal(100), None)
        landed.append({"tier": f"Gift {title}", "kind": "single", "qty": qty,
                       "offer_key": "gift-free" if offers_supported is not False else None,
                       "package_keys": [pkg["key"]],
                       "anchor": pkg["price"], "pct": 100,
                       "unit_after": money(unit), "order_total": money(unit * qty)})
        rationale.append(f"Gift package {title} at {pkg['price']}; a 100% package_percentage scoped only "
                         "to this package makes it free when it is in the cart "
                         "(offer doctrine: Gift with purchase).")
    if gift_keys:
        if offers_supported is not False:
            offers.append({
                "key": "gift-free", "name": f"{hero_title} - Gift free",
                "offer_type": "offer", "code": None,
                "condition": {"type": "any", "value": None, "package_keys": list(gift_keys)},
                "benefit": {"type": "package_percentage", "value": "100.00", "price_rounding": None},
            })
        if rounding:
            rationale.append("The gift offer omits price_rounding even though --rounding was set: "
                             "100% with charm rounding would land at the charm cents, not free.")
        if gift_mode == "auto":
            handoff.append(
                "Gift auto-add is funnel work, not an Offers API field. In next-campaigns-setup, add "
                "each gift package as a bundle item with \"noSlot\": true so the SDK silently adds it. "
                "This skill does not write funnel markup. The engine will not remove the gift if the "
                "shopper no longer qualifies; qualification is only that the gift is in the cart.")
        else:
            handoff.append(
                "Gift selection is funnel work: show the gift package as a visible choice (not a silent "
                "noSlot add). This skill does not write funnel markup. The 100% offer fires whenever "
                "the gift package is in the cart, whether or not the hero is present.")
        handoff.append(
            "Campaigns App cannot key a gift on hero quantity or spend, cannot auto-add or auto-remove "
            "it from the offer engine, and cannot discount a different product than the one that "
            "qualified. Min-spend GWP is a platform gap. How a 100%-off line appears on orders, "
            "fulfilment and refunds versus a true free gift is unverified; do not invent accounting rules.")

    def _is_zero(x):
        try:
            return D(x) == 0
        except CampaignAdminError:
            return False
    exit_pct = DEFAULT_EXIT_PCT if a.exit is None else (0 if _is_zero(a.exit) else whole_pct(a.exit, "exit"))
    if exit_pct and offers_supported is not False:
        if a.exit_code:
            exit_code, exit_stem, exit_source = a.exit_code, short_names.get(hero["id"]), "exit-code"
        else:
            exit_stem, exit_source = stem_for(hero["id"], hero_title)
            exit_code = code_from(exit_stem, exit_pct)
        if exit_code in upsell_codes:
            raise CampaignAdminError(
                f"the exit voucher code {exit_code} is also an upsell voucher code (an upsell of the hero "
                f"product at {exit_pct}%); pass a short --exit-code such as SAVE{exit_pct}")
        claim_code(hero["id"], hero_title, exit_code)
        voucher_codes.append({"offer_key": "exit-pop", "product_id": hero["id"], "title": hero_title,
                              "short_name": exit_stem, "generated_code": exit_code, "source": exit_source})
        offers.append({
            "key": "exit-pop", "name": f"{hero_title} - Exit - {exit_pct}%",
            "offer_type": "voucher", "code": exit_code,
            "condition": {"type": "any", "value": None, "package_keys": list(hero_keys)},
            "benefit": {"type": "package_percentage", "value": money(D(exit_pct)), "price_rounding": rounding},
        })
        # The exit voucher stacks on the tier price, so every hero row it can meet
        # must still come out below that row's price. Gift rows are out of scope.
        for row in landed:
            if row["kind"] in ("tier", "single") and set(row["package_keys"]) <= set(hero_keys):
                check_rounding_discount(f"{hero_title} - Exit - {exit_pct}% on {row['tier']}",
                                        D(row["unit_after"]), D(exit_pct), rounding)
        rationale.append(f"Exit-pop voucher for an additional {exit_pct}% applies on top of the tier price "
                         "(offer doctrine: Rounding and stacking).")
    if free_ship:
        rule = "every order" if min_qty is None else f"Buy {min_qty}+"
        if offers_supported is not False:
            if min_qty is None:
                name = f"{hero_title} - Free Shipping"
                cond = {"type": "any", "value": None, "package_keys": list(hero_keys)}
                rationale.append("Free shipping on every checkout order (automatic offer on the hero packages).")
            else:
                name = f"{hero_title} - Free Shipping - Buy {min_qty}+"
                cond = {"type": "count", "value": min_qty, "package_keys": list(hero_keys)}
                checkout = [l for l in landed if l["kind"] in ("tier", "single")]
                free = ", ".join(l["tier"] for l in checkout if l["qty"] >= min_qty) or "none"
                paid = ", ".join(l["tier"] for l in checkout if l["qty"] < min_qty) or "none"
                rationale.append(f"Free shipping on Buy {min_qty}+ (count condition on the hero packages): "
                                 f"ships free: {free}; pays shipping: {paid}. Upsells carry no shipping.")
            offers.append({
                "key": "free-shipping", "name": name,
                "offer_type": "offer", "code": None,
                "condition": cond,
                "benefit": {"type": "shipping_percentage", "value": "100.00", "price_rounding": None},
            })
        else:
            # verify refuses a plan whose hash changed after apply, so a dashboard
            # offer can never be folded back into this plan; it is proven by hand.
            probe = "1 unit" if min_qty is None else f"{min_qty - 1} and {min_qty} units"
            handoff.append(f"Free shipping requested ({rule}): create the automatic 100% shipping offer on the "
                           "hero packages in the dashboard (Offers & Discounts), then prove it with "
                           f"carts/calculate probes at {probe}.")
            rationale.append(f"Free shipping ({rule}) is not in this plan because the Offers API is unavailable; "
                             "verify will expect paid shipping on every checkout case, so the dashboard offer "
                             "is proven by hand probes, not by verify.")

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
    plan_name = a.name or hero_title
    if any(c.get("name") == plan_name for c in discovery.get("campaigns", [])):
        blockers.append(f"A campaign named {plan_name!r} already exists on the store; choose another name "
                        "with --name.")

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
    for pid in sorted(set(short_names) - {v["product_id"] for v in voucher_codes if v["source"] == "short-name"}):
        rationale.append(f"--short-name {pid}:{short_names[pid]} was not used: product {pid} gets no generated "
                         "voucher code in this plan (not an upsell, or not the hero of a generated exit code).")
    return {
        "store_slug": discovery["store_slug"],
        "store_origin": discovery["store_origin"],
        "generated_at": utcnow(),
        "ctc": a.ctc,
        "offer_kind": offer_kind,
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
        "voucher_codes": voucher_codes,
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


def validate_plan(plan: dict, for_create: bool = True) -> list:
    """Every problem with a plan as a list of messages. A hand-edited plan with the
    wrong shape somewhere is reported, never allowed to escape as a traceback, and
    an empty list is the only answer that lets a caller proceed.

    for_create adds the Campaigns API's uniqueness rules: one package per variant
    and one campaign shipping method per store code. recommend, plan and apply
    check them; verify does not, so a run created before those rules can still be
    read back and priced."""
    try:
        errs = _validate_plan(plan)
        return errs + (_uniqueness_errors(plan) if for_create else [])
    except (TypeError, AttributeError, KeyError, ValueError, InvalidOperation) as e:
        return [f"plan is malformed ({type(e).__name__}: {e}); regenerate it with recommend "
                "or correct the field by hand"]


def _uniqueness_errors(plan: dict) -> list:
    if not isinstance(plan, dict):
        return []
    errs, by_variant, by_code = [], {}, {}
    for p in plan.get("packages", []):
        for vid in p.get("product_variant_ids") or []:
            if vid in by_variant:
                errs.append(f"packages {by_variant[vid]} and {p.get('key')} both use variant {vid}; the Campaigns "
                            "API allows one package per variant. Reuse the one package and set the price "
                            "difference with an offer or voucher")
            else:
                by_variant[vid] = p.get("key")
    for sm in plan.get("shipping_methods", []):
        code = sm.get("shipping_method")
        if code in by_code:
            errs.append(f"shipping methods {by_code[code]} and {ship_key(sm)} both use store code {code!r}; the "
                        "Campaigns API allows one campaign shipping method per store code")
        elif code:
            by_code[code] = ship_key(sm)
    return errs


def _validate_plan(plan: dict) -> list:
    if not isinstance(plan, dict):
        return ["plan must be a JSON object"]
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
    sd = c.get("statement_descriptor")
    if sd and (not isinstance(sd, str) or len(sd) > 255):
        errs.append("campaign.statement_descriptor must be a string of at most 255 characters")
    if type(c.get("payment_gateway_group_id")) is not int:
        errs.append("campaign.payment_gateway_group_id must be an integer")

    keys = set()
    for p in plan.get("packages", []):
        k = p.get("key")
        if not k or k in keys:
            errs.append(f"package key missing or duplicate: {k!r}")
        keys.add(k)
        vids = p.get("product_variant_ids") or []
        if not isinstance(vids, list) or len(vids) != 1 or type(vids[0]) is not int:
            errs.append(f"package {k}: product_variant_ids must be exactly one integer (got {vids!r}); "
                        "the API creates one package per variant id and only the first is journalled")
        pname = p.get("name")
        if not isinstance(pname, str) or not pname or len(pname) > 200:
            errs.append(f"package {k}: name must be a non-empty string of at most 200 characters")
        elif re.match(r"^\d+\s*x\s", pname, re.I):
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
    # The plan-level key (default: the code) is the identity; the store returns no
    # key. Keys are deduped here on every path, so a plan from before the
    # one-method-per-code rule that repeats a code only validates because each
    # entry carries its own key and its own price.
    ship_keys, ship_code_prices = {}, set()
    for s in plan.get("shipping_methods", []):
        code = s.get("shipping_method")
        if not code:
            errs.append("shipping_method code is required")
        if "key" in s and (not isinstance(s["key"], str) or not s["key"]):
            errs.append(f"shipping {code}: key must be a non-empty string")
            continue
        k = ship_key(s)
        if k in ship_keys:
            first = ship_keys[k]
            if "key" not in s and "key" not in first:
                hint = f" (give each entry on code {code!r} its own key)"
            else:
                hint = (f" (entries on code {first.get('shipping_method')!r} at {first.get('price')} and "
                        f"code {code!r} at {s.get('price')}; shipping keys must be unique)")
            errs.append(f"duplicate shipping key {k!r}{hint}")
        else:
            ship_keys[k] = s
        price = str(s.get("price", ""))
        if not DECIMAL_RE.match(price):
            errs.append(f"shipping {k}: price {s.get('price')!r} is not a decimal")
        elif code:
            if (code, D(price)) in ship_code_prices:
                errs.append(f"shipping {k}: duplicates code {code!r} at {price} (same code and price as another entry)")
            ship_code_prices.add((code, D(price)))

    names, codes, offer_keys = set(), set(), set()
    for o in plan.get("offers", []):
        k = o.get("key", "?")
        if not o.get("key") or k in offer_keys:
            errs.append(f"offer key missing or duplicate: {k!r}")
        offer_keys.add(k)
        n = o.get("name", "")
        if not isinstance(n, str) or not n or len(n) > 128:
            errs.append(f"offer {k}: name must be a non-empty string of at most 128 characters")
        elif n in names:
            errs.append(f"offer {k}: duplicate offer name {n!r}")
        else:
            names.add(n)
        ot = o.get("offer_type", "offer")
        if ot not in OFFER_TYPES:
            errs.append(f"offer {k}: offer_type {ot!r} invalid")
        if "offer_type" not in o and o.get("code"):
            # apply would send this as an automatic offer and drop the code, so a
            # voucher that forgot the field would silently fire on every cart
            errs.append(f"offer {k}: has a code but no offer_type; set offer_type to 'voucher' or remove the code")
        if ot == "voucher":
            code = o.get("code") or ""
            if not isinstance(code, str) or not CODE_RE.match(code):
                errs.append(f"offer {k}: voucher needs an uppercase alphanumeric code (<=64), got {code!r}")
            elif code in codes:
                errs.append(f"offer {k}: duplicate voucher code {code!r}")
            else:
                codes.add(code)
        cond = o.get("condition", {})
        if cond.get("type") not in CONDITION_TYPES:
            errs.append(f"offer {k}: condition.type {cond.get('type')!r} invalid")
        if cond.get("type") == "count" and not (type(cond.get("value")) is int and cond["value"] >= 1):
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
        if "available" in o and type(o["available"]) is not bool:
            errs.append(f"offer {k}: available must be true or false")

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
        sk = l.get("shipping_key")
        if sk is not None:
            if l.get("kind") == "upsell":
                errs.append(f"{tag}: upsell rows carry no shipping method; remove shipping_key")
            elif sk not in ship_keys:
                errs.append(f"{tag}: shipping_key {sk!r} does not resolve")
        if l.get("kind") == "upsell" and ok_ is not None:
            up = next((o for o in plan.get("offers", []) if o.get("key") == ok_), None)
            if up and up.get("offer_type") != "voucher":
                errs.append(f"{tag}: upsell offer {ok_!r} must be a voucher")
    return errs


def ship_key(s: dict):
    """Plan-level identity of a campaign shipping method: its `key`, else the store code."""
    return s.get("key") or s.get("shipping_method")


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
    kind = plan.get("offer_kind") or "quantity"
    print(f"Campaign: {c['name']}  {c['currency']}/{c['language']}  gateway group {c['payment_gateway_group_id']}  store {plan['store_origin']}  offer {kind}")
    print(f"  payment methods: {c['available_payment_methods']}  express: {c['available_express_payment_methods']}  countries: {c['available_shipping_countries'] or 'all'}")
    print("Packages:")
    for p in plan["packages"]:
        img_spec = p.get("image")
        img = f"  image {img_spec['src']}" if isinstance(img_spec, dict) and img_spec.get("src") else ""
        print(f"  {p['key']:<14} {p['role']:<7} {p['name']}  variant {p['product_variant_ids']} ({p.get('variant_title')})  price {p['price']}{img}")
    print("Shipping methods:")
    for s in plan["shipping_methods"]:
        print(f"  {s['shipping_method']}  {s['price']}" + (f"  key {s['key']}" if s.get("key") else ""))
    print("Offers:")
    for o in plan.get("offers", []):
        cond = o["condition"]
        crit = f"qty>={cond['value']}" if cond["type"] == "count" else "any"
        code = f" code={o['code']}" if o.get("offer_type") == "voucher" else ""
        print(f"  {o['key']:<14} {o.get('offer_type','offer'):<7} {o['name']}  {crit} on {cond['package_keys']}  {o['benefit']['type']} {o['benefit']['value']}%{code}")
    vouchers = [o for o in plan.get("offers", []) if o.get("offer_type") == "voucher"]
    if vouchers:
        print("Voucher codes (override with --short-name <product_id>:<NAME> or --exit-code):")
        raw = plan.get("voucher_codes")
        prov = {v.get("offer_key"): v for v in (raw if isinstance(raw, list) else []) if isinstance(v, dict)}
        for o in vouchers:
            v = prov.get(o["key"])
            if v is None:
                print(f"  {o['key']:<14} {o.get('code')}  source unknown")
                continue
            source = v.get("source") if v.get("generated_code") == o.get("code") else "edited in plan"
            name = v.get("short_name") or "-"
            print(f"  {o['key']:<14} {o.get('code')}  {v.get('title')} ({v.get('product_id')})  "
                  f"short name {name}  source {source}")
    print("Landed prices (confirm these):")
    for l in plan["landed_prices"]:
        extra = ""
        if l.get("paid_qty") is not None and l.get("free_qty") is not None and l.get("full_retail") is not None:
            extra = (f"  paid {l['paid_qty']} free {l['free_qty']}  retail {l['full_retail']} "
                     f"payable {l.get('payable')}  save {l.get('savings')}")
        if l.get("note"):
            extra += f"  ({l['note']})"
        print(f"  {l['tier']:<28} qty {l['qty']}  anchor {l['anchor']}  -{l['pct']}%  unit {l['unit_after']}  "
              f"total {l['order_total']}  {_landed_shipping(plan, l)}{extra}")
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
        raise CampaignAdminError(f"{what}: {method} {path} returned {status}: {_short(resp)}"
                                 + auth_hint(status, method, path))
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
    plan_sm = {ship_key(s): s for s in plan["shipping_methods"]}
    currency = plan["campaign"]["currency"]
    for e in man.data["shipping_methods"]:
        if e.get("status") != "pending":
            continue
        s = plan_sm.get(e["key"])
        if s is None:
            raise CampaignAdminError(f"manifest shipping entry {e['key']} is not in the plan")
        # The store returns no plan key, so a lost response is claimed by code and
        # price. Entries already journalled under another key are never candidates
        # (recomputed per entry, so one claimed a moment ago is excluded too).
        claimed = {x.get("id") for x in man.data["shipping_methods"]
                   if x.get("status") == "created" and x.get("id") is not None}
        cands = [x for x in ships if x.get("shipping_method") == s["shipping_method"] and x.get("id") not in claimed]
        if not cands:
            # the only path that lets apply POST this entry again
            man.mark("shipping_methods", e["key"], status="absent")
            continue
        # Anything short of one exact price match among readable prices stops: a
        # re-POST from here could leave a duplicate method on the campaign.
        read = [(x.get("id"), _finite(_num(_price_in(x, currency)))) for x in cands]
        priced = [i for i, p in read if p is not None and p == D(s["price"])]
        if len(priced) == 1 and all(p is not None for _, p in read):
            man.mark("shipping_methods", e["key"], status="created", id=priced[0], reconciled=True, intent=None)
        else:
            seen = ", ".join(f"{i} at {'unreadable' if p is None else money(p)}" for i, p in read)
            raise CampaignAdminError(
                f"pending shipping method {e['key']} ({s['shipping_method']} at {s['price']}) matches "
                f"{len(priced)} remote entries at that price; unclaimed entries on that code: {seen}; resolve by hand")
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


def create_offer(client: Client, man: Manifest, cid, o: dict, package_ids: dict, **journal) -> bool:
    """Journal and POST one planned offer (pending -> created). False when the
    store has no offers endpoint (404/405): the entry is journalled `unsupported`
    and nothing was created. Shared by apply and edit so both journal alike."""
    body = offer_body(o, package_ids)
    man.mark("offers", o["key"], status="pending", intent=body, **journal)
    path = f"/api/admin/campaigns/{cid}/offers/"
    status, resp = client.request("POST", path, body)
    if status in (404, 405):
        man.mark("offers", o["key"], status="unsupported", intent=None)
        return False
    if status not in (200, 201) or not isinstance(resp, dict):
        raise CampaignAdminError(f"create offer {o['key']}: POST returned {status}: {_short(resp)}"
                                 + auth_hint(status, "POST", path))
    man.mark("offers", o["key"], status="created", id=resp["id"], name=resp.get("name"), intent=None)
    print(f"created offer {resp['id']} {o['name']!r}")
    return True


TORN_DOWN_STATUSES = ("deleting", "deleted")


def torn_down_entries(man: Manifest) -> list:
    """Manifest entries teardown has started or finished deleting, as labels."""
    torn = []
    if man.data["campaign"].get("status") in TORN_DOWN_STATUSES:
        torn.append(f"campaign {man.data['campaign'].get('status')}")
    for section in ("packages", "shipping_methods", "offers"):
        for e in man.data.get(section, []):
            if e.get("status") in TORN_DOWN_STATUSES:
                torn.append(f"{section} {e.get('key')} {e['status']}")
    return torn


def apply(client: Client, plan: dict, plan_sha: str, manifest_path: Path, resume_path: Path | None) -> Manifest:
    errs = validate_plan(plan)
    if errs:
        raise CampaignAdminError("plan is invalid:\n  - " + "\n  - ".join(errs))
    if plan.get("blockers"):
        raise CampaignAdminError("plan has blockers; clear them and re-run recommend:\n  - " + "\n  - ".join(plan["blockers"]))
    # A new offer is live the moment it is created, so a paused offer can only be
    # one an edit paused after the fact. apply never creates one.
    paused = [o["key"] for o in plan.get("offers", []) if o.get("available") is False]

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
        if man.data.get("pending_edit"):
            raise CampaignAdminError(PENDING_EDIT_MSG + " before resuming.")
        if man.data["plan_sha256"] != plan_sha:
            raise CampaignAdminError("manifest plan_sha256 does not match this plan file; resume needs the same plan")
        not_live = [k for k in paused if (man.entry("offers", k) or {}).get("status") != "created"]
        if not_live:
            raise CampaignAdminError(
                f"plan pauses offer(s) {not_live} that this run has not created; resume creates offers "
                'live. Remove "available": false from them, or finish the build first.')
        torn = torn_down_entries(man)
        if torn:
            # Resume only finishes a build. Re-creating something teardown deleted, or
            # something whose DELETE never confirmed, would duplicate it or undo the
            # teardown, so a run teardown has touched is refused before any request.
            raise CampaignAdminError(
                f"this run was torn down, fully or in part ({', '.join(torn)}); resume cannot rebuild it. "
                "Re-run teardown to finish a partial one, then start a fresh run in a new directory "
                "(recommend --out <dir>).")
        if man.data["campaign"].get("status") == "created":
            live = client.get_ok(f"/api/admin/campaigns/{man.data['campaign']['id']}/")
            if live.get("name") != man.data["campaign"].get("name"):
                raise CampaignAdminError("manifest campaign name does not match the live campaign; refusing to resume")
            if not same_instant(live.get("created_at"), man.data["campaign"].get("created_at")):
                raise CampaignAdminError("manifest campaign created_at does not match the live campaign; refusing to resume")
        reconcile(client, man, plan)
    else:
        if paused:
            raise CampaignAdminError(
                f"plan has paused offer(s) {paused}; a fresh run creates every offer live. Remove "
                '"available": false, create the campaign, then pause with edit.')
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
            image_failures.append(f"{p['key']}: PUT returned {status}: {_short(resp)}"
                                  + auth_hint(status, "PUT", f"/api/admin/campaigns/{cid}/packages/{e['id']}/image/"))
        else:
            man.mark("packages", p["key"], image_status="set", image_intent=None,
                     image=resp.get("image"), image_error=None)
            image_set_ok = True
            print(f"set image on package {e['id']} {p['key']!r}")

    # shipping
    for s in plan["shipping_methods"]:
        key = ship_key(s)
        e = man.entry("shipping_methods", key)
        if e and e.get("status") == "created":
            continue
        body = {"shipping_method": s["shipping_method"], "price": s["price"]}
        man.mark("shipping_methods", key, status="pending", intent=body)
        resp = _created(client, "POST", f"/api/admin/campaigns/{cid}/shipping-methods/", body, f"create shipping {key}")
        man.mark("shipping_methods", key, status="created", id=resp["id"], intent=None)
        print(f"created shipping method {resp['id']} {key!r} ({s['shipping_method']} at {s['price']})")

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
        if not create_offer(client, man, cid, o, package_ids):
            offers_unsupported = True
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

# Identity of a journalled object against its live read-back. teardown and edit
# share these, so "is this the thing this run created" has one answer.

def campaign_identity_ok(live, camp: dict) -> bool:
    return (isinstance(live, dict) and live.get("name") == camp.get("name")
            and same_instant(live.get("created_at"), camp.get("created_at")))


def offer_identity_ok(e: dict, x: dict, plan_of: dict) -> bool:
    return x.get("name") == plan_of.get(e["key"], {}).get("name")


def package_identity_ok(e: dict, x: dict, plan_pk: dict) -> bool:
    return (x.get("product_variant_id") == e.get("product_variant_id")
            and x.get("name") == (e.get("name") or plan_pk.get(e["key"], {}).get("name")))


def shipping_identity_ok(e: dict, x: dict, plan_sm: dict, currency: str) -> bool:
    # The GET is by the journalled id; the code must match, and so must the
    # price whenever the store reports one in the campaign currency.
    s = plan_sm.get(e["key"], {})
    if not s or x.get("shipping_method") != s.get("shipping_method"):
        return False
    live_price = _price_in(x, currency)
    # A price that is present but unreadable (NaN, Infinity, garbage) is not a
    # match: teardown fails closed on it, as resume does.
    return live_price is None or _finite(_num(live_price)) == _num(s.get("price"))


PENDING_EDIT_MSG = ("an in-place edit of this run is unfinished: re-run the same edit to finish it, "
                    "or edit --undo <its receipt> to roll it back,")


def pending_edit_hashes(man: Manifest) -> set:
    """Plan hashes an unfinished edit can leave on disk: the plan it started
    from, the plan it is writing, and the plan a rollback of it writes."""
    pe = man.data.get("pending_edit") or {}
    hashes = {pe.get("old_plan_sha256"), pe.get("new_plan_sha256"), (pe.get("rollback") or {}).get("plan_sha256")}
    return {h for h in hashes if h}


def load_edit_receipt(man: Manifest, pe: dict) -> dict:
    """The receipt of the manifest's unfinished edit, from the manifest's own
    directory. It holds the before-images and the identity of anything the edit
    created, so nothing that depends on them proceeds without it."""
    path = man.path.parent / Path(str(pe.get("receipt") or "missing")).name
    try:
        receipt = load_json(path)
    except CampaignAdminError as exc:
        raise CampaignAdminError(f"an edit is unfinished but its receipt cannot be read ({exc}); "
                                 "restore that file before doing anything else with this run")
    if receipt.get("run_id") != man.data.get("run_id") or receipt.get("edit") != pe.get("edit"):
        raise CampaignAdminError(f"{path} is not the receipt of this run's unfinished edit")
    return receipt


def find_added_offer(client: Client, cid, man: Manifest, receipt: dict, name: str):
    """The live offer an edit created under `name`, for a lost POST response. An
    offer that was live before the edit, or that the manifest already owns, is
    never a candidate, so a dashboard offer cannot be claimed by name."""
    before = set(receipt.get("preexisting_offer_ids") or [])
    owned = {e.get("id") for e in man.data["offers"] if e.get("id") is not None}
    hits = [x for x in client.paginate(f"/api/admin/campaigns/{cid}/offers/")
            if x.get("name") == name and x.get("id") not in before and x.get("id") not in owned]
    if len(hits) > 1:
        raise CampaignAdminError(f"{len(hits)} live offers named {name!r} appeared since the edit began "
                                 f"({[x.get('id') for x in hits]}); resolve by hand")
    return hits[0] if hits else None


def teardown(client: Client, man: Manifest, plan: dict, plan_sha: str, confirm) -> None:
    check_origin_binding(man.data, "manifest")
    pe = man.data.get("pending_edit")
    accepted = {man.data["plan_sha256"]} | pending_edit_hashes(man)
    if man.data["store_slug"] != plan["store_slug"] or plan_sha not in accepted:
        raise CampaignAdminError("manifest does not belong to this plan (slug or plan hash differs)")
    camp = man.data["campaign"]
    if camp.get("status") not in ("created", "deleting", "deleted"):
        print("nothing to tear down: the campaign was never created")
        return
    cid = camp.get("id")
    plan_pk = {p["key"]: p for p in plan["packages"]}
    plan_of = {o["key"]: o for o in plan.get("offers", [])}
    plan_sm = {ship_key(s): s for s in plan["shipping_methods"]}
    currency = plan["campaign"]["currency"]
    # An unfinished edit may have created an offer the plan on disk does not know
    # yet. Its identity comes from the edit's receipt, read before any request.
    receipt = load_edit_receipt(man, pe) if pe else None
    for a in (receipt or {}).get("adds", []):
        plan_of.setdefault(a["key"], a["offer"])

    # Phase 1: read everything back and verify identity BEFORE any DELETE.
    todo = []  # (section, key, path)
    status, live = client.request("GET", f"/api/admin/campaigns/{cid}/")
    if status == 404:
        man.mark("campaign", "campaign", status="deleted")
        print(f"campaign {cid} already gone; nothing left to delete")
        return
    if status != 200 or not campaign_identity_ok(live, camp):
        raise CampaignAdminError(f"campaign {cid} identity mismatch (name/created_at); refusing to delete anything"
                                 + (f" (GET returned {status})" + auth_hint(status, "GET", f"/api/admin/campaigns/{cid}/")
                                    if status in (401, 403) else ""))

    for a in (receipt or {}).get("adds", []):
        e = man.entry("offers", a["key"])
        if e and not e.get("id") and e.get("status") == "pending":
            hit = find_added_offer(client, cid, man, receipt, a["offer"]["name"])
            if hit:
                man.mark("offers", a["key"], status="created", id=hit["id"], name=hit.get("name"), reconciled=True)
            else:
                man.mark("offers", a["key"], status="absent")

    for section, path_part, ident in (
        ("offers", "offers", lambda e, x: offer_identity_ok(e, x, plan_of)),
        ("shipping_methods", "shipping-methods", lambda e, x: shipping_identity_ok(e, x, plan_sm, currency)),
        ("packages", "packages", lambda e, x: package_identity_ok(e, x, plan_pk)),
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
                raise CampaignAdminError(f"{section} {e['key']} (id {e['id']}) identity mismatch; aborting before any delete"
                                         + (f" (GET returned {st})" + auth_hint(st, "GET", path) if st in (401, 403) else ""))
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
            raise CampaignAdminError(f"DELETE {path} returned {st}: {_short(body)}; rerun teardown to continue"
                                     + auth_hint(st, "DELETE", path))
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


def _finite(d: Decimal | None) -> Decimal | None:
    """d when it is a finite number, else None (NaN and Infinity parse as Decimals)."""
    return d if d is not None and d.is_finite() else None


def _price_in(obj: dict, currency: str):
    """The raw price for `currency` in an Admin API object's `prices` list, or None."""
    prices = obj.get("prices") if isinstance(obj, dict) else None
    return next((x.get("price") for x in (prices or []) if isinstance(x, dict) and x.get("currency") == currency), None)


def _offer_live(o: dict) -> bool:
    """False only for an offer an edit paused; a plan offer is live by default."""
    return o.get("available") is not False


def _condition_met(cond: dict, qty: int) -> bool:
    if cond.get("type") == "any":
        return True
    return cond.get("type") == "count" and type(cond.get("value")) is int and cond["value"] <= qty


def _voucher_applies(o: dict, keys: list, qty: int) -> bool:
    """Whether a voucher discounts a cart of `qty` units drawn from `keys`: it
    is live, its scope covers every one of them, and its condition is met."""
    cond = o.get("condition") or {}
    return _offer_live(o) and set(keys) <= set(cond.get("package_keys") or []) and _condition_met(cond, qty)


def _free_shipping_offers(plan: dict) -> list:
    """Automatic 100% shipping offers as (key, threshold, scope_keys); `any` is a
    threshold of 1. Vouchers are skipped: no cart case enters a shipping code.
    Reads defensively because print_plan calls it on plans not yet validated."""
    out = []
    for o in plan.get("offers") or []:
        ben, cond = o.get("benefit") or {}, o.get("condition") or {}
        if (o.get("offer_type", "offer") != "offer" or ben.get("type") != "shipping_percentage"
                or _num(ben.get("value")) != Decimal(100) or not _offer_live(o)):
            continue
        if cond.get("type") == "any":
            n = 1
        elif cond.get("type") == "count" and type(cond.get("value")) is int and cond["value"] >= 1:
            n = cond["value"]
        else:
            continue
        out.append((o.get("key"), n, frozenset(cond.get("package_keys") or [])))
    return out


def _in_scope(line_keys: list, scope) -> int:
    return sum(q for k, q in line_keys if k in scope)


def _ships_free(plan: dict, line_keys: list) -> bool:
    """Whether a free-shipping offer's condition is met by these (package_key,
    quantity) pairs: `any` needs one in-scope unit, `count` needs `value`."""
    return any(_in_scope(line_keys, scope) >= n for _, n, scope in _free_shipping_offers(plan))


def _partial_shipping_offers(plan: dict) -> list:
    """Automatic shipping_percentage offers below 100%, as frozenset scopes.
    Vouchers are skipped: no cart case enters a shipping code. A missing
    offer_type means 'offer', as it does in validate_plan and offer_body, so the
    gate describes what apply sends; validate_plan rejects the ambiguous case of
    a code with no offer_type. Reads defensively because print_plan calls it on
    plans not yet validated."""
    out = []
    for o in plan.get("offers") or []:
        if o.get("offer_type", "offer") != "offer" or not _offer_live(o):
            continue
        ben, cond = o.get("benefit") or {}, o.get("condition") or {}
        if ben.get("type") != "shipping_percentage" or _num(ben.get("value")) == Decimal(100):
            continue
        out.append(frozenset(cond.get("package_keys") or []))
    return out


def _landed_shipping(plan: dict, l: dict) -> str:
    """The plan gate's shipping column for one landed row. It has to hold for
    every variant mix the row allows, not for one sampled cart, and it is
    computed from the offers rather than stored on the row, so a hand-edited
    offer shows here too. Mixed-scope cases the two cheap rules cannot settle
    are reported as depending on the mix."""
    if l.get("kind") == "upsell":
        return "no shipping (post-purchase)"
    keys, qty = list(l.get("package_keys") or []), l.get("qty") or 0
    if any(n <= qty and all(k in scope for k in keys) for _, n, scope in _free_shipping_offers(plan)):
        return "shipping free"
    # Partial shipping discounts are not modelled here or in verify; say so
    # rather than print the full price for a row one of them may touch.
    keys_set = set(keys)
    if any(scope & keys_set for scope in _partial_shipping_offers(plan)):
        return "shipping partly discounted (not modelled; prove by hand)"
    # Every single-variant cart paying means every mix pays: no offer both
    # covers one of these keys and has a threshold within qty.
    if not any(_ships_free(plan, [(k, qty)]) for k in keys):
        ships = plan.get("shipping_methods") or [{}]
        sk = l.get("shipping_key")
        if sk is None:
            chosen = ships[0]
        else:
            # print_plan runs on plans that may not have passed validation yet, so an
            # unresolved key must say so rather than show another method's price.
            chosen = next((s for s in ships if ship_key(s) == sk), None)
            if chosen is None:
                return f"shipping ? (key {sk!r} unresolved)"
        return f"shipping {chosen.get('price', '?')}"
    return "shipping depends on variant mix"


CartCase = namedtuple("CartCase", "name lines vouchers expected ship line_keys shipping_key")
CartCase.__new__.__defaults__ = (None,)


def _cart_cases_from_plan(plan: dict, ids: dict) -> list:
    """Build carts/calculate cases straight from the structured landed_prices rows.

    Each case is a CartCase (name, lines, vouchers, expected_subtotal, ship,
    line_keys, shipping_key).
    `ship` is "paid" or "free" for a checkout cart, decided by whether that cart
    meets a free-shipping offer's condition, and "none" for an upsell voucher:
    a post-purchase upsell adds lines to a placed order and carries no shipping
    method. `line_keys` is the (package_key, quantity) list the cart was built
    from. `shipping_key` is the row's own shipping method, or None to use the
    campaign's first (always None for an upsell). Each row names its
    package_keys and offer_key, so nothing is parsed back out of display labels. Rows whose packages were not created are
    skipped (the admin read-back has already recorded that failure)."""
    offers = {o["key"]: o for o in plan.get("offers", [])}
    exit_offer = offers.get("exit-pop")
    free_scopes = [scope for _, _, scope in _free_shipping_offers(plan)]
    cases = []

    def add(name, line_keys, vouchers, total, ship=None, shipping_key=None):
        if ship is None:
            ship = "free" if _ships_free(plan, line_keys) else "paid"
        lines = [{"package_id": ids[k], "quantity": q} for k, q in line_keys]
        cases.append(CartCase(name, lines, vouchers, total, ship, line_keys, shipping_key))

    for l in plan.get("landed_prices", []):
        keys = [k for k in l["package_keys"] if ids.get(k)]
        if not keys:
            continue
        qty, total = l["qty"], D(l["order_total"])
        sk = l.get("shipping_key")
        if l["kind"] in ("tier", "single"):
            # A free-shipping offer scoped to a variant other than the row's first
            # would never meet a cart it can fire on, so each such scope gets a
            # single-variant cart of its own. Every key in a row prices alike.
            leads = [keys[0]]
            for scope in free_scopes:
                k = next((x for x in keys if x in scope), None)
                if k is not None and keys[0] not in scope and k not in leads:
                    leads.append(k)
            for i, k in enumerate(leads):
                add(f"{l['tier']} single variant" + (f" {k}" if i else ""), [(k, qty)], [], total, shipping_key=sk)
            if qty > 1 and len(keys) > 1:
                add(f"{l['tier']} mixed variants", [(keys[i % len(keys)], 1) for i in range(qty)], [], total,
                    shipping_key=sk)
            if exit_offer and _voucher_applies(exit_offer, l["package_keys"], qty):
                unit = landed_unit(D(l["unit_after"]), D(exit_offer["benefit"]["value"]),
                                   exit_offer["benefit"].get("price_rounding"))
                add(f"{l['tier']} + exit voucher", [(keys[0], qty)], [exit_offer["code"]], unit * qty, shipping_key=sk)
        elif l["kind"] == "upsell":
            up = offers.get(l.get("offer_key")) if l.get("offer_key") else None
            if up and up.get("offer_type") == "voucher":
                add(f"{l['tier']} voucher", [(keys[0], 1)], [up["code"]], D(l["unit_after"]), ship="none")

    # Hero and gift package_percentage offers run in the same cart when a gift is
    # present; prove they do not shadow each other (separate single-line cases alone
    # would miss that regression). One mixed case per gift covers multi-gift plans.
    roles = {p["key"]: p.get("role") for p in plan.get("packages", [])}
    hero_one = next((l for l in plan.get("landed_prices", [])
                     if l.get("kind") in ("tier", "single") and l.get("qty") == 1
                     and l.get("package_keys")
                     and all(roles.get(k) == "hero" for k in l["package_keys"])
                     and ids.get(l["package_keys"][0])), None)
    gift_rows = [l for l in plan.get("landed_prices", [])
                 if l.get("kind") == "single" and l.get("package_keys")
                 and all(roles.get(k) == "gift" for k in l["package_keys"])
                 and all(ids.get(k) for k in l["package_keys"])]
    if hero_one:
        hk = hero_one["package_keys"][0]
        for gift_one in gift_rows:
            gk = gift_one["package_keys"][0]
            add(f"{hero_one['tier']} + {gift_one['tier']}",
                [(hk, hero_one["qty"]), (gk, gift_one["qty"])], [],
                D(hero_one["order_total"]) + D(gift_one["order_total"]),
                shipping_key=hero_one.get("shipping_key"))
    return cases


def _free_shipping_coverage(cases: list, offers: list, key, n: int, scope, price_of) -> tuple:
    """(ok, detail) for one free-shipping offer. calculate pins its threshold only
    with a checkout cart at exactly n in-scope units that no other free-shipping
    offer would free on its own, and, for n > 1, one at exactly n - 1 that pays.
    Wider gaps would pass a live offer that starts earlier or later, and a cart
    another offer frees anyway says nothing about this one. A cart also only
    counts when the shipping price clears its rounding tolerance (0.01 a unit);
    below that, a free cart and a paid one price the same. price_of(case) is the
    shipping price that case is probed with, or None when it has no shipping
    method to carry (none on the campaign, or its row's method was never
    created); such a case is not probed with shipping and proves nothing. Cases
    may sit on different methods: each is compared against its own price."""
    def units(x):
        return f"{x} in-scope unit" + ("" if x == 1 else "s")

    def freed_by_other(lk):
        return next((k for k, m, s in offers if k != key and _in_scope(lk, s) >= m), None)

    priced = [(c[4], c[5], price_of(c)) for c in cases if c[4] != "none"]
    if priced and all(p is None for _, _, p in priced):
        return False, "no shipping method on the campaign, so calculate cannot tell free shipping from paid"
    checkout_candidates = [(ship, _in_scope(lk, scope), lk, p) for ship, lk, p in priced if p is not None]
    checkout = [c for c in checkout_candidates if c[3] > Decimal("0.01") * sum(q for _, q in c[2])]
    tiny = ", ".join(sorted({money(c[3]) for c in checkout_candidates if c not in checkout}))
    if checkout_candidates and not checkout:
        return False, (f"shipping price {tiny} is within calculate's rounding tolerance, "
                       "so a free cart and a paid one price the same")
    seen = ", ".join(str(u) for u in sorted({u for _, u, _, _ in checkout if u})) or "none"
    problems = []
    at = [lk for _, u, lk, _ in checkout if u == n]
    if not at:
        problems.append(f"no calculate case with exactly {units(n)}")
    elif all(freed_by_other(lk) for lk in at):
        problems.append(f"threshold masked by offer {freed_by_other(at[0])} at {units(n)}; "
                        "calculate cannot prove it")
    if n > 1:
        below = [(ship, lk) for ship, u, lk, _ in checkout if u == n - 1]
        if not below:
            problems.append(f"no calculate case with exactly {units(n - 1)}")
        elif all(ship == "free" for ship, _ in below):
            # a cart below this offer's threshold can only be free through another
            # offer (keys are unique in a validated plan), so this names one
            problems.append(f"threshold masked by offer {freed_by_other(below[0][1])} at "
                            f"{units(n - 1)}; calculate cannot prove it")
    if problems:
        dropped = len(checkout_candidates) - len(checkout)
        note = (f" ({dropped} cart(s) left out: shipping {tiny} is within their rounding tolerance)"
                if dropped else "")
        return False, "; ".join(problems) + f"; cases cover {seen} in-scope units{note}"
    return True, f"cases at {n - 1} and {units(n)}" if n > 1 else f"a case at {units(1)}"


OFFER_SET_CHECK = "campaign offers match the plan"


def verify(client: Client, cart: Client, man: Manifest, plan: dict, plan_sha: str) -> dict:
    errs = validate_plan(plan, for_create=False)
    if errs:
        raise CampaignAdminError("plan is invalid, cannot verify:\n  - " + "\n  - ".join(errs))
    check_origin_binding(man.data, "manifest")
    if man.data.get("pending_edit"):
        raise CampaignAdminError(PENDING_EDIT_MSG + " then verify.")
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

    plan_sm = {ship_key(s): s for s in plan["shipping_methods"]}
    journalled_ships = {e.get("key") for e in man.data["shipping_methods"]}
    for k in plan_sm:
        if k not in journalled_ships:
            check(f"shipping {k} journalled", False, "no manifest entry for this planned shipping method")
    ships = {s["id"]: s for s in client.paginate(f"/api/admin/campaigns/{cid}/shipping-methods/")}
    ship_by_key = {}  # shipping key -> (created id, planned price), in manifest order
    for e in man.data["shipping_methods"]:
        s = plan_sm.get(e.get("key"))
        if s is None:
            check(f"shipping {e.get('key')}", False, "manifest entry is not in the plan")
            continue
        if e.get("status") != "created" or not e.get("id"):
            check(f"shipping {e['key']}", False, f"status {e.get('status')}")
            continue
        live_s = ships.get(e.get("id"))
        if not live_s:
            check(f"shipping {e['key']}", False, "missing remotely")
            continue
        check(f"shipping {e['key']} code", live_s.get("shipping_method") == s["shipping_method"],
              f"{live_s.get('shipping_method')} vs {s['shipping_method']}")
        price = _price_in(live_s, c["currency"])
        live_price = _finite(_num(price))
        check(f"shipping {e['key']} price", live_price == D(s["price"]),
              f"{'unreadable' if live_price is None else price} vs {s['price']}")
        if live_s.get("shipping_method") == s["shipping_method"]:
            ship_by_key.setdefault(e["key"], (e["id"], D(s["price"])))
    # A row without its own shipping_key carries the PLAN's first shipping method,
    # not the first journalled one. If that method was not created, those rows fail
    # rather than borrow another rung's price.
    plan_default_key = ship_key(plan["shipping_methods"][0]) if plan.get("shipping_methods") else None

    def case_ship_key(case):
        return None if case.ship == "none" else (case.shipping_key or plan_default_key)

    def case_ship_price(case):
        sk = case_ship_key(case)
        return ship_by_key[sk][1] if sk in ship_by_key else None

    # apply journals an offer only as it creates it, so a run that stopped partway
    # leaves later planned offers with no entry and no row below.
    journalled_offers = {e.get("key") for e in man.data["offers"]}
    for o in plan.get("offers", []):
        if o["key"] not in journalled_offers:
            check(f"offer {o['key']} journalled", False, "no manifest entry for this planned offer")

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
                check(f"offer {e['key']}", False, f"retrieve returned {st}"
                      + auth_hint(st, "GET", f"/api/admin/campaigns/{cid}/offers/{e['id']}/"))
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
            # Read back only where the retrieve body carries the field: older store
            # builds omit some of these, and an absent field is not a mismatch.
            live_c = live_o.get("condition") or {}
            if live_c.get("type") is not None:
                check(f"offer {e['key']} condition type", live_c.get("type") == o["condition"]["type"],
                      str(live_c.get("type")))
            if "value" in live_c and o["condition"]["type"] == "count":
                check(f"offer {e['key']} condition value", live_c.get("value") == o["condition"]["value"],
                      str(live_c.get("value")))
            if "available" in live_o:
                check(f"offer {e['key']} available", bool(live_o.get("available")) == _offer_live(o),
                      str(live_o.get("available")))

    # Pricing truth: carts/calculate with the campaign key.
    hero_present = any(p["role"] == "hero" and ids.get(p["key"]) for p in plan["packages"])
    if not hero_present:
        check("hero packages present", False, "no created hero package ids in the manifest")
    cart_cases = _cart_cases_from_plan(plan, ids)
    if hero_present:
        free_offers = _free_shipping_offers(plan)
        for key, n, scope in free_offers:
            ok, detail = _free_shipping_coverage(cart_cases, free_offers, key, n, scope, case_ship_price)
            check(f"offer {key} free-shipping coverage", ok, detail)

    for case in cart_cases:
        name, lines, vouchers, expected, ship = case.name, case.lines, case.vouchers, case.expected, case.ship
        body = {"lines": lines}
        if vouchers:
            body["vouchers"] = vouchers
        path = "/api/v1/carts/calculate/"
        sk = case_ship_key(case)
        shipping_price = Decimal(0)
        units = sum(l["quantity"] for l in lines)
        tol = Decimal("0.01") * units  # documented per-unit rounding bound (offer doctrine: Rounding and stacking)
        if ship == "none":
            # The Cart API's upsell mode skips site-wide automatic offers, as a real
            # upsell page does; an upsell has no shipping method of its own.
            path += "?upsell=true"
        elif sk is not None and sk not in ship_by_key:
            # Probing without the row's method would price a different cart and
            # could pass; the row is unproven, so it fails without a call.
            cases.append({"case": name, "result": "FAIL", "status": None,
                          "expected_total": None, "got_total": None, "delta": None,
                          "tolerance": money(tol), "units": units,
                          "shipping": ship, "expected_shipping": None, "shipping_key": sk,
                          "path": path, "request": body, "response_keys": None,
                          "error": f"shipping method {sk!r} was not created; cannot price this row"})
            continue
        elif sk is not None:
            body["shipping_method"], shipping_price = ship_by_key[sk]
        status, resp = cart.request("POST", path, body)
        got_total = _num((resp or {}).get("total")) if isinstance(resp, dict) else None
        shipped = "shipping_method" in body
        ship_component = shipping_price if (ship == "paid" and shipped) else Decimal(0)
        want_total = expected + ship_component
        delta = None if got_total is None else (got_total - want_total)
        ok = status == 200 and delta is not None and abs(delta) <= tol
        cases.append({"case": name, "result": "PASS" if ok else "FAIL", "status": status,
                      "expected_total": money(want_total), "got_total": None if got_total is None else money(got_total),
                      "delta": None if delta is None else money(delta),
                      "tolerance": money(tol), "units": units,
                      "shipping": ship, "expected_shipping": money(ship_component) if shipped else None,
                      "shipping_key": sk if shipped else None,
                      "path": path, "request": body,
                      "response_keys": sorted(resp.keys()) if isinstance(resp, dict) else None,
                      "error": None if ok else _short(resp)})

    # An offer added in the dashboard can free or discount a cart the way a
    # planned offer should, so calculate would pass a wrong or missing threshold.
    # The live set has to be exactly what this run created, read after the probes
    # so an offer added while they ran is caught too.
    unowned = []
    try:
        live_offers = client.paginate(f"/api/admin/campaigns/{cid}/offers/")
    except CampaignAdminError as exc:
        # Skip the check only when both hold: the store has no offers endpoint
        # (404/405) and the plan has no offers, so there is nothing to compare.
        # Any other failure to list leaves the live set unknown, which is not a pass.
        endpoint_absent = getattr(exc, "status", None) in (404, 405)
        nothing_planned = not plan.get("offers")
        if not (endpoint_absent and nothing_planned):
            check(OFFER_SET_CHECK, False, f"could not list live offers: {exc}")
    else:
        ours = {e.get("id") for e in man.data["offers"]
                if e.get("status") == "created" and e.get("id") is not None}
        extra = [f"{x.get('id')} {x.get('name')!r}" for x in live_offers if x.get("id") not in ours]
        unowned = [{"id": x.get("id"), "name": x.get("name")} for x in live_offers if x.get("id") not in ours]
        # an offer deleted after its read-back and probes would otherwise pass
        live_ids = {x.get("id") for x in live_offers}
        gone = sorted(str(i) for i in ours - live_ids)
        problems = ([f"unplanned live offer(s): {', '.join(extra)}"] if extra else []) + \
                   ([f"created offer(s) no longer live: {', '.join(gone)}"] if gone else [])
        check(OFFER_SET_CHECK, not problems,
              "; ".join(problems) if problems else f"{len(live_offers)} live, all created by this run")

    result = "PASS" if all(x["result"] == "PASS" for x in checks + cases) else "FAIL"
    # The overall result is unchanged by this breakdown. It says which part
    # failed: with an offer this run does not own live on the campaign, the
    # carts price through an offer set the plan cannot account for, so a cart
    # result is not evidence about the plan either way.
    def part(rows):
        return "PASS" if all(x["result"] == "PASS" for x in rows) else "FAIL"
    sections = {
        "owned_fields": part([x for x in checks if x["check"] != OFFER_SET_CHECK]),
        "calculate": "NOT ATTRIBUTABLE" if unowned else part(cases),
        "live_offer_set": part([x for x in checks if x["check"] == OFFER_SET_CHECK]),
    }
    return {"manifest_run_id": man.data["run_id"], "plan_sha256": plan_sha, "verified_at": utcnow(),
            "admin_checks": checks, "calculate_cases": cases, "sections": sections,
            "unowned_live_offers": unowned, "result": result}


def print_verify(report: dict) -> None:
    for x in report["admin_checks"]:
        print(f"  {x['result']}  {x['check']}  {x['detail']}")
    for x in report["calculate_cases"]:
        extra = "" if x["result"] == "PASS" else f"  ({x['error']})"
        if x.get("shipping") == "none":
            ship = "no shipping, upsell"
        elif x.get("expected_shipping") is None and x.get("shipping_key"):
            ship = f"shipping method {x['shipping_key']} not created"
        elif x.get("expected_shipping") is None:
            ship = "no shipping method"
        elif x.get("shipping") == "free":
            ship = "free shipping"
        else:
            ship = f"shipping {x['expected_shipping']}"
        print(f"  {x['result']}  calculate {x['case']}: expected {x['expected_total']} ({ship}) "
              f"got {x['got_total']}{extra}")
    if report.get("unowned_live_offers"):
        s = report.get("sections") or {}
        names = ", ".join(f"{x.get('id')} {x.get('name')!r}" for x in report["unowned_live_offers"])
        print(f"  Offer(s) this run does not own are live: {names}. They are left alone. Fields this run "
              f"owns: {s.get('owned_fields')}. The cart totals above cannot be attributed to the plan "
              "while those offers are live.")
    print(f"VERIFY: {report['result']}")


# --------------------------------------------------------------------------- #
# edit (in-place changes to a campaign this run created)
# --------------------------------------------------------------------------- #

# Operation -> the fields it may carry. Anything else is not editable in place.
EDIT_OPS = {
    "set_package_price": ("package_keys", "price"),
    "set_offer_benefit": ("offer_key", "value", "price_rounding"),
    "set_offer_condition": ("offer_key", "type", "value"),
    "set_offer_scope": ("offer_key", "package_keys"),
    "set_offer_available": ("offer_key", "available"),
    "add_offer": ("offer", "available"),
}
ADD_OFFER_FIELDS = ("key", "name", "offer_type", "code", "condition", "benefit")
# Requests an operator may reasonably make that have no in-place route. Naming
# them lets the refusal say which field forces a teardown.
NOT_EDITABLE = {
    "set_campaign_currency": "campaign.currency",
    "set_campaign_language": "campaign.language",
    "set_campaign_gateway_group": "campaign.payment_gateway_group_id",
    "set_package_product": "package.product_id",
    "set_package_variant": "package.product_variant_ids",
    "set_package_image": "package.image",
    "set_offer_type": "offer.offer_type",
    "set_offer_code": "offer.code",
    "set_offer_all_packages": "offer.condition.all_packages",
    "set_offer_benefit_type": "offer.benefit.type",
    "set_shipping_price": "shipping_method.price",
    "delete_offer": "deleting an offer (pause it with set_offer_available instead)",
    "delete_package": "deleting a package",
    "delete_shipping_method": "deleting a shipping method",
    "delete_campaign": "deleting the campaign",
}
RECREATE = "teardown and recreate"


def _not_editable(tag: str, field: str) -> CampaignAdminError:
    return CampaignAdminError(f"{tag}: cannot be edited in place: {field}; {RECREATE}")


def _rows_agree(a: list, b: list) -> bool:
    """Whether two landed_prices lists price alike, ignoring number formatting."""
    def sig(l):
        return (l.get("offer_key"), _num(l.get("anchor")), _num(l.get("pct")),
                _num(l.get("unit_after")), _num(l.get("order_total")))
    return [sig(l) for l in a] == [sig(l) for l in b]


def reland(plan: dict) -> list:
    """landed_prices recomputed from the plan's own packages and offers.

    Each existing row keeps its label, quantity and packages; its price is
    worked out again the way the offer engine works it out: among the live
    automatic package offers whose scope covers the row and whose condition the
    quantity meets, the highest percentage wins, and rounding is applied to the
    winner (offer doctrine: Rounding and stacking). No winner means the package
    price. Upsell rows are priced by their own voucher and never enter that
    contest. Shapes the rows cannot express are refused rather than guessed."""
    prices = {p["key"]: D(p["price"]) for p in plan["packages"]}
    offers = {o["key"]: o for o in plan.get("offers", [])}
    auto = [o for o in plan.get("offers", [])
            if o.get("offer_type", "offer") == "offer" and o["benefit"]["type"] == "package_percentage"
            and _offer_live(o)]
    exit_offer = offers.get("exit-pop")
    old_rows = plan.get("landed_prices", [])
    bxgy_keys = {l.get("offer_key") for l in old_rows if l.get("paid_qty") is not None}
    out = []
    for old in old_rows:
        row = dict(old)
        keys, qty, tag = list(row["package_keys"]), row["qty"], row.get("tier")
        anchors = {prices[k] for k in keys}
        if len(anchors) != 1:
            raise CampaignAdminError(
                f"landed row {tag!r}: its packages {keys} would no longer share one price. The packages "
                f"of a row are repriced together; a different price per variant needs a fresh "
                f"recommend ({RECREATE})")
        anchor = anchors.pop()
        if row["kind"] == "upsell":
            up = offers.get(row.get("offer_key")) if row.get("offer_key") else None
            if up is None:
                if _num(row.get("anchor")) != anchor:
                    raise CampaignAdminError(
                        f"landed row {tag!r} has no voucher in the plan, so its price cannot be recomputed")
                out.append(row)
                continue
            if not _voucher_applies(up, keys, qty):
                raise CampaignAdminError(
                    f"landed row {tag!r}: voucher {up['key']!r} would no longer apply to it (its scope must "
                    f"cover {keys} and its condition must be met at quantity {qty}), so the upsell would sell "
                    f"at the package price under a discounted label. That needs a fresh recommend ({RECREATE})")
            pct = D(up["benefit"]["value"])
            unit = check_rounding_discount(f"{up['name']} ({keys[0]})", anchor, pct,
                                           up["benefit"].get("price_rounding"))
            if unit <= 0:
                raise CampaignAdminError(f"landed row {tag!r}: {_pct_for_landed(pct)}% leaves a non-positive "
                                         f"unit price ({unit})")
            row.update(anchor=money(anchor), pct=_pct_for_landed(pct), unit_after=money(unit),
                       order_total=money(unit * qty))
            out.append(row)
            continue

        if row.get("offer_key") is None and (_num(row.get("pct")) or Decimal(0)) != 0:
            raise CampaignAdminError(
                f"landed row {tag!r} carries a discount with no offer in the plan (its offers were handed "
                "to the dashboard), so its price cannot be recomputed in place")
        scoped = auto + ([exit_offer] if exit_offer else [])
        partial = [o["key"] for o in scoped
                   if 0 < len(set(keys) & set(o["condition"]["package_keys"])) < len(set(keys))]
        if partial:
            raise CampaignAdminError(
                f"landed row {tag!r}: offer(s) {partial} would cover only some of its packages {keys}, so "
                "single-variant and mixed-variant carts would price differently. A scope covers a row's "
                f"packages entirely or not at all; anything else needs a fresh recommend ({RECREATE})")
        eligible = [o for o in auto if set(keys) <= set(o["condition"]["package_keys"])
                    and _condition_met(o["condition"], qty)]
        win = None
        if eligible:
            top = max(D(o["benefit"]["value"]) for o in eligible)
            winners = [o for o in eligible if D(o["benefit"]["value"]) == top]
            if len(winners) > 1:
                raise CampaignAdminError(
                    f"landed row {tag!r}: offers {[o['key'] for o in winners]} both apply at "
                    f"{_pct_for_landed(top)}%, so which one prices the row is not determined; give them "
                    "different percentages or pause one")
            win = winners[0]
        was_bxgy = row.get("paid_qty") is not None
        if was_bxgy != (win is not None and win["key"] in bxgy_keys) or (was_bxgy and win["key"] != old.get("offer_key")):
            raise CampaignAdminError(
                f"landed row {tag!r} would be priced by {win['key'] if win else 'no offer'} instead of "
                f"{old.get('offer_key') or 'no offer'}, which changes what kind of row it is; that needs a "
                f"fresh recommend ({RECREATE})")
        pct = D(win["benefit"]["value"]) if win else Decimal(0)
        unit = check_rounding_discount(win["name"], anchor, pct, win["benefit"].get("price_rounding")) if win else anchor
        if unit < 0 or (unit == 0 and _num(old.get("unit_after")) != 0):
            raise CampaignAdminError(f"landed row {tag!r}: {_pct_for_landed(pct)}% off {money(anchor)} leaves a "
                                     f"non-positive unit price ({unit})")
        if was_bxgy:
            row.update(_bxgy_landed_row(tag, row["kind"], qty, win["key"], keys, anchor, pct, unit,
                                        row["paid_qty"], row["free_qty"], note=row.get("note")))
        else:
            row.update(offer_key=win["key"] if win else None, anchor=money(anchor),
                       pct=_pct_for_landed(pct), unit_after=money(unit), order_total=money(unit * qty))
        out.append(row)

    # The exit voucher stacks on the row price, so every row it can meet must
    # still come out below that price, as recommend checks.
    if exit_offer and exit_offer["benefit"]["type"] == "package_percentage":
        for row in out:
            if (row["kind"] in ("tier", "single") and D(row["unit_after"]) > 0
                    and _voucher_applies(exit_offer, row["package_keys"], row["qty"])):
                check_rounding_discount(f"{exit_offer['name']} on {row['tier']}", D(row["unit_after"]),
                                        D(exit_offer["benefit"]["value"]),
                                        exit_offer["benefit"].get("price_rounding"))
    return out


def _edit_ops(spec) -> list:
    if not isinstance(spec, dict) or not isinstance(spec.get("operations"), list) or not spec["operations"]:
        raise CampaignAdminError('the edit file must be a JSON object with a non-empty "operations" list')
    extra = sorted(set(spec) - {"operations", "note"})
    if extra:
        raise CampaignAdminError(f"the edit file has unsupported field(s) {extra}; only operations and note are read")
    return spec["operations"]


def _pct_value(tag: str, v) -> str:
    s = str(v)
    if isinstance(v, bool) or not DECIMAL_RE.match(s) or not (Decimal(0) < D(s) <= Decimal(100)):
        raise CampaignAdminError(f"{tag}: value {v!r} must be a percentage in (0, 100]")
    return money(D(s))


def build_edit(plan: dict, spec: dict) -> tuple:
    """Apply an edit file to a copy of the plan. Returns (new_plan, changed,
    added): `changed` is the [(section, key)] whose plan object differs,
    `added` the keys of offers the edit creates. Nothing is read from the
    store here; every refusal names the operation and the field."""
    if not _rows_agree(reland(plan), plan.get("landed_prices", [])):
        raise CampaignAdminError(
            "this plan's landed prices are not what its own packages and offers produce (it was edited by "
            f"hand), so an edit cannot recompute them; {RECREATE} from a fresh recommend")
    new = json.loads(json.dumps(plan))
    pk = {p["key"]: p for p in new["packages"]}
    of = {o["key"]: o for o in new.get("offers", [])}
    rows = plan.get("landed_prices", [])
    bxgy_keys = {l.get("offer_key") for l in rows if l.get("paid_qty") is not None}
    row_vouchers = {l.get("offer_key") for l in rows if l.get("kind") == "upsell"} | {"exit-pop"}
    added = []

    def keys_of(tag, op):
        ks = op.get("package_keys")
        if not isinstance(ks, list) or not ks or len(set(map(str, ks))) != len(ks):
            raise CampaignAdminError(f"{tag}: package_keys must be a non-empty list of distinct package keys")
        missing = [k for k in ks if k not in pk]
        if missing:
            raise CampaignAdminError(f"{tag}: package key(s) {missing} are not in the plan; edit never "
                                     "touches a package this run did not create")
        return list(ks)

    def offer_of(tag, op):
        k = op.get("offer_key")
        if k in added:
            raise CampaignAdminError(f"{tag}: offer {k!r} is added by this same edit; set it in add_offer")
        if k not in of:
            raise CampaignAdminError(f"{tag}: offer {k!r} is not in the plan; edit never touches an offer "
                                     "this run did not create")
        return of[k]

    def pause_refusal(tag, o):
        if o.get("offer_type", "offer") == "voucher" and o["key"] in row_vouchers:
            raise CampaignAdminError(
                f"{tag}: pausing voucher {o['key']!r} is not supported in this version: how the cart treats "
                f"a paused code is unobserved, so its landed price could not be proven. Pause it in the "
                f"dashboard, or {RECREATE}")

    for i, op in enumerate(_edit_ops(spec)):
        tag = f"operations[{i}]"
        if not isinstance(op, dict):
            raise CampaignAdminError(f"{tag}: must be an object with an op")
        name = op.get("op")
        if name in NOT_EDITABLE:
            raise _not_editable(tag, NOT_EDITABLE[name])
        if name not in EDIT_OPS:
            raise CampaignAdminError(f"{tag}: unknown op {name!r}; expected one of {sorted(EDIT_OPS)}")
        tag = f"{tag} {name}"
        extra = sorted(set(op) - {"op"} - set(EDIT_OPS[name]))
        if extra:
            raise _not_editable(tag, ", ".join(extra))

        if name == "set_package_price":
            price = str(op.get("price", ""))
            if isinstance(op.get("price"), bool) or not DECIMAL_RE.match(price) or D(price) <= 0:
                raise CampaignAdminError(f"{tag}: price {op.get('price')!r} must be a decimal greater than 0 "
                                         "(max 8 digits, 2 places)")
            for k in keys_of(tag, op):
                pk[k]["price"] = money(D(price))
        elif name == "set_offer_benefit":
            o = offer_of(tag, op)
            if "value" not in op and "price_rounding" not in op:
                raise CampaignAdminError(f"{tag}: give value, price_rounding or both")
            if "value" in op:
                if o["key"] in bxgy_keys:
                    raise _not_editable(tag, "benefit.value of a buy-X-get-Y offer (the percentage is derived "
                                             "from its paid and free quantities)")
                o["benefit"]["value"] = _pct_value(tag, op["value"])
            if "price_rounding" in op:
                if op["price_rounding"] not in PRICE_ROUNDINGS:
                    raise CampaignAdminError(f"{tag}: price_rounding {op['price_rounding']!r} must be null or one "
                                             f"of {', '.join(PRICE_ROUNDINGS[1:])}")
                o["benefit"]["price_rounding"] = op["price_rounding"]
        elif name == "set_offer_condition":
            o = offer_of(tag, op)
            if "type" not in op and "value" not in op:
                raise CampaignAdminError(f"{tag}: give type, value or both")
            if o["key"] in bxgy_keys:
                raise _not_editable(tag, "the condition of a buy-X-get-Y offer (its landed rows are built on "
                                         "that quantity)")
            ctype = op.get("type", o["condition"]["type"])
            if ctype not in CONDITION_TYPES:
                raise CampaignAdminError(f"{tag}: type {ctype!r} must be one of {', '.join(CONDITION_TYPES)}")
            if ctype == "any":
                if op.get("value") is not None:
                    raise CampaignAdminError(f"{tag}: an 'any' condition takes no value")
                value = None
            else:
                value = op.get("value", o["condition"].get("value"))
                if type(value) is not int or value < 1:
                    raise CampaignAdminError(f"{tag}: a count condition needs an integer value >= 1")
            o["condition"]["type"], o["condition"]["value"] = ctype, value
        elif name == "set_offer_scope":
            o = offer_of(tag, op)
            o["condition"]["package_keys"] = keys_of(tag, op)
        elif name == "set_offer_available":
            o = offer_of(tag, op)
            if type(op.get("available")) is not bool:
                raise CampaignAdminError(f"{tag}: available must be true or false")
            if op["available"]:
                o.pop("available", None)
            else:
                pause_refusal(tag, o)
                o["available"] = False
        else:  # add_offer
            src = op.get("offer")
            if not isinstance(src, dict):
                raise CampaignAdminError(f"{tag}: offer must be an object shaped like a plan offer")
            extra = sorted(set(src) - set(ADD_OFFER_FIELDS))
            if extra:
                raise CampaignAdminError(f"{tag}: offer has unsupported field(s) {extra}")
            cond, ben = src.get("condition"), src.get("benefit")
            if not isinstance(cond, dict) or not isinstance(ben, dict) or not src.get("key"):
                raise CampaignAdminError(f"{tag}: offer needs a key, a condition and a benefit")
            if src["key"] in of:
                raise CampaignAdminError(f"{tag}: offer key {src['key']!r} is already in the plan")
            if cond.get("all_packages"):
                raise _not_editable(tag, "offer.condition.all_packages")
            if cond.get("type") == "any" and cond.get("value") is not None:
                raise CampaignAdminError(f"{tag}: an 'any' condition takes no value")
            if ben.get("price_rounding") not in PRICE_ROUNDINGS:
                raise CampaignAdminError(f"{tag}: price_rounding {ben.get('price_rounding')!r} must be null or "
                                         f"one of {', '.join(PRICE_ROUNDINGS[1:])}")
            o = {"key": src["key"], "name": src.get("name"), "offer_type": src.get("offer_type", "offer"),
                 "code": src.get("code"),
                 "condition": {"type": cond.get("type"), "value": cond.get("value") if cond.get("type") == "count" else None,
                               "package_keys": list(cond.get("package_keys") or [])},
                 "benefit": {"type": ben.get("type"), "value": _pct_value(tag, ben.get("value")),
                             "price_rounding": ben.get("price_rounding")}}
            if "available" in op:
                if type(op["available"]) is not bool:
                    raise CampaignAdminError(f"{tag}: available must be true or false")
                if not op["available"]:
                    pause_refusal(tag, o)
                    o["available"] = False
            new.setdefault("offers", []).append(o)
            of[o["key"]] = o
            added.append(o["key"])

    errs = validate_plan(new, for_create=False)
    if errs:
        raise CampaignAdminError("the edit would leave an invalid plan:\n  - " + "\n  - ".join(errs))
    new["landed_prices"] = reland(new)
    old_pk = {p["key"]: p for p in plan["packages"]}
    old_of = {o["key"]: o for o in plan.get("offers", [])}
    changed = ([("packages", k) for k in old_pk if pk[k] != old_pk[k]]
               + [("offers", k) for k in old_of if of[k] != old_of[k]])
    if not changed and not added:
        raise CampaignAdminError("the edit changes nothing: every value it sets is already in the plan")
    return new, changed, added


def _norm_money(x):
    d = _finite(_num(x))
    return money(d) if d is not None else (None if x is None else str(x))


def norm_package(x: dict) -> dict:
    """The parts of a package read body an edit can change or relies on."""
    rows = []
    for r in x.get("prices") or []:
        if isinstance(r, dict):
            row = {"currency": r.get("currency"), "price": _norm_money(r.get("price"))}
            if r.get("price_recurring") is not None:
                row["price_recurring"] = _norm_money(r["price_recurring"])
            rows.append(row)
    return {"id": x.get("id"), "name": x.get("name"), "product_variant_id": x.get("product_variant_id"),
            "prices": sorted(rows, key=lambda r: str(r["currency"]))}


def norm_offer(x: dict) -> dict:
    """The parts of an offer read body an edit can change or relies on. A field
    the store did not send is recorded as absent, never assumed."""
    cond, ben = x.get("condition") or {}, x.get("benefit") or {}
    pkgs = cond.get("packages")
    rounding = ben.get("price_rounding")
    return {
        "id": x.get("id"), "name": x.get("name"), "offer_type": x.get("offer_type"), "code": x.get("code") or None,
        "available": x.get("available") if isinstance(x.get("available"), bool) else None,
        "condition": {"type": cond.get("type"), "has_value": "value" in cond, "value": cond.get("value"),
                      "all_packages": bool(cond.get("all_packages")),
                      "package_ids": sorted(p.get("id") for p in pkgs if isinstance(p, dict))
                      if isinstance(pkgs, list) else None},
        "benefit": {"type": ben.get("type"), "value": _norm_money(ben.get("value")),
                    "price_rounding": None if rounding in (None, "") else _norm_money(rounding)},
    }


def _offer_vs_plan(n: dict, o: dict, package_ids: dict, need_condition: bool, need_available: bool) -> list:
    """Where a live offer (normalised) disagrees with its plan offer, as
    messages. need_* demand that the store actually sent the field, because a
    write is about to depend on it."""
    out = []
    # What the percentage means depends on these three, so a live offer that
    # differs here is not the offer the plan prices, whatever its value. No edit
    # writes them, so a read that omits one is inconclusive, not a mismatch.
    if n["benefit"]["type"] is not None and n["benefit"]["type"] != o["benefit"]["type"]:
        out.append(f"benefit.type is {n['benefit']['type']} live, {o['benefit']['type']} in the plan")
    if n["offer_type"] is not None and n["offer_type"] != o.get("offer_type", "offer"):
        out.append(f"offer_type is {n['offer_type']} live, {o.get('offer_type', 'offer')} in the plan")
    if o.get("offer_type", "offer") == "voucher" and n["code"] is not None and n["code"] != o.get("code"):
        out.append(f"code is {n['code']!r} live, {o.get('code')!r} in the plan")
    if n["benefit"]["value"] != money(D(o["benefit"]["value"])):
        out.append(f"benefit.value is {n['benefit']['value']} live, {o['benefit']['value']} in the plan")
    if n["benefit"]["price_rounding"] != (o["benefit"].get("price_rounding") or None):
        out.append(f"benefit.price_rounding is {n['benefit']['price_rounding']} live, "
                   f"{o['benefit'].get('price_rounding')} in the plan")
    c = n["condition"]
    if need_condition and (c["type"] not in CONDITION_TYPES or not c["has_value"] or c["package_ids"] is None):
        out.append("the store's read of this offer does not carry its full condition (type, value and "
                   "packages), and a condition write replaces all of it, so the change cannot be proven")
    if c["type"] is not None and c["type"] != o["condition"]["type"]:
        out.append(f"condition.type is {c['type']} live, {o['condition']['type']} in the plan")
    if c["has_value"] and o["condition"]["type"] == "count" and c["value"] != o["condition"]["value"]:
        out.append(f"condition.value is {c['value']} live, {o['condition']['value']} in the plan")
    missing = [k for k in o["condition"]["package_keys"] if k not in package_ids]
    if missing:
        out.append(f"its plan scope names package(s) {missing} this run did not create")
    else:
        want = sorted(package_ids[k] for k in o["condition"]["package_keys"])
        if c["package_ids"] is not None and c["package_ids"] != want:
            out.append(f"condition packages are {c['package_ids']} live, {want} in the plan")
    if c["all_packages"]:
        out.append("condition.all_packages is set live")
    if need_available and n["available"] is None:
        out.append("the store's read of this offer does not carry `available`, so pausing or resuming it "
                   "cannot be proven")
    if n["available"] is not None and n["available"] != _offer_live(o):
        out.append(f"available is {n['available']} live, {_offer_live(o)} in the plan")
    return out


def _offer_patch(old: dict, new: dict, package_ids: dict) -> dict:
    """The one PATCH body that takes an offer from its `old` plan shape to
    `new`. benefit is merged by the server, so only what changes is sent; a
    condition is replaced whole, so it is always sent in full."""
    body = {}
    ob, nb = old["benefit"], new["benefit"]
    rounding_changed = (ob.get("price_rounding") or None) != (nb.get("price_rounding") or None)
    if D(ob["value"]) != D(nb["value"]) or rounding_changed:
        body["benefit"] = {"value": nb["value"]}
        if rounding_changed:
            body["benefit"]["price_rounding"] = nb.get("price_rounding") or None
    if old["condition"] != new["condition"]:
        body["condition"] = offer_body(new, package_ids)["condition"]
    if _offer_live(old) != _offer_live(new):
        body["available"] = _offer_live(new)
    return body


def _offer_expected(before: dict, new: dict, package_ids: dict, patch: dict) -> dict:
    """The normalised read an offer must show after `patch` lands."""
    exp = json.loads(json.dumps(before))
    if "benefit" in patch:
        exp["benefit"]["value"] = money(D(new["benefit"]["value"]))
        if "price_rounding" in patch["benefit"]:
            exp["benefit"]["price_rounding"] = new["benefit"].get("price_rounding") or None
    if "condition" in patch:
        exp["condition"].update(type=new["condition"]["type"], has_value=True,
                                value=new["condition"]["value"] if new["condition"]["type"] == "count" else None,
                                all_packages=False,
                                package_ids=sorted(package_ids[k] for k in new["condition"]["package_keys"]))
    if "available" in patch:
        exp["available"] = patch["available"]
    return exp


def _edit_ownership(client: Client, man: Manifest, plan: dict, plan_sha: str, accepted=None) -> None:
    """The checks every edit path passes before it reads or writes an object:
    the manifest belongs to this store and plan, the run is a built campaign
    that teardown has not touched, and the live campaign is the one it made."""
    check_origin_binding(man.data, "manifest")
    if man.data["store_slug"] != plan["store_slug"]:
        raise CampaignAdminError("manifest store_slug does not match the plan")
    if plan_sha not in (accepted or {man.data["plan_sha256"]}):
        raise CampaignAdminError("manifest plan_sha256 does not match this plan file; edit needs the plan "
                                 "this run's manifest records")
    camp = man.data["campaign"]
    torn = torn_down_entries(man)
    if torn:
        raise CampaignAdminError(f"this run was torn down, fully or in part ({', '.join(torn)}); there is "
                                 "nothing to edit")
    if camp.get("status") != "created" or not camp.get("id"):
        raise CampaignAdminError("the campaign is not in created state; edit only changes a campaign this "
                                 "run finished creating")
    live = client.get_ok(f"/api/admin/campaigns/{camp['id']}/")
    if not campaign_identity_ok(live, camp):
        raise CampaignAdminError(f"campaign {camp['id']} identity mismatch (name/created_at); refusing to "
                                 "edit anything")


def _read_object(client: Client, obj: dict) -> dict:
    st, x = client.request("GET", obj["path"])
    if st != 200 or not isinstance(x, dict):
        raise CampaignAdminError(f"{obj['section']} {obj['key']} (id {obj['id']}): GET returned {st}"
                                 + auth_hint(st, "GET", obj["path"]))
    return norm_package(x) if obj["section"] == "packages" else norm_offer(x)


def edit_prepare(client: Client, man: Manifest, plan: dict, plan_sha: str, spec: dict, kind: str = "edit") -> dict:
    """Everything an edit will do, worked out and read back, with nothing
    written. The returned edit_sha256 covers the plan, the change list and the
    before-image of every object the edit will write."""
    _edit_ownership(client, man, plan, plan_sha)
    new_plan, changed, added = build_edit(plan, spec)
    cid = man.data["campaign"]["id"]
    currency = plan["campaign"]["currency"]
    package_ids = {e["key"]: e["id"] for e in man.data["packages"] if e.get("status") == "created" and e.get("id")}
    plan_pk = {p["key"]: p for p in plan["packages"]}
    old_of = {o["key"]: o for o in plan.get("offers", [])}
    new_pk = {p["key"]: p for p in new_plan["packages"]}
    new_of = {o["key"]: o for o in new_plan.get("offers", [])}
    touches_offers = bool(added) or any(s == "offers" for s, _ in changed)

    try:
        live_offers = client.paginate(f"/api/admin/campaigns/{cid}/offers/")
    except HttpStatusError as exc:
        if touches_offers or exc.status not in (404, 405):
            raise
        live_offers = []
    owned = {e["id"] for e in man.data["offers"] if e.get("status") == "created" and e.get("id") is not None}
    unowned = [{"id": x.get("id"), "name": x.get("name")} for x in live_offers if x.get("id") not in owned]
    for k in added:
        o = new_of[k]
        clash = [x for x in live_offers
                 if x.get("name") == o["name"] or (o.get("code") and x.get("code") == o["code"])]
        if clash or man.entry("offers", k):
            who = f"live offer {clash[0].get('id')} {clash[0].get('name')!r}" if clash else "a manifest entry"
            raise CampaignAdminError(f"add_offer {k!r}: its name or code is already taken by {who}; choose "
                                     "another. Nothing was written.")

    objects = []
    for section, key in changed:
        e = man.entry(section, key)
        if not e or e.get("status") != "created" or not e.get("id"):
            raise CampaignAdminError(
                f"{section} {key} is not something this run created (manifest status "
                f"{(e or {}).get('status')!r}); edit never touches an object the manifest does not own")
        part = "packages" if section == "packages" else "offers"
        obj = {"section": section, "key": key, "id": e["id"],
               "path": f"/api/admin/campaigns/{cid}/{part}/{e['id']}/"}
        st, x = client.request("GET", obj["path"])
        if st != 200 or not isinstance(x, dict):
            raise CampaignAdminError(f"{section} {key} (id {e['id']}): GET returned {st}; nothing was written"
                                     + auth_hint(st, "GET", obj["path"]))
        ok = package_identity_ok(e, x, plan_pk) if section == "packages" else offer_identity_ok(e, x, old_of)
        if not ok:
            raise CampaignAdminError(f"{section} {key} (id {e['id']}) identity mismatch; refusing to edit anything")
        if section == "packages":
            before = norm_package(x)
            old_price, new_price = money(D(plan_pk[key]["price"])), money(D(new_pk[key]["price"]))
            live_price = next((r["price"] for r in before["prices"] if r["currency"] == currency), None)
            if live_price != old_price:
                raise CampaignAdminError(
                    f"package {key} (id {e['id']}): its {currency} price is {live_price} live, {old_price} in "
                    "the plan. It was changed outside this skill; put it back or " + RECREATE)

            def rows(price, source):
                return [dict(r, price=price) if r.get("currency") == currency else dict(r) for r in source]
            # The request resends the rows as the store returned them, so a field
            # on a price row this tool does not model goes back untouched.
            raw = [r for r in x.get("prices") or [] if isinstance(r, dict)]
            obj.update(before=before, expected=dict(before, prices=rows(new_price, before["prices"])),
                       patch={"prices": rows(new_price, raw)}, restore={"prices": rows(old_price, raw)},
                       diff=[["price", old_price, new_price]])
        else:
            before = norm_offer(x)
            old, new = old_of[key], new_of[key]
            patch = _offer_patch(old, new, package_ids)
            problems = _offer_vs_plan(before, old, package_ids, "condition" in patch, "available" in patch)
            if problems:
                raise CampaignAdminError(f"offer {key} (id {e['id']}) cannot be edited as it stands:\n  - "
                                         + "\n  - ".join(problems)
                                         + f"\n  Nothing was written. Put the offer back, or {RECREATE}.")
            diff = []
            for label, a, b in (
                ("benefit.value", old["benefit"]["value"], new["benefit"]["value"]),
                ("benefit.price_rounding", old["benefit"].get("price_rounding"), new["benefit"].get("price_rounding")),
                ("condition.type", old["condition"]["type"], new["condition"]["type"]),
                ("condition.value", old["condition"].get("value"), new["condition"].get("value")),
                ("condition.package_keys", old["condition"]["package_keys"], new["condition"]["package_keys"]),
                ("available", _offer_live(old), _offer_live(new)),
            ):
                if a != b:
                    diff.append([label, a, b])
            obj.update(before=before, expected=_offer_expected(before, new, package_ids, patch), patch=patch,
                       restore=_offer_patch(new, old, package_ids), diff=diff)
        obj["before_sha256"] = canonical_sha256(obj["before"])
        objects.append(obj)

    adds = [{"key": k, "offer": new_of[k], "body": offer_body(new_of[k], package_ids),
             "paused": not _offer_live(new_of[k])} for k in added]
    edit_sha = canonical_sha256({"kind": kind, "plan_sha256": plan_sha, "spec": spec,
                                 "before": {f"{o['section']}:{o['key']}": o["before_sha256"] for o in objects}})
    return {"kind": kind, "spec": spec, "old_plan_sha256": plan_sha, "new_plan": new_plan,
            "new_plan_sha256": hashlib.sha256(json_bytes(new_plan)).hexdigest(),
            "objects": objects, "adds": adds, "unowned_offers": unowned,
            "preexisting_offer_ids": sorted(x.get("id") for x in live_offers if x.get("id") is not None),
            "edit_sha256": edit_sha, "campaign_id": cid}


def print_edit(prep: dict, plan: dict, man: Manifest) -> None:
    camp = man.data["campaign"]
    what = "Undo" if prep["kind"] == "undo" else "Edit"
    print(f"{what} target: {man.data['store_origin']} campaign {camp['id']} {camp.get('name')!r}")
    if prep["objects"]:
        print("Changes (old -> new):")
    for o in prep["objects"]:
        for label, a, b in o["diff"]:
            print(f"  {o['section'][:-1]} {o['key']} (id {o['id']})  {label}: {a} -> {b}")
    if prep["adds"]:
        print("Offers added (a new offer is live the moment it is created):")
    for a in prep["adds"]:
        o = a["offer"]
        crit = f"qty>={o['condition']['value']}" if o["condition"]["type"] == "count" else "any"
        code = f" code={o['code']}" if o.get("offer_type") == "voucher" else ""
        note = "  then paused" if a["paused"] else ""
        print(f"  {o['key']}  {o['name']}  {crit} on {o['condition']['package_keys']}  "
              f"{o['benefit']['type']} {o['benefit']['value']}%{code}{note}")
    print("Landed prices (before -> after):")
    for b, a in zip(plan.get("landed_prices", []), prep["new_plan"]["landed_prices"]):
        mark = "" if (b.get("unit_after"), b.get("order_total"), b.get("offer_key")) == \
            (a.get("unit_after"), a.get("order_total"), a.get("offer_key")) else "  *"
        print(f"  {a['tier']:<28} qty {a['qty']}  anchor {b['anchor']} -> {a['anchor']}  "
              f"-{b['pct']}% -> -{a['pct']}%  unit {b['unit_after']} -> {a['unit_after']}  "
              f"total {b['order_total']} -> {a['order_total']}  "
              f"priced by {b.get('offer_key') or 'no offer'} -> {a.get('offer_key') or 'no offer'}{mark}")
    if prep["unowned_offers"]:
        print("Live offers this run does not own (listed, never written; verify will report them):")
        for x in prep["unowned_offers"]:
            print(f"  offer {x['id']} {x['name']!r}")
    print("Requests this will send, in order (each object is re-read just before and just after):")
    i = 0
    for o in prep["objects"]:
        i += 1
        print(f"  {i:>2}. PATCH {o['path']}  {json.dumps(o['patch'])}")
    for a in prep["adds"]:
        i += 1
        print(f"  {i:>2}. POST /api/admin/campaigns/{prep['campaign_id']}/offers/  {json.dumps(a['body'])}")
        if a["paused"]:
            i += 1
            print(f"  {i:>2}. PATCH /api/admin/campaigns/{prep['campaign_id']}/offers/<{a['key']}>/  "
                  + json.dumps({"available": False}))
    if prep["adds"]:
        print("Undo of an added offer pauses it; edit never deletes. teardown removes it with the rest.")
    print(f"Edit SHA-256: {prep['edit_sha256']}")


def _journal(man: Manifest, bucket: str, label: str, status: str, scope: str = None) -> None:
    pe = man.data["pending_edit"]
    (pe[scope] if scope else pe)[bucket][label] = status
    man.save()


def edit_apply(client: Client, man: Manifest, plan_path: Path, plan_text: str, prep: dict,
               live_traffic: bool, undoes=None) -> None:
    """Run a prepared, approved edit: receipt first, then the journal, then the
    writes."""
    n = len(man.data.get("edits", [])) + 1
    name = f"edit-{n}-receipt.json"
    # A file already at this name belongs to an edit that stopped before it was
    # journalled, so it wrote nothing: the number is only taken once an edit is
    # recorded in the manifest, and the run-directory lock keeps two apart.
    path = man.path.parent / name
    receipt = {
        "edit": n, "kind": prep["kind"], "undoes": undoes, "run_id": man.data["run_id"], "created_at": utcnow(),
        "store_origin": man.data["store_origin"], "campaign_id": prep["campaign_id"],
        "edit_sha256": prep["edit_sha256"], "live_traffic_acknowledged": bool(live_traffic),
        "spec": prep["spec"], "old_plan_sha256": prep["old_plan_sha256"], "old_plan_text": plan_text,
        "new_plan_sha256": prep["new_plan_sha256"], "new_plan": prep["new_plan"],
        "preexisting_offer_ids": prep["preexisting_offer_ids"], "unowned_offers": prep["unowned_offers"],
        "objects": prep["objects"], "adds": prep["adds"],
    }
    atomic_write_json(path, receipt)
    man.data["pending_edit"] = {
        "edit": n, "kind": prep["kind"], "receipt": name, "started_at": utcnow(),
        "old_plan_sha256": prep["old_plan_sha256"], "new_plan_sha256": prep["new_plan_sha256"],
        "objects": {f"{o['section']}:{o['key']}": "pending" for o in prep["objects"]},
        "adds": {a["key"]: "pending" for a in prep["adds"]},
    }
    man.save()
    print(f"saved before-images to {path}")
    edit_run(client, man, plan_path, receipt)


def _pause_added(client: Client, man: Manifest, cid, a: dict, package_ids: dict, expect_paused: bool) -> None:
    """Bring a created, edit-added offer to its intended availability and prove
    the whole offer by read-back."""
    e = man.entry("offers", a["key"])
    obj = {"section": "offers", "key": a["key"], "id": e["id"],
           "path": f"/api/admin/campaigns/{cid}/offers/{e['id']}/"}
    live = _read_object(client, obj)

    def problems_with(read, paused):
        found = _offer_vs_plan(read, dict(a["offer"], available=not paused), package_ids, True, expect_paused)
        if read["name"] != a["offer"]["name"]:
            found.append(f"name is {read['name']!r}, not the {a['offer']['name']!r} that was sent")
        return found

    if expect_paused and live["available"] is not False:
        # Identity and fields first: nothing is written to an offer that is not
        # the one this edit created, as it was created.
        early = problems_with(live, False)
        if early:
            raise CampaignAdminError(f"offer {a['key']} (id {e['id']}) is not what this edit created; it was "
                                     "not paused or changed:\n  - " + "\n  - ".join(early))
        status, resp = client.request("PATCH", obj["path"], {"available": False})
        if status != 200:
            raise CampaignAdminError(
                f"offer {a['key']} (id {e['id']}) was created and is LIVE, but pausing it returned {status}: "
                f"{_short(resp)}; not retried. Re-run the same edit to try the pause again."
                + auth_hint(status, "PATCH", obj["path"]))
        live = _read_object(client, obj)
    problems = problems_with(live, expect_paused)
    if problems:
        raise CampaignAdminError(f"offer {a['key']} (id {e['id']}) was created but its read-back is not what "
                                 "was intended; stopped, not retried:\n  - " + "\n  - ".join(problems))
    man.mark("offers", a["key"], pause_pending=False)


def edit_run(client: Client, man: Manifest, plan_path: Path, receipt: dict) -> None:
    """Carry an edit from wherever its journal says it is to done. Each object
    is classified by what the store shows now: the intended result means done,
    the saved before-image means write, anything else stops."""
    pe = man.data["pending_edit"]
    cid = man.data["campaign"]["id"]
    undo_hint = f" Roll back with: {PROG} edit --manifest {man.path} --plan {plan_path} --undo " \
                f"{man.path.parent / pe['receipt']}"
    for obj in receipt["objects"]:
        label = f"{obj['section']}:{obj['key']}"
        live = _read_object(client, obj)
        if pe["objects"].get(label) == "verified":
            # Written and proven on an earlier run. It is read again so the plan
            # is never committed over a store that has moved since.
            if live != obj["expected"]:
                raise CampaignAdminError(
                    f"{label} (id {obj['id']}) was edited and verified, and has changed again since. The "
                    "plan was not rewritten." + undo_hint)
            continue
        if live != obj["expected"]:
            if live != obj["before"]:
                raise CampaignAdminError(
                    f"{label} (id {obj['id']}) changed since it was read: it is neither the saved "
                    "before-image nor this edit's result. Nothing was written to it." + undo_hint)
            _journal(man, "objects", label, "sending")
            status, resp = client.request("PATCH", obj["path"], obj["patch"])
            if status != 200:
                raise CampaignAdminError(
                    f"PATCH {obj['path']} returned {status}: {_short(resp)}; not retried. Re-run the same "
                    "edit to continue." + undo_hint + auth_hint(status, "PATCH", obj["path"]))
            live = _read_object(client, obj)
            if live != obj["expected"]:
                raise CampaignAdminError(
                    f"PATCH {obj['path']} answered 200 but the read-back is not the intended state; stopped, "
                    f"not retried.\n  intended: {json.dumps(obj['expected'], sort_keys=True)}\n  live:     "
                    f"{json.dumps(live, sort_keys=True)}\n " + undo_hint)
            print(f"edited {obj['section'][:-1]} {obj['key']} (id {obj['id']})")
        _journal(man, "objects", label, "verified")

    package_ids = {e["key"]: e["id"] for e in man.data["packages"] if e.get("status") == "created" and e.get("id")}
    for a in receipt["adds"]:
        if pe["adds"].get(a["key"]) == "verified":
            continue
        e = man.entry("offers", a["key"])
        if not (e and e.get("status") == "created" and e.get("id")):
            # A journalled entry with no id is a POST whose answer was lost: look
            # for it before sending another.
            hit = find_added_offer(client, cid, man, receipt, a["offer"]["name"]) if e else None
            if hit:
                man.mark("offers", a["key"], status="created", id=hit["id"], name=hit.get("name"),
                         intent=None, reconciled=True)
            elif not create_offer(client, man, cid, a["offer"], package_ids, pause_pending=a["paused"]):
                raise CampaignAdminError("the offers endpoint answered 404/405: this store does not accept "
                                         "offers over the API, so one cannot be added here." + undo_hint)
        _pause_added(client, man, cid, a, package_ids, a["paused"])
        _journal(man, "adds", a["key"], "verified")

    # Plan first, then the manifest's hash. A stop between the two leaves the new
    # plan on disk and pending_edit naming its hash, which this function accepts.
    atomic_write_bytes(plan_path, json_bytes(receipt["new_plan"]))
    man.data["plan_sha256"] = pe["new_plan_sha256"]
    man.data.setdefault("edits", []).append({
        "edit": pe["edit"], "kind": pe.get("kind", "edit"), "receipt": pe["receipt"], "applied_at": utcnow(),
        "edit_sha256": receipt["edit_sha256"], "old_plan_sha256": pe["old_plan_sha256"],
        "new_plan_sha256": pe["new_plan_sha256"]})
    del man.data["pending_edit"]
    man.save()


def inverse_spec(receipt: dict) -> dict:
    """The edit file that puts back what a finished edit changed. Fields return
    to their saved values; an offer the edit added is paused, never deleted."""
    old = json.loads(receipt["old_plan_text"])
    new = receipt["new_plan"]
    old_pk = {p["key"]: p for p in old["packages"]}
    old_of = {o["key"]: o for o in old.get("offers", [])}
    new_of = {o["key"]: o for o in new.get("offers", [])}
    ops = []
    for p in new["packages"]:
        if p["price"] != old_pk[p["key"]]["price"]:
            ops.append({"op": "set_package_price", "package_keys": [p["key"]], "price": old_pk[p["key"]]["price"]})
    for k, o in old_of.items():
        n = new_of[k]
        ben = {}
        if o["benefit"]["value"] != n["benefit"]["value"]:
            ben["value"] = o["benefit"]["value"]
        if (o["benefit"].get("price_rounding") or None) != (n["benefit"].get("price_rounding") or None):
            ben["price_rounding"] = o["benefit"].get("price_rounding") or None
        if ben:
            ops.append(dict({"op": "set_offer_benefit", "offer_key": k}, **ben))
        if (o["condition"]["type"], o["condition"].get("value")) != (n["condition"]["type"], n["condition"].get("value")):
            cond = {"op": "set_offer_condition", "offer_key": k, "type": o["condition"]["type"]}
            if o["condition"]["type"] == "count":
                cond["value"] = o["condition"]["value"]
            ops.append(cond)
        if o["condition"]["package_keys"] != n["condition"]["package_keys"]:
            ops.append({"op": "set_offer_scope", "offer_key": k, "package_keys": o["condition"]["package_keys"]})
        if _offer_live(o) != _offer_live(n):
            ops.append({"op": "set_offer_available", "offer_key": k, "available": _offer_live(o)})
    for a in receipt.get("adds", []):
        if _offer_live(new_of[a["key"]]):
            ops.append({"op": "set_offer_available", "offer_key": a["key"], "available": False})
    if not ops:
        raise CampaignAdminError("nothing to undo: that edit only added offers that are already paused")
    return {"operations": ops, "note": f"undo of edit {receipt['edit']}"}


def rollback_prepare(client: Client, man: Manifest, receipt: dict) -> dict:
    """What rolling back an unfinished edit will do, from the journal and the
    live state. Nothing is written and nothing is ever created."""
    pe = man.data["pending_edit"]
    cid = man.data["campaign"]["id"]
    actions = []
    for obj in receipt["objects"]:
        label = f"{obj['section']}:{obj['key']}"
        if pe["objects"].get(label) == "pending":
            actions.append([label, "leave", "this edit never wrote to it"])
            continue
        live = _read_object(client, obj)
        if live == obj["before"]:
            actions.append([label, "none", "already at its saved before-image"])
        elif live == obj["expected"]:
            actions.append([label, "restore", json.dumps(obj["restore"])])
        else:
            raise CampaignAdminError(
                f"{label} (id {obj['id']}) is neither its saved before-image nor this edit's result: it was "
                "changed elsewhere. Rollback will not guess. Set it by hand to one or the other, then re-run.")
    for a in receipt["adds"]:
        e = man.entry("offers", a["key"])
        if e and e.get("status") == "created" and e.get("id"):
            actions.append([f"offers:{a['key']}", "pause", f"created as id {e['id']}; kept and paused"])
        elif e and find_added_offer(client, cid, man, receipt, a["offer"]["name"]):
            actions.append([f"offers:{a['key']}", "claim", "created but its answer was lost; claimed, kept and paused"])
        else:
            actions.append([f"offers:{a['key']}", "drop", "never created; removed from the manifest"])
    return {"actions": actions,
            "sha256": canonical_sha256({"rollback_of": receipt["edit_sha256"],
                                        "actions": [a[:2] for a in actions]})}


def rollback_execute(client: Client, man: Manifest, plan_path: Path, receipt: dict, prep: dict) -> None:
    pe = man.data["pending_edit"]
    cid = man.data["campaign"]["id"]
    if "rollback" not in pe:
        pe["rollback"] = {"approved_sha256": prep["sha256"], "started_at": utcnow(), "objects": {},
                          "adds": {}, "plan_sha256": None}
        man.save()
    rb = pe["rollback"]
    by_label = {f"{o['section']}:{o['key']}": o for o in receipt["objects"]}
    adds = {f"offers:{a['key']}": a for a in receipt["adds"]}
    package_ids = {e["key"]: e["id"] for e in man.data["packages"] if e.get("status") == "created" and e.get("id")}
    kept, dropped = [], []
    for label, action, _ in prep["actions"]:
        if label in by_label:
            obj = by_label[label]
            if action == "restore":
                # Same rule as a forward write: re-read just before it, and never
                # overwrite a state this edit did not leave.
                if _read_object(client, obj) != obj["expected"]:
                    raise CampaignAdminError(
                        f"{label} (id {obj['id']}) changed since the rollback preview read it; nothing was "
                        "written to it. Re-run the undo to see where it stands.")
                status, resp = client.request("PATCH", obj["path"], obj["restore"])
                if status != 200:
                    raise CampaignAdminError(f"PATCH {obj['path']} returned {status}: {_short(resp)}; not "
                                             "retried. Re-run the same undo to continue."
                                             + auth_hint(status, "PATCH", obj["path"]))
                live = _read_object(client, obj)
                if live != obj["before"]:
                    raise CampaignAdminError(
                        f"PATCH {obj['path']} answered 200 but the read-back is not the saved before-image; "
                        f"stopped, not retried.\n  before: {json.dumps(obj['before'], sort_keys=True)}\n  "
                        f"live:   {json.dumps(live, sort_keys=True)}")
                print(f"restored {obj['section'][:-1]} {obj['key']} (id {obj['id']})")
            _journal(man, "objects", label, "done" if action != "leave" else "left", scope="rollback")
            continue
        a = adds[label]
        if action == "drop":
            entry = man.entry("offers", a["key"])
            if entry is not None:
                man.data["offers"].remove(entry)
            dropped.append(a["key"])
            _journal(man, "adds", a["key"], "dropped", scope="rollback")
            continue
        if action == "claim":
            hit = find_added_offer(client, cid, man, receipt, a["offer"]["name"])
            if not hit:
                raise CampaignAdminError(f"offer {a['key']} was live a moment ago and is now gone; re-run the undo")
            man.mark("offers", a["key"], status="created", id=hit["id"], name=hit.get("name"),
                     intent=None, reconciled=True)
        _pause_added(client, man, cid, a, package_ids, True)
        kept.append(a)
        _journal(man, "adds", a["key"], "paused", scope="rollback")

    # The plan a rollback leaves is the old plan, plus any offer the edit did
    # create, kept and paused. It is a third plan, so its hash is journalled
    # before the file is replaced.
    if kept:
        plan = json.loads(receipt["old_plan_text"])
        for a in kept:
            plan.setdefault("offers", []).append(dict(a["offer"], available=False))
        raw = json_bytes(plan)
    else:
        raw = receipt["old_plan_text"].encode()
    sha = hashlib.sha256(raw).hexdigest()
    name = f"edit-{pe['edit']}-rollback.json"
    atomic_write_json(man.path.parent / name, {
        "edit": pe["edit"], "run_id": man.data["run_id"], "rolled_back_at": utcnow(),
        "approved_sha256": rb["approved_sha256"], "actions": prep["actions"],
        "kept_added_offers": [a["key"] for a in kept], "dropped_added_offers": dropped,
        "plan_sha256": sha, "plan_text": raw.decode()})
    rb["plan_sha256"] = sha
    man.save()
    atomic_write_bytes(plan_path, raw)
    man.data["plan_sha256"] = sha
    man.data.setdefault("edits", []).append({
        "edit": pe["edit"], "kind": "rolled_back", "receipt": pe["receipt"], "rollback": name,
        "rolled_back_at": utcnow(), "old_plan_sha256": pe["old_plan_sha256"], "new_plan_sha256": sha,
        "kept_added_offers": [a["key"] for a in kept], "dropped_added_offers": dropped})
    del man.data["pending_edit"]
    man.save()


def print_rollback(prep: dict, man: Manifest, receipt: dict) -> None:
    camp = man.data["campaign"]
    print(f"Rollback target: {man.data['store_origin']} campaign {camp['id']} {camp.get('name')!r}, "
          f"unfinished edit {receipt['edit']}")
    for label, action, detail in prep["actions"]:
        print(f"  {label}  {action}  {detail}")
    print("Rollback never creates or deletes anything. An offer the edit created is kept and paused.")


EDIT_LOCK_NAME = "edit.lock"


def _unchanged_under_lock(man: Manifest) -> None:
    """With the lock held, the manifest on disk must still be the one this
    command read and previewed from; another edit may have finished in between."""
    if load_json(man.path) != man.data:
        raise CampaignAdminError("the run manifest changed while this edit was being prepared (another "
                                 "edit ran); nothing was written. Preview again.")


class EditLock:
    """One edit at a time in a run directory. Two approved edits started together
    would otherwise pick the same receipt number and overwrite each other's
    journal. The lock is a file created exclusively; a run that dies leaves it
    behind, and the message says how to clear it."""

    def __init__(self, run_dir: Path):
        self.path = Path(run_dir) / EDIT_LOCK_NAME

    def __enter__(self):
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise CampaignAdminError(
                f"another edit is running in this run directory ({self.path} exists). If none is, a "
                "previous edit was cut short: delete that file, then re-run the same command to finish "
                "or undo it.")
        with os.fdopen(fd, "w") as f:
            f.write(f"{os.getpid()} {utcnow()}\n")
        return self

    def __exit__(self, *exc):
        try:
            os.unlink(self.path)
        except OSError:
            pass
        return False


def run_edit(args) -> int:
    plan_path = Path(args.plan).expanduser()
    if plan_path.is_symlink():
        raise CampaignAdminError(f"plan {plan_path} is a symlink; refusing to write through it")
    plan, plan_sha = load_json_and_hash(plan_path)
    plan_text = plan_path.read_bytes().decode()
    if hashlib.sha256(plan_text.encode()).hexdigest() != plan_sha:
        raise CampaignAdminError(f"{plan_path} changed while it was being read; run the command again")
    errs = validate_plan(plan, for_create=False)
    if errs:
        raise CampaignAdminError("plan is invalid, cannot edit:\n  - " + "\n  - ".join(errs))
    # Every edit path saves to the manifest and writes receipts beside it.
    man_path = guard_manifest_path(args.manifest)
    man = Manifest.load(man_path)
    pe = man.data.get("pending_edit")
    if args.changes and args.undo:
        raise CampaignAdminError("pass --changes or --undo, not both")
    if not args.changes and not args.undo and not pe:
        raise CampaignAdminError("pass --changes <edit file> to edit, or --undo <receipt> to undo an edit")
    client = _client_for(plan["store_slug"])
    base = f"{PROG} edit --manifest {man_path} --plan {plan_path}"

    def gate(sha, command, extra=""):
        if args.yes and args.edit_sha256 == sha:
            return True
        print(f"\nNOT APPLIED: nothing was written.{extra} Approve this exact change with:\n  "
              f"{command} --yes --edit-sha256 {sha}", file=sys.stderr)
        return False

    if args.undo:
        receipt = load_json(args.undo)
        if receipt.get("run_id") != man.data.get("run_id"):
            raise CampaignAdminError(f"{args.undo} is not a receipt of this run")
        if pe and pe.get("edit") == receipt.get("edit"):
            _edit_ownership(client, man, plan, plan_sha, accepted=pending_edit_hashes(man))
            receipt = load_edit_receipt(man, pe)
            prep = rollback_prepare(client, man, receipt)
            print_rollback(prep, man, receipt)
            # A rollback that was approved and then interrupted continues under
            # that approval: its remaining actions shrink as it progresses.
            sha = (pe.get("rollback") or {}).get("approved_sha256") or prep["sha256"]
            print(f"Edit SHA-256: {sha}")
            if not gate(sha, f"{base} --undo {args.undo}"):
                return 2
            with EditLock(man_path.parent):
                _unchanged_under_lock(man)
                rollback_execute(client, man, plan_path, receipt, prep)
            print(f"\nDONE: edit {receipt['edit']} rolled back; plan and manifest are in line")
            return 0
        if pe:
            raise CampaignAdminError(PENDING_EDIT_MSG + f" (edit {pe.get('edit')}) before undoing another.")
        done = [x for x in man.data.get("edits", []) if x.get("kind") != "rolled_back"]
        last = man.data.get("edits", [])[-1] if man.data.get("edits") else None
        if not last or last not in done or last.get("edit") != receipt.get("edit") \
                or receipt.get("new_plan_sha256") != man.data["plan_sha256"]:
            raise CampaignAdminError(
                f"edit {receipt.get('edit')} is not this run's most recent applied edit; undo works "
                "backwards from the latest one")
        spec, kind, undoes = inverse_spec(receipt), "undo", receipt["edit"]
        command = f"{base} --undo {args.undo}"
    elif pe:
        if pe.get("rollback"):
            raise CampaignAdminError(
                f"edit {pe.get('edit')} is being rolled back; finish with: {base} --undo "
                f"{man_path.parent / pe['receipt']}")
        receipt = load_edit_receipt(man, pe)
        if args.changes and load_json(args.changes) != receipt["spec"]:
            raise CampaignAdminError(PENDING_EDIT_MSG + " before starting a different one.")
        _edit_ownership(client, man, plan, plan_sha,
                        accepted={pe["old_plan_sha256"], pe["new_plan_sha256"]})
        print(f"Edit {pe['edit']} is unfinished. Recorded progress:")
        for label, st in list(pe["objects"].items()) + [(f"offers:{k} (added)", v) for k, v in pe["adds"].items()]:
            print(f"  {label}  {st}")
        print(f"Edit SHA-256: {receipt['edit_sha256']}")
        resume = base + (f" --changes {args.changes}" if args.changes else "")
        if not gate(receipt["edit_sha256"], resume, " This continues the edit that was already approved."):
            return 2
        with EditLock(man_path.parent):
            _unchanged_under_lock(man)
            edit_run(client, man, plan_path, receipt)
        print(f"\nDONE: edit {receipt['edit']} finished; plan and manifest are in line. Run verify.")
        return 0
    else:
        spec, kind, undoes = load_json(args.changes), "edit", None
        command = f"{base} --changes {args.changes}"

    prep = edit_prepare(client, man, plan, plan_sha, spec, kind=kind)
    print_edit(prep, plan, man)
    if not gate(prep["edit_sha256"], command + " --live-traffic <yes|no>"):
        return 2
    if args.live_traffic is None:
        print("\nNOT APPLIED: nothing was written. Say whether a funnel is sending shoppers to this campaign "
              "right now with --live-traffic yes or --live-traffic no; the answer is recorded in the receipt.",
              file=sys.stderr)
        return 2
    with EditLock(man_path.parent):
        _unchanged_under_lock(man)
        edit_apply(client, man, plan_path, plan_text, prep, args.live_traffic == "yes", undoes=undoes)
    n = man.data["edits"][-1]["edit"]
    print(f"\nDONE: edit {n} applied; plan and manifest are in line. Run verify.")
    print(f"  undo with: {base} --undo {man_path.parent / man.data['edits'][-1]['receipt']}")
    return 0


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
    r.add_argument("--shipping", action="append", required=True,
                   help="<code>:<price>[:<key>], repeatable; each store code once (one campaign method per code)")
    r.add_argument("--name", help="campaign name (default: hero product title)")
    r.add_argument("--gateway-group", type=int)
    r.add_argument("--payment-methods")
    r.add_argument("--express-methods")
    r.add_argument("--currency")
    r.add_argument("--language")
    r.add_argument("--countries", help="comma-separated ISO alpha-2")
    r.add_argument("--offer-type", choices=["quantity", "bxgy", "gwp"], default="quantity",
                   help="quantity (Buy 1/2/3, default), bxgy (buy-X-get-Y approximation), or gwp (gift with purchase)")
    r.add_argument("--paid-qty", type=int, help="paid units for --offer-type bxgy")
    r.add_argument("--free-qty", type=int, help="free units for --offer-type bxgy")
    r.add_argument("--gift", action="append",
                   help="<variant_id>:<price>[:<qty>], repeatable; gift package plus a 100%% offer scoped to it")
    r.add_argument("--gift-mode", choices=["auto", "select"], default="auto",
                   help="funnel handoff only: auto = silent noSlot add, select = customer picks (default auto)")
    r.add_argument("--tiers", help="comma-separated percentages for Buy 1/2/3 (default 50,55,60; quantity only)")
    r.add_argument("--exit", help="exit-pop voucher percentage (default 10; 0 disables)")
    r.add_argument("--exit-code")
    r.add_argument("--short-name", action="append",
                   help="<product_id>:<NAME>, repeatable; the product's voucher code name (A-Z0-9, starts with a letter, at most 12)")
    r.add_argument("--bump", action="append", help="<variant_id>:<price>, repeatable")
    r.add_argument("--upsell", action="append", help="<variant_id>:<price>:<pct>, repeatable")
    r.add_argument("--free-shipping", action="store_true", help="free shipping on every checkout order")
    r.add_argument("--free-shipping-min-qty", type=int, metavar="N",
                   help="free shipping only from N hero units, e.g. 2 for Buy 2+ (instead of --free-shipping)")
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

    e = sub.add_parser("edit", help="change what this run created, in place (gated)")
    e.add_argument("--manifest", required=True)
    e.add_argument("--plan", required=True)
    e.add_argument("--changes", help="edit file (campaign-edit.json) listing the operations")
    e.add_argument("--undo", help="receipt of the edit to undo (edit-<n>-receipt.json)")
    e.add_argument("--yes", action="store_true")
    e.add_argument("--edit-sha256")
    e.add_argument("--live-traffic", choices=["yes", "no"],
                   help="whether a funnel is sending shoppers to this campaign now; recorded in the receipt")

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

    if args.cmd == "edit":
        return run_edit(args)

    if args.cmd == "metadata":
        slug = normalize_store(args.store)
        return metadata_provision(_client_for(slug), slug, args.apply)
    return 1  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
