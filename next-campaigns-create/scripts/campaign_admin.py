#!/usr/bin/env python3
"""Provision a Campaigns App campaign over the NEXT Admin API.

Backs the `/next-campaigns-create` skill and is normally run through
`next-campaigns-create.sh`. The create path, in the usual order:

    discover  --store <slug>                      read-only store snapshot
    metadata  --store <slug> [--apply]            audit or create the campaign metadata definitions
    recommend --discovery ... --hero ... --ctc ... [--offer-type quantity|bxgy|gwp] build campaign-plan.json
    plan      --plan campaign-plan.json           validate + print requests + hash
    apply     --plan ... --yes --plan-sha256 ...  create campaign/packages/shipping/offers
    verify    --manifest ... --plan ...           read back + Cart API calculate
    teardown  --manifest ... --plan ... --yes     delete what THIS run created

And the update path, for a campaign that already exists:

    adopt     --store <slug> --campaign <id>      write a plan and manifest for a live campaign
    diff      --plan <edited copy> --manifest ... three-way diff -> change-set.json (read-only)
    update    --plan <merged> --manifest ... --change-set ... --yes --change-set-sha256 ...
                                                  apply that reviewed change set

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
import contextlib
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
# Where a plan's desired state came from: a run of this skill, or a campaign that
# already existed and was adopted. A plan or manifest without the field was
# written before the field existed, so it was created.
PLAN_ORIGINS = ("created", "adopted")
DEFAULT_TIERS = (50, 55, 60)
DEFAULT_EXIT_PCT = 10

PROG = "next-campaigns-create.sh"
RUNS_DIR_NAME = "next-campaigns-create-runs"
MANIFEST_NAME = "run-manifest.json"
PLAN_NAME = "campaign-plan.json"
RUN_LOCK_NAME = ".run.lock"
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
    """The exact bytes `atomic_write_json` writes for `data`. `diff` serialises the
    merged plan through here once, hashes those bytes and writes them, so the hash
    in the change set is the hash of the file on disk and promotion can copy the
    bytes rather than re-serialise them."""
    return (json.dumps(data, indent=2, sort_keys=False) + "\n").encode()


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        if hasattr(os, "fchmod"):  # not on Windows; mode 600 is not enforced there
            os.fchmod(fd, mode)  # restrict before any secret bytes are written
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, data, mode: int = 0o600) -> None:
    atomic_write_bytes(path, json_bytes(data), mode)


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


@contextlib.contextmanager
def run_lock(run_dir):
    """Exclusive access to one run directory, for the whole life of a command that
    can write it. `Manifest.save` gives persistence, not exclusion: two updates could
    both pass the baseline and `active_update` checks before either saves, then send
    duplicate writes. The lock is a file created with O_CREAT | O_EXCL (stdlib only,
    so it works where `fcntl` does not) holding the pid and start time of the holder,
    and removed in a `finally`. A stale lock is removed by the operator: this never
    decides on its own that another process is gone."""
    path = Path(run_dir) / RUN_LOCK_NAME
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        try:
            held = json.loads(path.read_text())
        except (OSError, ValueError):
            held = {}
        raise CampaignAdminError(
            f"another {PROG} command holds this run directory: {path} (pid {held.get('pid')}, "
            f"started {held.get('started_at')}). Wait for it to finish. If that process is gone, "
            f"delete {path} by hand; it is never removed automatically.")
    try:
        try:
            os.write(fd, json_bytes({"pid": os.getpid(), "started_at": utcnow()}))
        finally:
            os.close(fd)
        yield path
    finally:
        try:
            os.unlink(path)
        except OSError:  # pragma: no cover - the operator removed it mid-run
            pass


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
    if "origin" in plan and plan["origin"] not in PLAN_ORIGINS:
        errs.append(f"plan origin {plan['origin']!r} invalid; one of {', '.join(PLAN_ORIGINS)} "
                    "(a plan with no origin was created by a run of this skill)")
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
        # An offer the dashboard switched off is still part of the campaign and
        # still owned, so it is modelled rather than dropped; the pricing gates
        # skip it because it fires on no cart.
        if "available" in o and type(o["available"]) is not bool:
            errs.append(f"offer {k}: available must be true or false (got {o['available']!r})")
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
    if "available" in o:
        # Omitted means the API's own default (true); sent only when the plan says
        # so, so a create path that never mentions availability is unchanged.
        body["available"] = o["available"]
    if body["offer_type"] == "voucher":
        body["code"] = o["code"]
    return body


# Campaign fields an update may change, by how they are cleared. `currency` is
# absent on purpose: it is immutable once the campaign exists.
CAMPAIGN_SCALARS = ("name", "language", "payment_gateway_group_id")
CAMPAIGN_CODE_LISTS = ("available_payment_methods", "available_express_payment_methods",
                       "available_shipping_countries")
CAMPAIGN_NULLABLE = ("statement_descriptor", "paypal_account_id")
# Plan-space field names the diff walks per section. Plan-only fields (role,
# variant_title, image) are merged but never produce an op.
PACKAGE_DIFF_FIELDS = ("name", "price", "price_recurring", "interval", "interval_count")
OFFER_DIFF_FIELDS = ("name", "offer_type", "code", "available", "condition_type",
                     "condition_value", "package_keys", "benefit_type", "benefit_value",
                     "price_rounding")


def plan_campaign_n(c: dict) -> dict:
    """A plan's campaign in the same shape `normalize_snapshot` produces, so desired
    and live are compared field by field without either side being re-read."""
    return {
        "name": c.get("name"), "currency": c.get("currency"), "language": c.get("language"),
        "payment_gateway_group_id": c.get("payment_gateway_group_id"),
        "paypal_account_id": c.get("paypal_account_id") or None,
        "statement_descriptor": c.get("statement_descriptor") or None,
        "additional_currencies": sorted(c.get("additional_currencies") or []),
        "available_payment_methods": sorted(c.get("available_payment_methods") or []),
        "available_express_payment_methods": sorted(c.get("available_express_payment_methods") or []),
        "available_shipping_countries": sorted(c.get("available_shipping_countries") or []),
    }


def plan_package_n(p: dict) -> dict:
    """A plan package in normalised shape. The recurring trio is absent-or-set as one
    thing: a package the plan does not price recurringly has all three at None, which
    is what the store reads back for it."""
    rec = p.get("price_recurring")
    return {
        "name": p.get("name"), "price": money(D(p["price"])) if p.get("price") is not None else None,
        "price_recurring": money(D(rec)) if rec else None,
        "interval": (p.get("interval", "month") or None) if rec else None,
        "interval_count": (p.get("interval_count", 1)) if rec else None,
    }


def plan_offer_n(o: dict) -> dict:
    """A plan offer in normalised shape, with the scope as package KEYS. Live offer
    ids are translated into keys through the manifest before anything is compared, so
    the whole diff happens in plan space."""
    cond, ben = o.get("condition") or {}, o.get("benefit") or {}
    return {
        "name": o.get("name"), "offer_type": o.get("offer_type", "offer"),
        "code": o.get("code") or None, "available": o.get("available", True),
        "condition_type": cond.get("type"),
        "condition_value": cond.get("value") if cond.get("type") == "count" else None,
        "package_keys": sorted(cond.get("package_keys") or []),
        "benefit_type": ben.get("type"),
        "benefit_value": money(D(ben["value"])) if ben.get("value") is not None else None,
        "price_rounding": ben.get("price_rounding") or None,
    }


def campaign_patch_body(live_n: dict, desired_n: dict) -> dict:
    """Every campaign field whose desired value differs from live, with the explicit
    clearing value the API wants: `[]` for the code lists, `null` for
    `additional_currencies`, `statement_descriptor` and `paypal_account_id`. Never
    `currency`, which cannot change."""
    body = {}
    for f in CAMPAIGN_SCALARS + CAMPAIGN_NULLABLE:
        if desired_n.get(f) != live_n.get(f):
            body[f] = desired_n.get(f)
    for f in CAMPAIGN_CODE_LISTS:
        want = list(desired_n.get(f) or [])
        if want != list(live_n.get(f) or []):
            body[f] = want
    want = list(desired_n.get("additional_currencies") or [])
    if want != list(live_n.get("additional_currencies") or []):
        body["additional_currencies"] = want or None
    return body


def package_patch_body(live_n: dict, desired: dict, currency: str) -> dict:
    """A package PATCH: the changed name, the whole `prices` list when either price
    moved, and the recurring pair when it changed. `prices` is a list because the
    package update endpoint replaces it; `price` on its own is create-only."""
    want = plan_package_n(desired)
    body = {}
    if want["name"] != live_n.get("name"):
        body["name"] = want["name"]
    if want["price"] != live_n.get("price") or want["price_recurring"] != live_n.get("price_recurring"):
        body["prices"] = [{"currency": currency, "price": want["price"],
                           "price_recurring": want["price_recurring"]}]
    for f in ("interval", "interval_count"):
        if want[f] != live_n.get(f):
            body[f] = want[f]
    return body


def shipping_patch_body(live_n: dict, desired: dict, currency: str) -> dict:
    """A campaign shipping method PATCH: prices only. Its store code is its identity
    and sending another method's code is a 400, so the diff refuses a code change
    instead of trying to send one."""
    want = money(D(desired["price"]))
    if want == live_n.get("price"):
        return {}
    return {"prices": [{"currency": currency, "price": want}]}


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
            "origin": "created",
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


def manifest_origin(man: Manifest) -> str:
    """How this run's campaign came to be ours. Manifests written before the field
    existed (0.7.5 and earlier) are all from a run that created the campaign, so a
    missing value reads as `created`."""
    return man.data.get("origin") or "created"


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


def find_pending_candidates(client: Client, man: Manifest, plan: dict, section: str, key: str,
                            items=None) -> list:
    """The live objects that could be the one `key` names, by identity only. Read-only:
    it never marks the manifest and never claims anything, so a caller can read a
    candidate back in full before deciding it is ours. `items` is the section's live
    list when the caller already holds it (`reconcile` reads one list per section with
    something pending); without it the collection is read here."""
    if items is None:
        cid = (man.data.get("campaign") or {}).get("id")
        items = client.paginate(f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/")
    if section == "packages":
        p = next((x for x in plan.get("packages") or [] if x.get("key") == key), None)
        if p is None:
            raise CampaignAdminError(f"manifest package entry {key} is not in the plan")
        vid = p["product_variant_ids"][0] if p.get("product_variant_ids") else None
        return [x for x in items if x.get("product_variant_id") == vid
                and str(x.get("name", "")).startswith(p["name"])]
    if section == "shipping_methods":
        s = next((x for x in plan.get("shipping_methods") or [] if ship_key(x) == key), None)
        if s is None:
            raise CampaignAdminError(f"manifest shipping entry {key} is not in the plan")
        # The store returns no plan key, so a lost response is claimed by code and
        # price. Entries already journalled under another key are never candidates
        # (recomputed per call, so one claimed a moment ago is excluded too).
        claimed = {x.get("id") for x in man.data.get("shipping_methods") or []
                   if x.get("status") == "created" and x.get("id") is not None}
        return [x for x in items if x.get("shipping_method") == s["shipping_method"]
                and x.get("id") not in claimed]
    o = next((x for x in plan.get("offers") or [] if x.get("key") == key), None)
    if o is None:
        raise CampaignAdminError(f"manifest offer entry {key} is not in the plan")
    return [x for x in items if x.get("name") == o["name"]]


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
    for e in man.data["packages"]:
        if e.get("status") == "pending":
            hit = find_pending_candidates(client, man, plan, "packages", e["key"], items=pkgs)
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
        cands = find_pending_candidates(client, man, plan, "shipping_methods", e["key"], items=ships)
        if not cands:
            # the only path that lets apply POST this entry again
            man.mark("shipping_methods", e["key"], status="absent")
            continue
        # Anything short of one exact price match among readable prices stops: a
        # re-POST from here could leave a duplicate method on the campaign.
        read = [(x.get("id"), _finite(_num(_price_in(x, currency)))) for x in cands]
        priced = [i for i, p in read if p is not None and p == D(s["price"])]
        if len(priced) == 1 and all(p is not None for _, p in read):
            man.mark("shipping_methods", e["key"], status="created", id=priced[0],
                     shipping_method=s["shipping_method"], reconciled=True, intent=None)
        else:
            seen = ", ".join(f"{i} at {'unreadable' if p is None else money(p)}" for i, p in read)
            raise CampaignAdminError(
                f"pending shipping method {e['key']} ({s['shipping_method']} at {s['price']}) matches "
                f"{len(priced)} remote entries at that price; unclaimed entries on that code: {seen}; resolve by hand")
    for e in man.data["offers"]:
        if e.get("status") == "pending":
            hit = find_pending_candidates(client, man, plan, "offers", e["key"], items=offers)
            if len(hit) == 1:
                man.mark("offers", e["key"], status="created", id=hit[0]["id"],
                         name=hit[0].get("name"), reconciled=True)
            elif not hit:
                man.mark("offers", e["key"], status="absent")
            else:
                raise CampaignAdminError(f"pending offer {e['key']} matches {len(hit)} remote offers")


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


def _put_image(client: Client, man: Manifest, cid, p: dict, e: dict, st: dict) -> None:
    """One package's image override, journalled. `st` is the state the caller keeps
    across packages: `unsupported` (this store has no image endpoint), `set_ok` (one
    PUT has already landed, so a 404 is about this package alone) and `failures`
    (lines for the caller's report). Shared by `apply` and `update`, so an approved
    image op behaves exactly as it does on the create path."""
    img = image_body(p)
    if img is None:
        return
    st_img = e.get("image_status")
    if st_img in ("set", "unsupported", "failed"):
        # Terminal. `unsupported` means the endpoint is absent; `failed` means the
        # server rejected this src, and the src cannot change without changing the
        # plan hash, so a retry would reproduce the same rejection forever.
        st["unsupported"] = st["unsupported"] or st_img == "unsupported"
        if st_img == "failed":
            st["failures"].append(f"{p['key']}: {e.get('image_error')}")
        return
    if st["unsupported"]:
        man.mark("packages", p["key"], image_status="unsupported", image_intent=None)
        return

    man.mark("packages", p["key"], image_status="pending", image_intent=img)
    status, resp = client.request("PUT", f"/api/admin/campaigns/{cid}/packages/{e['id']}/image/", img)
    if status in (404, 405):
        # 405 is unambiguous: the route exists and will not take a PUT. A 404 is not
        # — it is equally "this package id is gone". Only conclude the store lacks the
        # endpoint while nothing has proved otherwise; once one PUT has landed, the
        # route demonstrably exists and a 404 is about that package alone.
        if status == 405 or not st["set_ok"]:
            man.mark("packages", p["key"], image_status="unsupported", image_intent=None)
            st["unsupported"] = True
        else:
            # `unsupported` would be a lie here — it is documented as "this store has
            # no image endpoint", and one has already answered. This is a per-package
            # failure: terminal like any other, reported, and never store-wide.
            detail = "404 after another package's image landed; the package id is likely gone"
            man.mark("packages", p["key"], image_status="failed", image_intent=None,
                     image_error=detail)
            st["failures"].append(f"{p['key']}: {detail}; not retried")
    elif status is None or status == 429 or status >= 500:
        # Not a verdict on the request: the write may well have landed. Every 5xx
        # counts, not just the five the GET retry loop lists, because the promise in
        # SKILL.md is that a server-side failure is resumable. A PUT is a replace and
        # a resume runs against the identical plan, so leaving this `pending` makes it
        # retryable without any risk of a duplicate.
        man.mark("packages", p["key"], image_error=_short(resp))
        st["failures"].append(f"{p['key']}: no answer from the image endpoint "
                              f"(left pending, --resume will retry): {_short(resp)}")
    elif status != 200 or not isinstance(resp, dict) or not resp.get("image"):
        man.mark("packages", p["key"], image_status="failed", image_intent=None, image_error=_short(resp))
        st["failures"].append(f"{p['key']}: PUT returned {status}: {_short(resp)}"
                              + auth_hint(status, "PUT", f"/api/admin/campaigns/{cid}/packages/{e['id']}/image/"))
    else:
        man.mark("packages", p["key"], image_status="set", image_intent=None,
                 image=resp.get("image"), image_src=img["src"], image_error=None)
        st["set_ok"] = True
        print(f"set image on package {e['id']} {p['key']!r}")


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
        if manifest_origin(man) == "adopted":
            # Resume finishes a build this skill started. An adopted campaign was
            # built elsewhere: there is nothing half-created to claim, and every
            # POST here would duplicate something that already exists.
            raise CampaignAdminError(
                f"{man.path} was adopted from an existing campaign, not created by a run; apply --resume "
                "only finishes a run it started. Change an adopted campaign with diff and update.")
        if man.data["store_slug"] != plan["store_slug"]:
            raise CampaignAdminError("manifest store_slug does not match the plan")
        if man.data["plan_sha256"] != plan_sha:
            raise CampaignAdminError("manifest plan_sha256 does not match this plan file; resume needs the same plan")
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
    img_state = {"unsupported": False, "failures": [],
                 "set_ok": any(e.get("image_status") == "set" for e in man.data["packages"])}
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
        _put_image(client, man, cid, p, e, img_state)
    images_unsupported, image_failures = img_state["unsupported"], img_state["failures"]

    # shipping
    for s in plan["shipping_methods"]:
        key = ship_key(s)
        e = man.entry("shipping_methods", key)
        if e and e.get("status") == "created":
            continue
        body = {"shipping_method": s["shipping_method"], "price": s["price"]}
        man.mark("shipping_methods", key, status="pending", intent=body)
        resp = _created(client, "POST", f"/api/admin/campaigns/{cid}/shipping-methods/", body, f"create shipping {key}")
        # The store code goes in the entry: it is the shipping method's immutable
        # identity, and a manifest that carries it does not have to reach back into
        # the plan to know what it owns.
        man.mark("shipping_methods", key, status="created", id=resp["id"],
                 shipping_method=resp.get("shipping_method") or s["shipping_method"], intent=None)
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
        body = offer_body(o, package_ids)
        man.mark("offers", o["key"], status="pending", intent=body)
        status, resp = client.request("POST", f"/api/admin/campaigns/{cid}/offers/", body)
        if status in (404, 405):
            man.mark("offers", o["key"], status="unsupported", intent=None)
            offers_unsupported = True
            continue
        if status not in (200, 201) or not isinstance(resp, dict):
            raise CampaignAdminError(f"create offer {o['key']}: POST returned {status}: {_short(resp)}"
                                     + auth_hint(status, "POST", f"/api/admin/campaigns/{cid}/offers/"))
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
    if manifest_origin(man) == "adopted":
        raise CampaignAdminError(
            "teardown deletes what this run created; reduce an adopted campaign with update --allow-delete")
    if man.data["store_slug"] != plan["store_slug"] or man.data["plan_sha256"] != plan_sha:
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

    def ship_ident(e, x):
        # The GET is by the journalled id; the code must match, and so must the
        # price whenever the store reports one in the campaign currency.
        s = plan_sm.get(e["key"], {})
        if not s or x.get("shipping_method") != s.get("shipping_method"):
            return False
        live_price = _price_in(x, currency)
        # A price that is present but unreadable (NaN, Infinity, garbage) is not a
        # match: teardown fails closed on it, as resume does.
        return live_price is None or _finite(_num(live_price)) == _num(s.get("price"))

    # Phase 1: read everything back and verify identity BEFORE any DELETE.
    todo = []  # (section, key, path)
    status, live = client.request("GET", f"/api/admin/campaigns/{cid}/")
    if status == 404:
        man.mark("campaign", "campaign", status="deleted")
        print(f"campaign {cid} already gone; nothing left to delete")
        return
    if status != 200 or live.get("name") != camp.get("name") or not same_instant(live.get("created_at"), camp.get("created_at")):
        raise CampaignAdminError(f"campaign {cid} identity mismatch (name/created_at); refusing to delete anything"
                                 + (f" (GET returned {status})" + auth_hint(status, "GET", f"/api/admin/campaigns/{cid}/")
                                    if status in (401, 403) else ""))

    for section, path_part, ident in (
        ("offers", "offers", lambda e, x: x.get("name") == plan_of.get(e["key"], {}).get("name")),
        ("shipping_methods", "shipping-methods", ship_ident),
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


def _available(o: dict) -> bool:
    """Whether an offer fires at all. The field is optional and defaults to true,
    so only an explicit false switches an offer off."""
    return o.get("available") is not False


def _live_package_name_ok(live_name, p: dict) -> bool:
    """Whether the live package name is this plan entry's name. Package create
    appends " - {variant}" to the name it is sent, so the plan holds the bare name
    and both forms read back as a match. The plan's own `variant_title` is checked
    first; the prefix form covers a store whose suffix the plan does not record."""
    name = p.get("name") or ""
    if live_name == name:
        return True
    if not isinstance(live_name, str) or not name:
        return False
    variant = p.get("variant_title")
    if variant is not None and live_name == f"{name} - {variant}":
        return True
    return live_name.startswith(name + " - ")


def _free_shipping_offers(plan: dict) -> list:
    """Automatic 100% shipping offers as (key, threshold, scope_keys); `any` is a
    threshold of 1. Vouchers are skipped: no cart case enters a shipping code.
    Reads defensively because print_plan calls it on plans not yet validated."""
    out = []
    for o in plan.get("offers") or []:
        if not _available(o):
            continue
        ben, cond = o.get("benefit") or {}, o.get("condition") or {}
        if (o.get("offer_type", "offer") != "offer" or ben.get("type") != "shipping_percentage"
                or _num(ben.get("value")) != Decimal(100)):
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
        if o.get("offer_type", "offer") != "offer" or not _available(o):
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
    # An offer with `available: false` discounts nothing, so it builds no cart case
    # and never stacks into one (the exit voucher and the upsell lookups below).
    offers = {o["key"]: o for o in plan.get("offers", []) if _available(o)}
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
            if exit_offer:
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


def verify(client: Client, cart: Client, man: Manifest, plan: dict, plan_sha: str) -> dict:
    errs = validate_plan(plan, for_create=False)
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

    def row(name, result, detail=""):
        """A read-back row with its own verdict. Only FAIL fails the report:
        UNVERIFIED marks a field this store does not report, INFO something that
        was not provable and is not a defect."""
        checks.append({"check": name, "result": result, "detail": detail})

    def check(name, ok, detail=""):
        row(name, "PASS" if ok else "FAIL", detail)

    live = client.get_ok(f"/api/admin/campaigns/{cid}/")
    c = plan["campaign"]
    check("campaign.name", live.get("name") == c["name"], str(live.get("name")))
    check("campaign.currency", live.get("currency") == c["currency"], str(live.get("currency")))
    check("campaign.language", live.get("language") == c["language"], str(live.get("language")))
    check("campaign.gateway_group", live.get("payment_gateway_group_id") == c["payment_gateway_group_id"], str(live.get("payment_gateway_group_id")))
    live_methods = sorted(m["code"] for m in live.get("available_payment_methods", []))
    check("campaign.payment_methods", live_methods == sorted(c["available_payment_methods"]), str(live_methods))
    live_express = sorted(m["code"] for m in live.get("available_express_payment_methods", []))
    check("campaign.express_methods", live_express == sorted(c.get("available_express_payment_methods", [])), str(live_express))
    live_countries = sorted(x["code"] for x in live.get("available_shipping_countries", []))
    check("campaign.shipping_countries", live_countries == sorted(c.get("available_shipping_countries", [])), str(live_countries))
    # A campaign cleared of its extra currencies answers with null, not []
    live_addl = sorted(live.get("additional_currencies") or [])
    check("campaign.additional_currencies", live_addl == sorted(c.get("additional_currencies") or []), str(live_addl))
    check("campaign.statement_descriptor", (live.get("statement_descriptor") or "") == (c.get("statement_descriptor") or ""), str(live.get("statement_descriptor")))
    check("campaign.paypal_account_id", (live.get("paypal_account_id") or None) == (c.get("paypal_account_id") or None),
          str(live.get("paypal_account_id")))
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
        p = next((x for x in plan["packages"] if x["key"] == e["key"]), None)
        if p is None:
            # A key the plan no longer carries cannot be read back against anything;
            # the pair is out of step, which is the finding.
            check(f"package {e['key']}", False, "manifest entry not in plan")
            continue
        live_p = pkgs.get(e.get("id"))
        ids[e["key"]] = e.get("id")
        if not live_p:
            check(f"package {e['key']}", False, "missing remotely")
            continue
        live_prices = live_p.get("prices") or []
        price = next((x.get("price") for x in live_prices if x.get("currency") == c["currency"]), None)
        check(f"package {e['key']} price", _num(price) == D(p["price"]), f"{price} vs {p['price']}")
        check(f"package {e['key']} name", _live_package_name_ok(live_p.get("name"), p),
              f"{live_p.get('name')} vs {p['name']}")
        check(f"package {e['key']} variant", live_p.get("product_variant_id") == p["product_variant_ids"][0], str(live_p.get("product_variant_id")))
        if p.get("price_recurring"):
            # Only a subscription package has these, and only the plan can say so:
            # a one-off package reads back interval "" and interval_count null.
            recurring = next((x.get("price_recurring") for x in live_prices if x.get("currency") == c["currency"]), None)
            check(f"package {e['key']} price_recurring", _num(recurring) == D(p["price_recurring"]),
                  f"{recurring} vs {p['price_recurring']}")
            check(f"package {e['key']} interval", (live_p.get("interval") or None) == p.get("interval", "month"),
                  f"{live_p.get('interval')} vs {p.get('interval', 'month')}")
            check(f"package {e['key']} interval_count", live_p.get("interval_count") == p.get("interval_count", 1),
                  f"{live_p.get('interval_count')} vs {p.get('interval_count', 1)}")
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
            o = next((x for x in plan["offers"] if x["key"] == e["key"]), None)
            if o is None:
                check(f"offer {e['key']}", False, "manifest entry not in plan")
                continue
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
            cond = live_o.get("condition") or {}
            ben = live_o.get("benefit") or {}
            got = sorted(p.get("id") for p in cond.get("packages", []))
            check(f"offer {e['key']} scope", got == want and not cond.get("all_packages"), f"{got} vs {want}")
            check(f"offer {e['key']} benefit", _num(ben.get("value")) == D(o["benefit"]["value"]), str(ben.get("value")))
            check(f"offer {e['key']} type", live_o.get("offer_type") == o.get("offer_type", "offer"), str(live_o.get("offer_type")))
            check(f"offer {e['key']} name", live_o.get("name") == o["name"], str(live_o.get("name")))
            # Availability defaults to true on both sides: the plan omits the field
            # for every offer that fires, and so does a store that does not report it.
            check(f"offer {e['key']} available", bool(live_o.get("available", True)) == _available(o),
                  f"{live_o.get('available')} vs {_available(o)}")
            check(f"offer {e['key']} condition.type", cond.get("type") == o["condition"]["type"],
                  f"{cond.get('type')} vs {o['condition']['type']}")
            if o["condition"]["type"] == "count":
                # The threshold comes back as a decimal string ("2.00"), so it is
                # compared as a number. A store whose read-back omits it proves
                # nothing either way, which is not the same as a wrong threshold.
                live_value = _finite(_num(cond.get("value")))
                if live_value is None:
                    row(f"offer {e['key']} condition.value", "UNVERIFIED",
                        "this store's offer read-back carries no threshold; check it in the dashboard")
                else:
                    check(f"offer {e['key']} condition.value", live_value == D(o["condition"]["value"]),
                          f"{cond.get('value')} vs {o['condition']['value']}")
            check(f"offer {e['key']} benefit.type", ben.get("type") == o["benefit"]["type"],
                  f"{ben.get('type')} vs {o['benefit']['type']}")
            # "" and null are the same absence of rounding; only one of them is sent
            check(f"offer {e['key']} benefit.price_rounding",
                  (ben.get("price_rounding") or None) == (o["benefit"].get("price_rounding") or None),
                  f"{ben.get('price_rounding')} vs {o['benefit'].get('price_rounding')}")
            if o.get("offer_type") == "voucher":
                check(f"offer {e['key']} code", live_o.get("code") == o["code"], str(live_o.get("code")))

    # Pricing truth: carts/calculate with the campaign key.
    # Only a plan that declares a hero can be missing one. A campaign whose plan
    # names no hero role (one adopted from the dashboard, say) is not thereby broken.
    hero_declared = any(p.get("role") == "hero" for p in plan["packages"])
    hero_present = any(p.get("role") == "hero" and ids.get(p["key"]) for p in plan["packages"])
    if hero_declared and not hero_present:
        check("hero packages present", False, "no created hero package ids in the manifest")
    cart_cases = _cart_cases_from_plan(plan, ids)
    if not plan.get("landed_prices"):
        # Without landed rows there are no carts, so every free-shipping offer would
        # fail the coverage gate for the lack of cases it could never have had. The
        # offers above are still read back; the pricing is simply unproven, once.
        row("pricing coverage unproven: no landed rows", "INFO",
            "the plan carries no landed_prices, so calculate proves no offer threshold")
    elif hero_present:
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
    try:
        live_offers = client.paginate(f"/api/admin/campaigns/{cid}/offers/")
    except CampaignAdminError as exc:
        # Skip the check only when both hold: the store has no offers endpoint
        # (404/405) and the plan has no offers, so there is nothing to compare.
        # Any other failure to list leaves the live set unknown, which is not a pass.
        endpoint_absent = getattr(exc, "status", None) in (404, 405)
        nothing_planned = not plan.get("offers")
        if not (endpoint_absent and nothing_planned):
            check("campaign offers match the plan", False, f"could not list live offers: {exc}")
    else:
        ours = {e.get("id") for e in man.data["offers"]
                if e.get("status") == "created" and e.get("id") is not None}
        extra = [f"{x.get('id')} {x.get('name')!r}" for x in live_offers if x.get("id") not in ours]
        # an offer deleted after its read-back and probes would otherwise pass
        live_ids = {x.get("id") for x in live_offers}
        gone = sorted(str(i) for i in ours - live_ids)
        problems = ([f"unplanned live offer(s): {', '.join(extra)}"] if extra else []) + \
                   ([f"created offer(s) no longer live: {', '.join(gone)}"] if gone else [])
        check("campaign offers match the plan", not problems,
              "; ".join(problems) if problems else f"{len(live_offers)} live, all created by this run")

    # Only a FAIL fails the report: an INFO or UNVERIFIED row records something
    # that could not be proven here, which is not the same as a wrong value.
    result = "FAIL" if any(x["result"] == "FAIL" for x in checks + cases) else "PASS"
    return {"manifest_run_id": man.data["run_id"], "plan_sha256": plan_sha, "verified_at": utcnow(),
            "admin_checks": checks, "calculate_cases": cases, "result": result}


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
    # Rows that are neither PASS nor FAIL do not change the verdict, so the verdict
    # line says how many of them there were rather than leaving them in the scroll.
    unproven = [x["result"] for x in report["admin_checks"] + report["calculate_cases"]
                if x["result"] not in ("PASS", "FAIL")]
    print(f"VERIFY: {report['result']}"
          + (f"  ({len(unproven)} row(s) not proven: {', '.join(sorted(set(unproven)))})"
             if unproven else ""))


# --------------------------------------------------------------------------- #
# adopt / diff / update
# --------------------------------------------------------------------------- #

CHANGE_SET_NAME = "change-set.json"
CHANGE_SET_SCHEMA = 1
# Printed by every command that reads a run while an update is journalled but not
# finished. The docs copy this string, so it lives in one place.
ACTIVE_UPDATE_MSG = "an update is in progress; finish it with update --resume"
# manifest section -> the path segment its collection lives under
SECTION_ROUTES = {"packages": "packages", "shipping_methods": "shipping-methods", "offers": "offers"}
PKG_KEY_PREFIX = "pkg-"
OFFER_KEY_PREFIX = "offer-"


def snapshot_live(client: Client, cid) -> dict:
    """Everything an update has to reason about, read-only: the campaign, its
    paginated packages, shipping methods and offers, then each offer by id because
    the offers list omits `condition.packages`."""
    campaign = client.get_ok(f"/api/admin/campaigns/{cid}/")
    packages = client.paginate(f"/api/admin/campaigns/{cid}/packages/")
    shipping = client.paginate(f"/api/admin/campaigns/{cid}/shipping-methods/")
    offers = [client.get_ok(f"/api/admin/campaigns/{cid}/offers/{o['id']}/")
              for o in client.paginate(f"/api/admin/campaigns/{cid}/offers/") if o.get("id")]
    return {"campaign": campaign, "packages": packages, "shipping_methods": shipping, "offers": offers}


def _money_or_none(raw):
    """A price in plan form, or None when the store reports none or reports one that
    is not a finite number. None never equals a plan price, so an unreadable price
    shows up as a difference rather than passing as a match."""
    v = _finite(_num(raw))
    return None if v is None else money(v)


def _whole(raw):
    """A count threshold as an int ("2.00" -> 2), or None when it is missing or not a
    whole number. None is a blocker on adopt and a difference in a diff: the engine
    never guesses a threshold."""
    v = _finite(_num(raw))
    if v is None:
        return None
    try:
        q = v.to_integral_value()
    except (InvalidOperation, ValueError):  # pragma: no cover - _finite already filtered these
        return None
    return int(q) if q == v else None


def _codes(value) -> list:
    """The code strings in one of the campaign's code lists, which read back as
    objects (`[{"code": "US"}]`) and are sent as plain strings."""
    out = [x.get("code") if isinstance(x, dict) else x for x in value or []]
    return [x for x in out if x]


def _entries_by_id(man, section: str) -> dict:
    """id -> manifest entry for the entries of one section this run still owns."""
    if man is None:
        return {}
    return {e["id"]: e for e in man.data.get(section) or []
            if e.get("status") == "created" and e.get("id") is not None}


def _bare_package_name(live_name, variant_name, plan_p=None, man_name=None):
    """A live package name in plan space. Package create and update append
    " - {variant}" to the name they are sent, so the plan holds the bare name and the
    live one has to be stripped before the two can be compared. In order: the name
    this run journalled (the plan's own name is then the bare form), the plan's
    `name - variant_title`, and the live `product_variant_name` suffix."""
    plan_name = (plan_p or {}).get("name")
    if plan_name:
        if man_name is not None and live_name == man_name:
            return plan_name
        if live_name == plan_name:
            return plan_name
        vt = (plan_p or {}).get("variant_title")
        if vt is not None and live_name == f"{plan_name} - {vt}":
            return plan_name
    if isinstance(live_name, str) and variant_name:
        suffix = f" - {variant_name}"
        if live_name.endswith(suffix):
            return live_name[: -len(suffix)]
    return live_name


def normalize_snapshot(snap: dict, currency: str, man=None, plan=None) -> dict:
    """A live campaign in plan space: the only input to the baseline hash and to the
    diff. Everything that cannot be desired state is dropped (api_key, timestamps,
    purchase and inventory availability, product name and sku, the `description`
    fields), `""` and `null` collapse where they mean the same absence, prices and
    thresholds become plan-shaped, and code and id lists are sorted so the hash does
    not move with the order the store happens to list things in.

    `image` is a receipt, not desired state: it is the server-built thumbnail, so it
    is hashed into the baseline (a dashboard image change between diff and update is
    drift) and never compared with a plan."""
    pkg_entries = _entries_by_id(man, "packages")
    plan_pk = {p.get("key"): p for p in (plan or {}).get("packages") or []}
    plan_by_variant = {}
    for p in (plan or {}).get("packages") or []:
        vids = p.get("product_variant_ids") or []
        if vids:
            plan_by_variant.setdefault(vids[0], p)

    c = snap.get("campaign") or {}
    out = {"campaign": {
        "id": c.get("id"), "name": c.get("name"), "currency": c.get("currency"),
        "language": c.get("language"), "payment_gateway_group_id": c.get("payment_gateway_group_id"),
        "paypal_account_id": c.get("paypal_account_id") or None,
        "statement_descriptor": c.get("statement_descriptor") or None,
        "additional_currencies": sorted(c.get("additional_currencies") or []),
        "available_payment_methods": sorted(_codes(c.get("available_payment_methods"))),
        "available_express_payment_methods": sorted(_codes(c.get("available_express_payment_methods"))),
        "available_shipping_countries": sorted(_codes(c.get("available_shipping_countries"))),
    }, "packages": [], "shipping_methods": [], "offers": []}

    for x in snap.get("packages") or []:
        vid = x.get("product_variant_id")
        e = pkg_entries.get(x.get("id"))
        plan_p = plan_pk.get(e.get("key")) if e else plan_by_variant.get(vid)
        recurring = next((p.get("price_recurring") for p in x.get("prices") or []
                          if isinstance(p, dict) and p.get("currency") == currency), None)
        out["packages"].append({
            "id": x.get("id"), "product_id": x.get("product_id"), "product_variant_id": vid,
            "name": _bare_package_name(x.get("name"), x.get("product_variant_name"), plan_p,
                                       (e or {}).get("name")),
            "price": _money_or_none(_price_in(x, currency)),
            "price_recurring": _money_or_none(recurring),
            "interval": x.get("interval") or None,
            "interval_count": x.get("interval_count"),
            "image": x.get("image") or None,
        })
    for x in snap.get("shipping_methods") or []:
        out["shipping_methods"].append({
            "id": x.get("id"), "shipping_method": x.get("shipping_method"),
            "price": _money_or_none(_price_in(x, currency)),
        })
    for x in snap.get("offers") or []:
        cond, ben = x.get("condition") or {}, x.get("benefit") or {}
        out["offers"].append({
            "id": x.get("id"), "name": x.get("name"), "offer_type": x.get("offer_type") or "offer",
            "code": x.get("code") or None,
            # The field is optional and defaults to true, as _available reads it.
            "available": x.get("available", True) is not False,
            "condition": {
                "type": cond.get("type"),
                "value": _whole(cond.get("value")) if cond.get("type") == "count" else None,
                "all_packages": bool(cond.get("all_packages")),
                "package_ids": sorted(p.get("id") for p in cond.get("packages") or []
                                      if isinstance(p, dict) and p.get("id") is not None),
            },
            "benefit": {"type": ben.get("type"), "value": _money_or_none(ben.get("value")),
                        "price_rounding": ben.get("price_rounding") or None},
        })
    for section in ("packages", "shipping_methods", "offers"):
        out[section].sort(key=lambda x: (x["id"] is None, x["id"]))
    return out


def baseline_sha256(norm: dict) -> str:
    """The reviewed state of the campaign as one hash. `update` re-reads the campaign
    and recomputes this; a different answer means the store moved after the operator
    read the diff."""
    return hashlib.sha256(json.dumps(norm, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def derive_role(pkg_id, offers: list) -> str:
    """The `role` an adopted package gets, from the offers that scope it. A fixed
    heuristic, printed by `adopt` so the operator can correct it: role is plan-only
    and is never sent, so changing it is never an op."""
    mine = [o for o in offers if pkg_id in (o["condition"]["package_ids"] or [])]
    if any(o["condition"]["type"] == "count" and o["benefit"]["type"] == "package_percentage"
           for o in mine):
        return "hero"
    if mine and all(o["benefit"]["type"] == "package_percentage"
                    and _num(o["benefit"]["value"]) == Decimal(100) for o in mine):
        return "gift"
    if mine and all(o["offer_type"] == "voucher" for o in mine):
        return "upsell"
    return "bump"


def plan_from_live(snap: dict, slug: str, *, convert_scope=()) -> tuple:
    """(plan, blockers) for a campaign that already exists: a plan whose desired
    state is exactly what is live. Blockers are the things the engine refuses to
    guess at; with any of them the campaign is not adopted."""
    currency = (snap.get("campaign") or {}).get("currency")
    norm = normalize_snapshot(snap, currency)
    convert = {int(x) for x in convert_scope or []}
    variant_names = {x.get("id"): x.get("product_variant_name") for x in snap.get("packages") or []}
    raw_value = {x.get("id"): (x.get("condition") or {}).get("value") for x in snap.get("offers") or []}
    blockers, waivers = [], []

    c = norm["campaign"]
    packages, key_by_id, seen_variant = [], {}, {}
    for p in norm["packages"]:
        vid = p["product_variant_id"]
        key = f"{PKG_KEY_PREFIX}{vid}"
        if vid in seen_variant:
            blockers.append(
                f"packages {seen_variant[vid]} and {p['id']} are both on product variant {vid}; the "
                "Campaigns API allows one package per variant, so this campaign cannot be represented "
                "as a plan. Remove one of them in the dashboard, then adopt again.")
        else:
            seen_variant[vid] = p["id"]
        key_by_id[p["id"]] = key
        entry = {"key": key, "role": derive_role(p["id"], norm["offers"]), "name": p["name"],
                 "variant_title": variant_names.get(p["id"]), "product_id": p["product_id"],
                 "product_variant_ids": [vid], "price": p["price"]}
        if p["price_recurring"]:
            entry["price_recurring"] = p["price_recurring"]
            entry["interval"] = p["interval"] or "month"
            entry["interval_count"] = 1 if p["interval_count"] is None else p["interval_count"]
        packages.append(entry)

    shipping, seen_code = [], {}
    for s in norm["shipping_methods"]:
        code = s["shipping_method"]
        if code in seen_code:
            blockers.append(
                f"campaign shipping methods {seen_code[code]} and {s['id']} are both on store code "
                f"{code!r}; a plan carries one campaign method per store code. Remove one of them in "
                "the dashboard, then adopt again.")
        else:
            seen_code[code] = s["id"]
        shipping.append({"shipping_method": code, "price": s["price"]})

    all_keys = [p["key"] for p in packages]
    offers, seen_name, converted = [], {}, set()
    for o in norm["offers"]:
        key = f"{OFFER_KEY_PREFIX}{o['id']}"
        if o["name"] in seen_name:
            blockers.append(
                f"offers {seen_name[o['name']]} and {o['id']} share the name {o['name']!r}; offer names "
                "are unique per campaign, so the engine cannot tell them apart. Rename one in the "
                "dashboard, then adopt again.")
        else:
            seen_name[o["name"]] = o["id"]
        cond = o["condition"]
        if cond["all_packages"]:
            scope = list(all_keys)
            if o["id"] in convert:
                converted.add(key)
                waivers.append(
                    f"offer {key} ({o['name']!r}) was scoped to all_packages on the store; "
                    f"--convert-scope {o['id']} pinned it to the campaign's current {len(all_keys)} "
                    f"package(s) {scope}. The first diff sends that conversion as a PATCH.")
            else:
                blockers.append(
                    f"offer {o['id']} ({o['name']!r}) is scoped to all_packages, which a plan cannot "
                    f"represent: it would also cover packages created later. Re-run with "
                    f"--convert-scope {o['id']} to pin it to the campaign's current {len(all_keys)} "
                    "package(s), or narrow it in the dashboard.")
        else:
            scope = [key_by_id[i] for i in cond["package_ids"] if i in key_by_id]
        if cond["type"] == "count" and cond["value"] is None:
            blockers.append(
                f"offer {o['id']} ({o['name']!r}) is a count offer whose threshold reads back as "
                f"{raw_value.get(o['id'])!r}; adopt needs a whole number and never guesses one. Read "
                "the threshold in the dashboard and correct the offer there, then adopt again.")
        offers.append({
            "key": key, "name": o["name"], "offer_type": o["offer_type"], "code": o["code"],
            "available": o["available"],
            "condition": {"type": cond["type"], "value": cond["value"], "package_keys": scope},
            "benefit": {"type": o["benefit"]["type"], "value": o["benefit"]["value"],
                        "price_rounding": o["benefit"]["price_rounding"]},
        })

    plan = {
        "store_slug": slug, "store_origin": slug_to_origin(slug),
        "origin": "adopted", "adopted_from_campaign_id": c["id"], "generated_at": utcnow(),
        "campaign": {
            "name": c["name"], "currency": c["currency"], "language": c["language"],
            "payment_gateway_group_id": c["payment_gateway_group_id"],
            "additional_currencies": list(c["additional_currencies"]),
            "available_payment_methods": list(c["available_payment_methods"]),
            "available_express_payment_methods": list(c["available_express_payment_methods"]),
            "available_shipping_countries": list(c["available_shipping_countries"]),
            "statement_descriptor": c["statement_descriptor"],
            "paypal_account_id": c["paypal_account_id"],
        },
        "packages": packages, "shipping_methods": shipping, "offers": offers,
        # No landed prices are synthesised: nothing read from the store says what the
        # cart engine charges, and verify says so once rather than guessing.
        "voucher_codes": [], "landed_prices": [], "rationale": [], "handoff": [],
        "blockers": [], "waivers": waivers, "converted_scopes": sorted(converted),
    }
    # Anything the engine cannot represent is a blocker too, so adopt never writes a
    # manifest for a campaign that later commands would refuse.
    for e in validate_plan(plan):
        if e not in blockers:
            blockers.append(e)
    plan["blockers"] = list(blockers)
    return plan, blockers


def adopt(client: Client, slug: str, cid, out: Path, *, convert_scope=()) -> tuple:
    """Take ownership of a campaign that already exists. Returns (plan path, manifest
    path), the manifest path None when the campaign has blockers."""
    out = Path(out)
    manifest_path = out / MANIFEST_NAME
    if manifest_path.exists():
        raise CampaignAdminError(
            f"{manifest_path} already exists; that file is the record of a campaign this skill already "
            "owns, and its plan is the only thing that can update or tear that campaign down. Pass "
            "--out <new directory> to adopt another campaign.")
    snap = snapshot_live(client, cid)
    live_c = snap["campaign"] or {}
    plan, blockers = plan_from_live(snap, slug, convert_scope=convert_scope)
    converted = set(plan.pop("converted_scopes"))
    plan_path = out / PLAN_NAME
    atomic_write_json(plan_path, plan)

    print(f"Campaign {live_c.get('id')} {live_c.get('name')!r} created {live_c.get('created_at')} "
          f"on {slug_to_origin(slug)}")
    print(f"  {len(plan['packages'])} package(s), {len(plan['shipping_methods'])} shipping method(s), "
          f"{len(plan['offers'])} offer(s)")
    print("Roles (a heuristic from the offers; correct them in the plan before the first diff):")
    for p in plan["packages"]:
        print(f"  {p['key']:<14} variant {p['product_variant_ids'][0]} "
              f"({p.get('variant_title')})  {p['role']}")
    for w in plan["waivers"]:
        print(f"  waiver: {w}")
    print(f"wrote {plan_path}")

    if blockers:
        print("BLOCKERS (this campaign is not adopted; no manifest was written):")
        for b in blockers:
            print(f"  - {b}")
        pending = [x.get("id") for x in snap["offers"]
                   if (x.get("condition") or {}).get("all_packages")
                   and x.get("id") not in {int(i) for i in convert_scope or []}]
        rerun = (f"{PROG} adopt --store {slug} --campaign {cid}"
                 + "".join(f" --convert-scope {i}" for i in pending))
        print(f"Fix them in the dashboard, then re-run: {rerun}")
        return plan_path, None

    live_pkg = {x.get("product_variant_id"): x for x in snap["packages"]}
    live_ship = {x.get("shipping_method"): x for x in snap["shipping_methods"]}
    now = utcnow()
    man = Manifest(manifest_path, {
        "store_slug": slug, "store_origin": slug_to_origin(slug),
        "origin": "adopted", "adopted_at": now, "adopted_from_campaign_id": live_c.get("id"),
        "plan_sha256": sha256_file(plan_path), "run_id": str(uuid.uuid4()), "started_at": now,
        "campaign": {"status": "created", "key": "campaign", "name": live_c.get("name"),
                     "id": live_c.get("id"), "api_key": live_c.get("api_key"),
                     "created_at": live_c.get("created_at"), "intent": None},
        "packages": [
            {"key": p["key"], "status": "created", "id": live_pkg[p["product_variant_ids"][0]].get("id"),
             "name": live_pkg[p["product_variant_ids"][0]].get("name"),
             "product_variant_id": p["product_variant_ids"][0], "intent": None}
            for p in plan["packages"]],
        "shipping_methods": [
            {"key": ship_key(s), "status": "created", "id": live_ship[s["shipping_method"]].get("id"),
             "shipping_method": s["shipping_method"], "price": s["price"], "intent": None}
            for s in plan["shipping_methods"]],
        "offers": [
            dict({"key": o["key"], "status": "created",
                  "id": int(o["key"][len(OFFER_KEY_PREFIX):]), "name": o["name"],
                  "code": o["code"], "intent": None},
                 **({"scope_conversion_pending": True} if o["key"] in converted else {}))
            for o in plan["offers"]],
        "completed_at": now,
    })
    man.save()
    print(f"wrote {manifest_path} (mode 600; it holds the campaign api_key, which is never printed)")
    print(f"Next: copy {plan_path.name} to campaign-plan.next.json, edit that copy, then "
          f"{PROG} diff --plan <that copy> --manifest {manifest_path}")
    return plan_path, manifest_path


# --------------------------------------------------------------------------- #
# diff: three-way merge and change set
# --------------------------------------------------------------------------- #

def check_identity(man, plan: dict, snap: dict, ops=None) -> list:
    """What cannot legitimately change, compared with the store: campaign id and
    created_at, package id and product_variant_id, shipping id and store code, offer
    id. Mutable fields are not ownership: a dashboard rename with an untouched
    candidate has to flow through the three-way merge as a preserved value, so
    mutable drift is the diff's and the baseline hash's job, not this one's. Never
    writes. A missing object is a problem unless a DELETE op in `ops` names it."""
    problems = []
    deleted = {(o.get("section"), o.get("key")) for o in (ops or []) if o.get("method") == "DELETE"}
    camp, live_c = man.data.get("campaign") or {}, snap.get("campaign") or {}
    if live_c.get("id") != camp.get("id"):
        problems.append(f"campaign {live_c.get('id')} is not the campaign this run owns "
                        f"({camp.get('id')})")
    elif camp.get("created_at") and not same_instant(live_c.get("created_at"), camp.get("created_at")):
        problems.append(f"campaign {camp.get('id')} was created at {live_c.get('created_at')}, not at "
                        f"{camp.get('created_at')} as this run recorded; the id names another campaign")
    live = {s: {x.get("id"): x for x in snap.get(s) or []}
            for s in ("packages", "shipping_methods", "offers")}
    # 0.7.5 manifests record a shipping entry's key and id but not its store code, so
    # for an entry without one the code comes from the hash-verified base plan.
    plan_sm = {ship_key(s): s for s in plan.get("shipping_methods") or []}
    for section in ("packages", "shipping_methods", "offers"):
        for e in man.data.get(section) or []:
            if e.get("status") != "created" or e.get("id") is None:
                continue
            x = live[section].get(e["id"])
            if x is None:
                if (section, e.get("key")) not in deleted:
                    problems.append(f"{section} {e.get('key')} (id {e['id']}) is no longer on the campaign")
                continue
            if section == "packages" and e.get("product_variant_id") is not None \
                    and x.get("product_variant_id") != e.get("product_variant_id"):
                problems.append(
                    f"package {e.get('key')} (id {e['id']}) is on product variant "
                    f"{x.get('product_variant_id')}, not {e.get('product_variant_id')}; that id is "
                    "another package now")
            if section == "shipping_methods":
                code = e.get("shipping_method") or (plan_sm.get(e.get("key")) or {}).get("shipping_method")
                if code is not None and x.get("shipping_method") != code:
                    problems.append(f"shipping {e.get('key')} (id {e['id']}) is on store code "
                                    f"{x.get('shipping_method')!r}, not {code!r}")
    return problems


def _show(v) -> str:
    """One value in a refusal or a preserved line, readable and unambiguous."""
    return json.dumps(v, default=str)


def _apply_package_n(p: dict, n: dict) -> dict:
    """A merged plan package: the candidate entry with the merged live-backed values.
    The recurring trio is set or absent as one thing, the way a plan carries it."""
    out = dict(p)
    out["name"], out["price"] = n["name"], n["price"]
    if n["price_recurring"]:
        out["price_recurring"] = n["price_recurring"]
        out["interval"] = n["interval"] or "month"
        out["interval_count"] = 1 if n["interval_count"] is None else n["interval_count"]
    else:
        for f in ("price_recurring", "interval", "interval_count"):
            out.pop(f, None)
    return out


def _apply_offer_n(o: dict, n: dict) -> dict:
    out = dict(o)
    out["name"], out["offer_type"] = n["name"], n["offer_type"]
    if "code" in o or n["code"] is not None:
        out["code"] = n["code"]
    if "available" in o or n["available"] is not True:
        out["available"] = n["available"]
    out["condition"] = dict(o.get("condition") or {})
    out["condition"].update(type=n["condition_type"], value=n["condition_value"],
                            package_keys=list(n["package_keys"]))
    out["benefit"] = dict(o.get("benefit") or {})
    out["benefit"].update(type=n["benefit_type"], value=n["benefit_value"],
                          price_rounding=n["price_rounding"])
    return out


def _apply_campaign_n(c: dict, n: dict) -> dict:
    out = dict(c)
    for f in CAMPAIGN_SCALARS:
        out[f] = n[f]
    for f in CAMPAIGN_CODE_LISTS:
        out[f] = list(n[f])
    out["additional_currencies"] = list(n["additional_currencies"])
    for f in CAMPAIGN_NULLABLE:
        if f in c or n[f] is not None:
            out[f] = n[f]
    return out


def _live_offer_n(lo: dict, key_of_package) -> dict:
    """A normalised live offer in plan space: its scope as package keys."""
    cond, ben = lo["condition"], lo["benefit"]
    return {"name": lo["name"], "offer_type": lo["offer_type"], "code": lo["code"],
            "available": lo["available"], "condition_type": cond["type"],
            "condition_value": cond["value"],
            "package_keys": sorted(key_of_package[i] for i in cond["package_ids"]),
            "benefit_type": ben["type"], "benefit_value": ben["value"],
            "price_rounding": ben["price_rounding"]}


def diff_change_set(base: dict, cand: dict, man, snap: dict, *, delete_changed=()) -> dict:
    """The reviewed change set for one update: base (the canonical plan), candidate
    (the edited copy) and live, merged in plan space.

    Per field: the candidate left it alone and the store changed it, so the store
    wins and the value is preserved; the candidate changed it and the store did not,
    so it is a PATCH; the store is already at the candidate's value, so nothing is
    sent; all three differ, so it is a conflict and the whole diff refuses. Per key:
    only in the candidate is a POST, only in the base is a DELETE, and a live object
    in neither plan is unmanaged and refuses."""
    errs = validate_plan(cand)
    if errs:
        raise CampaignAdminError("candidate plan is invalid:\n  - " + "\n  - ".join(errs))
    # The base plan is this engine's own output and is hash-bound to the manifest, so a
    # base that does not validate means the file was edited in place past the hash
    # check or written by something else. Everything below reads fields out of it.
    errs = validate_plan(base, for_create=False)
    if errs:
        raise CampaignAdminError(
            "the canonical plan in the run directory is invalid, so there is nothing to diff "
            "against:\n  - " + "\n  - ".join(errs)
            + "\n  Restore it (the archives in this directory are byte copies) or adopt the campaign "
              "into a fresh run directory.")
    if cand.get("blockers"):
        raise CampaignAdminError("candidate plan has blockers; clear them before diffing:\n  - "
                                 + "\n  - ".join(cand["blockers"]))
    check_origin_binding(base, "base plan")
    check_origin_binding(cand, "candidate plan")
    check_origin_binding(man.data, "manifest")
    if cand.get("store_slug") != base.get("store_slug") or man.data.get("store_slug") != base.get("store_slug"):
        raise CampaignAdminError("the base plan, the candidate plan and the manifest must all name one "
                                 f"store (the base plan names {base.get('store_slug')!r})")
    if man.data.get("active_update"):
        raise CampaignAdminError(ACTIVE_UPDATE_MSG)
    torn = torn_down_entries(man)
    if torn:
        raise CampaignAdminError(f"this run was torn down, fully or in part ({', '.join(torn)}); there "
                                 "is nothing left to update")
    camp_entry = man.data.get("campaign") or {}
    if camp_entry.get("status") != "created":
        raise CampaignAdminError(f"the manifest's campaign is {camp_entry.get('status')!r}, not created; "
                                 "there is nothing to update")
    unfinished = [f"{s} {e.get('key')} is {e.get('status')!r}"
                  for s in ("packages", "shipping_methods", "offers")
                  for e in man.data.get(s) or [] if e.get("status") != "created"]
    if unfinished:
        raise CampaignAdminError(
            "this run never finished:\n  - " + "\n  - ".join(unfinished)
            + "\n  Finish it with apply --resume, or tear it down, before updating anything.")

    currency = base["campaign"]["currency"]
    if cand["campaign"].get("currency") != currency:
        raise CampaignAdminError(
            f"campaign currency cannot change once the campaign exists (base {currency!r}, candidate "
            f"{cand['campaign'].get('currency')!r}); a different currency needs a new campaign")
    live_currency = (snap.get("campaign") or {}).get("currency")
    if live_currency != currency:
        raise CampaignAdminError(
            f"the campaign on the store is in {live_currency!r} and the base plan in {currency!r}; "
            "currency is immutable, so this manifest does not describe that campaign")

    problems = check_identity(man, base, snap)
    if problems:
        raise CampaignAdminError("ownership check failed; refusing to diff:\n  - " + "\n  - ".join(problems))

    cid = camp_entry["id"]
    live = normalize_snapshot(snap, currency, man, base)

    key_by_id, id_by_key, man_entry = {}, {}, {}
    for section in ("packages", "shipping_methods", "offers"):
        for e in man.data.get(section) or []:
            man_entry[(section, e.get("key"))] = e
            if e.get("status") == "created" and e.get("id") is not None:
                key_by_id[(section, e["id"])] = e["key"]
                id_by_key[(section, e["key"])] = e["id"]

    unmanaged = []
    for section, label in (("packages", "package"), ("shipping_methods", "shipping method"),
                           ("offers", "offer")):
        for x in live[section]:
            if (section, x["id"]) not in key_by_id:
                what = x.get("name") or x.get("shipping_method")
                unmanaged.append(f"{label} {x['id']} ({what!r})")
    if unmanaged:
        raise CampaignAdminError(
            "the campaign carries object(s) this run does not own: " + ", ".join(unmanaged)
            + ". An update only touches what the manifest records. Remove them in the dashboard, or "
            "adopt the campaign into a fresh run directory and edit that plan instead.")

    base_pk = {p["key"]: p for p in base.get("packages") or []}
    base_sm = {ship_key(s): s for s in base.get("shipping_methods") or []}
    base_of = {o["key"]: o for o in base.get("offers") or []}
    cand_pk = {p["key"]: p for p in cand.get("packages") or []}
    cand_sm = {ship_key(s): s for s in cand.get("shipping_methods") or []}
    cand_of = {o["key"]: o for o in cand.get("offers") or []}
    in_base = {"packages": base_pk, "shipping_methods": base_sm, "offers": base_of}
    stray = [f"{section} {key}" for (section, key) in sorted(id_by_key)
             if key not in in_base[section]]
    if stray:
        raise CampaignAdminError("the manifest records object(s) the base plan does not: "
                                 + ", ".join(stray) + "; this run directory is out of step")
    # The mirror of the check above: every key in the base plan has to be an object this
    # run owns, or there is nothing on the store to diff that key against.
    unowned = sorted(f"{section} {key}" for section, keys in in_base.items() for key in keys
                     if (section, key) not in id_by_key)
    if unowned:
        raise CampaignAdminError(
            "the base plan names object(s) the manifest does not own: " + ", ".join(unowned)
            + "; the run that created them never finished. Finish it with apply --resume, or adopt "
            "the campaign into a fresh run directory.")

    live_by_key = {(section, key_by_id[(section, x["id"])]): x
                   for section in ("packages", "shipping_methods", "offers") for x in live[section]}
    key_of_package = {pid: key for (section, pid), key in key_by_id.items() if section == "packages"}
    authorised = set(delete_changed or [])
    preserved, warnings, conflicts = [], [], []

    def merge(label, b, c, l, *, force_op=False):
        """One field, three ways. Returns (the merged plan's value, whether it is an op)."""
        if force_op:
            return c, True
        if c == b:
            if l == b:
                return b, False
            preserved.append(f"{label}: the store's {_show(l)} is kept (the candidate did not change it)")
            return l, False
        if l == b:
            return c, True
        if l == c:
            return c, False
        conflicts.append(f"{label}: base {_show(b)}, candidate {_show(c)}, store {_show(l)}")
        return c, False

    def deletable(section, key, fields, b_n, l_n):
        """A DELETE is only safe when the live object still matches the base plan: the
        baseline hash protects edits made after the diff, not before it."""
        changed = [f"{f}: base {_show(b_n[f])}, store {_show(l_n[f])}"
                   for f in fields if b_n[f] != l_n[f]]
        if not changed:
            return
        token = f"{section}:{key}"
        if token in authorised:
            warnings.append(f"{section} {key} is deleted although the store changed it since the base "
                            f"plan ({'; '.join(changed)}); authorised with --delete-changed {token}")
            return
        raise CampaignAdminError(
            f"{section} {key} cannot be deleted: the store changed it since the base plan "
            f"({'; '.join(changed)}). Review those values, then re-run with --delete-changed {token} "
            "to delete it anyway.")

    # ---- campaign ---------------------------------------------------------- #
    base_c, cand_c = plan_campaign_n(base["campaign"]), plan_campaign_n(cand["campaign"])
    live_c = {k: v for k, v in live["campaign"].items() if k != "id"}
    merged_c, camp_changed = {}, {}
    for f in CAMPAIGN_SCALARS + CAMPAIGN_NULLABLE + CAMPAIGN_CODE_LISTS + ("additional_currencies",):
        v, needs = merge(f"campaign.{f}", base_c.get(f), cand_c.get(f), live_c.get(f))
        merged_c[f] = v
        if needs:
            camp_changed[f] = (live_c.get(f), v)
    merged_c["currency"] = currency

    # ---- packages ---------------------------------------------------------- #
    merged_packages, pkg_patches, pkg_posts, pkg_deletes, image_puts = [], [], [], [], {}
    new_pkg_keys = {k for k in cand_pk if k not in base_pk}
    for key, p in cand_pk.items():
        img = p.get("image") if isinstance(p.get("image"), dict) else None
        src = (img or {}).get("src")
        was_src = (man_entry.get(("packages", key)) or {}).get("image_src")
        if key not in base_pk:
            merged_packages.append(dict(p))
            pkg_posts.append(key)
            if src:
                image_puts[key] = src
            continue
        bp, lp = base_pk[key], live_by_key.get(("packages", key))
        if (p.get("product_id") != bp.get("product_id")
                or list(p.get("product_variant_ids") or []) != list(bp.get("product_variant_ids") or [])):
            raise CampaignAdminError(
                f"package {key}: product_id and product_variant_ids cannot change on a package that "
                f"exists (base {bp.get('product_id')}/{bp.get('product_variant_ids')}, candidate "
                f"{p.get('product_id')}/{p.get('product_variant_ids')}); give the new variant its own "
                "package key and delete the old one")
        bn, cn = plan_package_n(bp), plan_package_n(p)
        ln = {f: lp.get(f) for f in PACKAGE_DIFF_FIELDS}
        merged_n, changed = {}, {}
        for f in PACKAGE_DIFF_FIELDS:
            v, needs = merge(f"package {key}.{f}", bn[f], cn[f], ln[f])
            merged_n[f] = v
            if needs:
                changed[f] = (ln[f], v)
        merged_packages.append(_apply_package_n(p, merged_n))
        if changed:
            pkg_patches.append((key, changed, merged_n))
        # Images are diffed by intent: the candidate's src against the src this run
        # recorded, never against the server-built thumbnail the store reads back.
        if src and src != was_src:
            image_puts[key] = src
        elif not src and was_src:
            warnings.append(f"package {key}: the candidate dropped image.src {was_src}; the image on the "
                            "store is left as it is (there is no delete-image route)")
    for key, bp in base_pk.items():
        if key in cand_pk:
            continue
        lp = live_by_key[("packages", key)]
        deletable("packages", key, PACKAGE_DIFF_FIELDS, plan_package_n(bp),
                  {f: lp.get(f) for f in PACKAGE_DIFF_FIELDS})
        pkg_deletes.append((key, bp, lp))

    # ---- shipping ---------------------------------------------------------- #
    merged_shipping, ship_patches, ship_posts, ship_deletes = [], [], [], []
    for key, s in cand_sm.items():
        if key not in base_sm:
            merged_shipping.append(dict(s))
            ship_posts.append(key)
            continue
        bs, ls = base_sm[key], live_by_key.get(("shipping_methods", key))
        if s.get("shipping_method") != bs.get("shipping_method"):
            raise CampaignAdminError(
                f"shipping {key}: the store code cannot change on a campaign shipping method that "
                f"exists (base {bs.get('shipping_method')!r}, candidate {s.get('shipping_method')!r}); "
                "add a method on the new code and delete this one")
        v, needs = merge(f"shipping {key}.price", money(D(bs["price"])), money(D(s["price"])),
                         ls.get("price"))
        merged = dict(s)
        merged["price"] = v
        merged_shipping.append(merged)
        if needs:
            ship_patches.append((key, {"price": (ls.get("price"), v)}, merged))
    for key, bs in base_sm.items():
        if key in cand_sm:
            continue
        ls = live_by_key[("shipping_methods", key)]
        deletable("shipping_methods", key, ("shipping_method", "price"),
                  {"shipping_method": bs.get("shipping_method"), "price": money(D(bs["price"]))},
                  {"shipping_method": ls.get("shipping_method"), "price": ls.get("price")})
        ship_deletes.append((key, bs, ls))

    # ---- offers ------------------------------------------------------------ #
    merged_offers, offer_patches, offer_posts, offer_deletes = [], [], [], []
    for key, o in cand_of.items():
        if key not in base_of:
            merged_offers.append(dict(o))
            offer_posts.append(key)
            continue
        bo, lo = base_of[key], live_by_key.get(("offers", key))
        all_pk = lo["condition"]["all_packages"]
        if all_pk and not (man_entry.get(("offers", key)) or {}).get("scope_conversion_pending"):
            raise CampaignAdminError(
                f"offer {key} (id {lo['id']}) is scoped to all_packages on the store; a plan cannot "
                "represent that, so the engine will not update it. Adopt the campaign again with "
                f"--convert-scope {lo['id']} to approve the conversion, or narrow the offer in the "
                "dashboard")
        bn, cn, ln = plan_offer_n(bo), plan_offer_n(o), _live_offer_n(lo, key_of_package)
        merged_n, changed = {}, {}
        for f in OFFER_DIFF_FIELDS:
            # all_packages is the one live value that is never preserved: the plan
            # cannot hold it, so an approved conversion is always a PATCH.
            v, needs = merge(f"offer {key}.{f}", bn[f], cn[f], ln[f],
                             force_op=all_pk and f == "package_keys")
            merged_n[f] = v
            if needs:
                changed[f] = (ln[f], v)
        merged_offers.append(_apply_offer_n(o, merged_n))
        if changed:
            offer_patches.append((key, changed, merged_offers[-1]))
    for key, bo in base_of.items():
        if key in cand_of:
            continue
        lo = live_by_key[("offers", key)]
        deletable("offers", key, OFFER_DIFF_FIELDS, plan_offer_n(bo),
                  _live_offer_n(lo, key_of_package))
        offer_deletes.append((key, bo, lo))

    # A package an offer still scopes cannot be deleted: the API answers 400 rather
    # than narrowing the offer, so the refusal belongs here, before any write.
    surviving = [lo for (section, k), lo in live_by_key.items() if section == "offers" and k in cand_of]
    for key, _bp, lp in pkg_deletes:
        refs = [f"{lo['id']} ({lo['name']!r})" for lo in surviving
                if lp["id"] in lo["condition"]["package_ids"]]
        if refs:
            raise CampaignAdminError(
                f"package {key} (id {lp['id']}) cannot be deleted while live offer(s) "
                f"{', '.join(sorted(refs))} scope it; the API refuses that delete. Narrow or delete "
                "those offers in their own update first, then delete the package")

    if conflicts:
        raise CampaignAdminError(
            "conflicting edits: the store changed what you changed:\n  - " + "\n  - ".join(conflicts)
            + "\n  Decide which value wins: set the candidate to the store's value to keep it, or put "
              "the store back to the base value, then re-run diff.")

    # ---- ops, in the order update sends them ------------------------------- #
    ops = []

    def add(method, section, key, path, body, before, after, *, oid=None, depends_on=None,
            body_template=None, package_keys=None):
        op = {"n": len(ops) + 1, "method": method, "section": section, "key": key}
        if oid is not None:
            op["id"] = oid
        op["path"] = path
        op["body"] = body
        if body_template is not None:
            op["body_template"] = body_template
        if package_keys is not None:
            op["package_keys"] = list(package_keys)
        if depends_on is not None:
            op["depends_on"] = depends_on
        op["before"], op["after"] = before, after
        ops.append(op)
        return op

    def item_path(section, oid):
        return f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/{oid}/"

    def collection_path(section):
        return f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/"

    def offer_op_bodies(merged_o, changed):
        """(body, body_template, package_keys) for one offer op. When the scope names a
        package this change set creates, the final body cannot exist yet: `body` is
        None, `body_template` is the same body with `<pkg-key>` placeholders for the
        operator to read, and `package_keys` is the ordered scope. The executor (M4)
        builds the real body with offer_body(o, package_ids) once those POSTs have
        answered."""
        keys = merged_o["condition"]["package_keys"]
        deferred = any(k in new_pkg_keys for k in keys)
        ids = ({k: f"<{k}>" for k in keys} if deferred
               else {k: id_by_key[("packages", k)] for k in keys})
        full = offer_body(merged_o, ids)
        if changed is None:
            built = full
        else:
            built = {f: changed[f][1] for f in ("name", "offer_type", "code", "available")
                     if f in changed}
            if {"condition_type", "condition_value", "package_keys"} & set(changed):
                built["condition"] = full["condition"]
            if {"benefit_type", "benefit_value", "price_rounding"} & set(changed):
                built["benefit"] = full["benefit"]
        return (None, built, keys) if deferred else (built, None, None)

    if camp_changed:
        add("PATCH", "campaign", "campaign", f"/api/admin/campaigns/{cid}/",
            campaign_patch_body(live_c, merged_c),
            {f: v[0] for f, v in camp_changed.items()}, {f: v[1] for f, v in camp_changed.items()},
            oid=cid)

    for key, bo, lo in offer_deletes:
        add("DELETE", "offers", key, item_path("offers", lo["id"]), None,
            _live_offer_n(lo, key_of_package), {}, oid=lo["id"])

    for key, _bp, lp in pkg_deletes:
        add("DELETE", "packages", key, item_path("packages", lp["id"]), None,
            {f: lp.get(f) for f in PACKAGE_DIFF_FIELDS}, {}, oid=lp["id"])
    patch_by_key = {k: (changed, merged_n) for k, changed, merged_n in pkg_patches}
    for key in cand_pk:
        if key not in base_pk:
            continue  # a new package is a POST, below
        pid = id_by_key[("packages", key)]
        if key in patch_by_key:
            changed, merged_n = patch_by_key[key]
            add("PATCH", "packages", key, item_path("packages", pid),
                package_patch_body(live_by_key[("packages", key)],
                                   _apply_package_n(cand_pk[key], merged_n), currency),
                {f: v[0] for f, v in changed.items()}, {f: v[1] for f, v in changed.items()}, oid=pid)
        if key in image_puts:
            lp = live_by_key[("packages", key)]
            add("PUT", "packages", key, f"{item_path('packages', pid)}image/",
                image_body(cand_pk[key]),
                {"image": lp.get("image"),
                 "image_src": (man_entry.get(("packages", key)) or {}).get("image_src")},
                {"image_src": image_puts[key]}, oid=pid)
    for key in pkg_posts:
        p = cand_pk[key]
        post = add("POST", "packages", key, collection_path("packages"), package_body(p), {},
                   dict(plan_package_n(p), product_variant_id=(p.get("product_variant_ids") or [None])[0]))
        if key in image_puts:
            # No id yet: the route is rebuilt at run time from the id the POST returns.
            add("PUT", "packages", key, f"/api/admin/campaigns/{cid}/packages/<{key}>/image/",
                image_body(p), {"image": None, "image_src": None}, {"image_src": image_puts[key]},
                depends_on=post["n"])

    for key, bs, ls in ship_deletes:
        add("DELETE", "shipping_methods", key, item_path("shipping_methods", ls["id"]), None,
            {"shipping_method": ls.get("shipping_method"), "price": ls.get("price")}, {}, oid=ls["id"])
    for key, changed, merged in ship_patches:
        sid = id_by_key[("shipping_methods", key)]
        add("PATCH", "shipping_methods", key, item_path("shipping_methods", sid),
            shipping_patch_body(live_by_key[("shipping_methods", key)], merged, currency),
            {f: v[0] for f, v in changed.items()}, {f: v[1] for f, v in changed.items()}, oid=sid)
    for key in ship_posts:
        s = cand_sm[key]
        add("POST", "shipping_methods", key, collection_path("shipping_methods"),
            {"shipping_method": s["shipping_method"], "price": s["price"]}, {},
            {"shipping_method": s["shipping_method"], "price": money(D(s["price"]))})

    for key, changed, merged_o in offer_patches:
        oid = id_by_key[("offers", key)]
        body, template, keys = offer_op_bodies(merged_o, changed)
        add("PATCH", "offers", key, item_path("offers", oid), body,
            {f: v[0] for f, v in changed.items()}, {f: v[1] for f, v in changed.items()},
            oid=oid, body_template=template, package_keys=keys)
    for key in offer_posts:
        o = cand_of[key]
        body, template, keys = offer_op_bodies(o, None)
        add("POST", "offers", key, collection_path("offers"), body, {}, plan_offer_n(o),
            body_template=template, package_keys=keys)

    merged = dict(cand)
    merged["campaign"] = _apply_campaign_n(cand["campaign"], merged_c)
    merged["packages"] = merged_packages
    merged["shipping_methods"] = merged_shipping
    merged["offers"] = merged_offers
    errs = validate_plan(merged)
    if errs:
        raise CampaignAdminError(
            "the merged plan (your edits plus the values preserved from the store) is invalid:\n  - "
            + "\n  - ".join(errs)
            + "\n  A value the store changed collides with one of your edits. Reconcile them in the "
              "candidate and re-run diff.")

    # Offer names and voucher codes are unique per campaign, so a valid final state is
    # not enough: every op has to be legal at the moment it is sent.
    held_names = {x["id"]: x["name"] for x in live["offers"]}
    held_codes = {x["id"]: x["code"] for x in live["offers"] if x["code"]}
    unborn = 0
    for op in ops:
        if op["section"] != "offers":
            continue
        if op["method"] == "DELETE":
            held_names.pop(op["id"], None)
            held_codes.pop(op["id"], None)
            continue
        oid = op.get("id")
        if oid is None:
            unborn -= 1
            oid = unborn  # an object that does not exist yet still needs a slot
        sent = op.get("body") or op.get("body_template") or {}
        for field, held, what in (("name", held_names, "name"),
                                  ("code", held_codes, "voucher code")):
            want = sent.get(field)
            if not want:
                continue
            clash = sorted(i for i, v in held.items() if v == want and i != oid)
            if clash:
                raise CampaignAdminError(
                    f"offer {op['key']}: step {op['n']} would set the {what} {want!r}, which live offer "
                    f"{clash[0]} still holds at that point; offer names and voucher codes are unique per "
                    "campaign. Do it as 2 updates through a temporary name, or reorder the edits so the "
                    "offer holding it is changed first.")
            held[oid] = want

    return {
        "schema": CHANGE_SET_SCHEMA, "run_id": man.data.get("run_id"),
        "store_slug": base["store_slug"], "campaign_id": cid, "created_at": utcnow(),
        "base_plan_sha256": man.data.get("plan_sha256"),
        "merged_plan_sha256": "", "merged_plan_file": "",
        "baseline_sha256": baseline_sha256(live), "baseline": live,
        "has_deletes": any(o["method"] == "DELETE" for o in ops),
        "warnings": warnings, "preserved": preserved, "ops": ops, "merged_plan": merged,
    }


def write_change_set(out: Path, cs: dict) -> tuple:
    """`diff` is the only writer of the merged plan: it serialises it once here,
    hashes those exact bytes and writes them, so `update --plan` hashes to
    `merged_plan_sha256` and promotion copies bytes rather than re-serialising."""
    merged_bytes = json_bytes(cs["merged_plan"])
    sha = hashlib.sha256(merged_bytes).hexdigest()
    cs["merged_plan_sha256"] = sha
    cs["merged_plan_file"] = f"campaign-plan.{sha[:8]}.json"
    plan_path = Path(out) / cs["merged_plan_file"]
    atomic_write_bytes(plan_path, merged_bytes)
    cs_path = Path(out) / CHANGE_SET_NAME
    atomic_write_json(cs_path, cs)
    return plan_path, cs_path


def print_change_set(cs: dict) -> None:
    print(f"Change set for campaign {cs['campaign_id']} on store {cs['store_slug']}  "
          f"(run {cs['run_id']})")
    print(f"  base plan     {cs['base_plan_sha256'][:8]}")
    print(f"  merged plan   {cs['merged_plan_file'] or '(not written yet)'}")
    print(f"  baseline      {cs['baseline_sha256'][:8]}")
    if cs["preserved"]:
        print("Kept from the store (the candidate did not touch these):")
        for x in cs["preserved"]:
            print(f"  - {x}")
    if cs["warnings"]:
        print("Warnings:")
        for x in cs["warnings"]:
            print(f"  - {x}")
    if not cs["ops"]:
        print("No requests: this change set only records values preserved from the store.")
        return
    print(f"Requests update will send, in order ({len(cs['ops'])}"
          + (", including DELETEs" if cs["has_deletes"] else "") + "):")
    for op in cs["ops"]:
        body = op["body"] if op["body"] is not None else op.get("body_template")
        shown = "" if body is None else "  " + json.dumps(body)
        dep = f"  (after step {op['depends_on']})" if op.get("depends_on") else ""
        print(f"  {op['n']:>2}. {op['method']:<6} {op['section']} {op['key']}  {op['path']}{shown}{dep}")
        if op["before"] or op["after"]:
            print(f"      before {json.dumps(op['before'])}  after {json.dumps(op['after'])}")


def update_command(cs: dict, plan_path: Path, manifest_path: Path, cs_path: Path, cs_sha: str) -> str:
    """The exact command that approves this change set."""
    return (f"{PROG} update --plan {plan_path} --manifest {manifest_path} "
            f"--change-set {cs_path} --yes --change-set-sha256 {cs_sha}"
            + (" --allow-delete" if cs["has_deletes"] else ""))


# --------------------------------------------------------------------------- #
# update: gate, journal, execution
# --------------------------------------------------------------------------- #

class ChangeSetNotApplied(CampaignAdminError):
    """A gate, not a failure: nothing was sent, and the operator has something to read
    or a flag to add. `main` turns it into exit 2, the same code the apply gate uses."""


# Everything resume compares on a campaign, and the per-section views it compares.
CAMPAIGN_DIFF_FIELDS = (CAMPAIGN_SCALARS + CAMPAIGN_NULLABLE + CAMPAIGN_CODE_LISTS
                        + ("additional_currencies",))
RESUME_PACKAGE_FIELDS = PACKAGE_DIFF_FIELDS + ("product_variant_id", "image")


def check_change_set(man: Manifest, base: dict, base_sha: str, plan: dict, plan_sha: str,
                     cs: dict, *, resume: bool = False) -> None:
    """Bind a change set to this run before anything is sent: it was written for this
    run, this store and this campaign, against the canonical plan that is on disk now,
    and every op names an object this run owns on the exact route for it. Raises
    `ChangeSetNotApplied`, so a refusal here is exit 2 and nothing went out."""
    if cs.get("schema") != CHANGE_SET_SCHEMA:
        raise ChangeSetNotApplied(
            f"this change set is schema {cs.get('schema')!r} and this engine writes schema "
            f"{CHANGE_SET_SCHEMA}; re-run diff to write it again")
    camp = man.data.get("campaign") or {}
    cid = camp.get("id")
    for what, got, want in (("run_id", cs.get("run_id"), man.data.get("run_id")),
                            ("store_slug", cs.get("store_slug"), man.data.get("store_slug")),
                            ("campaign_id", cs.get("campaign_id"), cid)):
        if got != want:
            raise ChangeSetNotApplied(
                f"this change set names {what} {got!r} and this run's manifest names {want!r}; a "
                "change set belongs to the one run and the one campaign it was written for")
    if plan.get("store_slug") != man.data.get("store_slug"):
        raise ChangeSetNotApplied(
            f"the plan passed with --plan names store {plan.get('store_slug')!r}, not "
            f"{man.data.get('store_slug')!r} as this run's manifest does")
    if cs.get("base_plan_sha256") != man.data.get("plan_sha256"):
        if cs.get("merged_plan_sha256") == man.data.get("plan_sha256"):
            raise ChangeSetNotApplied(
                "this change set has already been applied: its merged plan is this run's canonical "
                "plan. Run diff again for the next change.")
        raise ChangeSetNotApplied(
            f"this change set was written against plan {str(cs.get('base_plan_sha256'))[:8]} and this "
            f"run is on plan {str(man.data.get('plan_sha256'))[:8]}; the run moved on after the diff. "
            "Re-run diff and review the new change set.")
    if cs.get("base_plan_sha256") != base_sha:
        raise ChangeSetNotApplied(
            f"{PLAN_NAME} in the run directory hashes to {base_sha[:8]}, not the "
            f"{str(cs.get('base_plan_sha256'))[:8]} this change set was written against; the canonical "
            "plan has been edited in place. Restore it (the archives in this directory are byte "
            "copies) and re-run diff.")

    active, deleting, base_keys = {}, {}, {}
    for section in SECTION_ROUTES:
        for e in man.data.get(section) or []:
            if e.get("id") is None:
                continue
            if e.get("status") == "created":
                active[(section, e.get("key"))] = e["id"]
            elif e.get("status") == "deleting":
                deleting[(section, e.get("key"))] = e["id"]
    base_keys["packages"] = {p.get("key") for p in base.get("packages") or []}
    base_keys["shipping_methods"] = {ship_key(s) for s in base.get("shipping_methods") or []}
    base_keys["offers"] = {o.get("key") for o in base.get("offers") or []}
    removed_by_op = {x.get("op"): x for x in man.data.get("removed") or []}
    by_n = {o.get("n"): o for o in cs.get("ops") or []}

    for n, op in enumerate(cs.get("ops") or [], 1):
        if op.get("n") != n:
            raise ChangeSetNotApplied(
                f"change set op {op.get('n')!r} is out of step: ops are numbered from 1 in the order "
                "update sends them")
        method, section, key = op.get("method"), op.get("section"), op.get("key")
        if method not in ("PATCH", "POST", "PUT", "DELETE"):
            raise ChangeSetNotApplied(f"op {n}: {method!r} is not a method this engine sends")
        if section == "campaign":
            if method != "PATCH" or key != "campaign" or op.get("id") != cid:
                raise ChangeSetNotApplied(
                    f"op {n}: the campaign section takes one PATCH on campaign {cid}, not "
                    f"{method} {key!r} id {op.get('id')!r}")
            want = f"/api/admin/campaigns/{cid}/"
        elif section not in SECTION_ROUTES:
            raise ChangeSetNotApplied(f"op {n}: {section!r} is not a section of a campaign")
        elif method == "POST":
            if "id" in op:
                raise ChangeSetNotApplied(
                    f"op {n}: a POST creates the object, so it cannot name id {op.get('id')!r}")
            if (section, key) in active:
                raise ChangeSetNotApplied(
                    f"op {n}: {section} {key} is already an object this run owns (id "
                    f"{active[(section, key)]}); a POST would duplicate it")
            if key in base_keys[section]:
                raise ChangeSetNotApplied(
                    f"op {n}: {section} {key} is in the canonical plan already; a POST would "
                    "duplicate it")
            want = f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/"
        elif method == "PUT" and op.get("depends_on"):
            if "id" in op:
                raise ChangeSetNotApplied(
                    f"op {n}: this PUT waits for the POST in step {op['depends_on']}, so it cannot "
                    f"name id {op.get('id')!r}; its route is built from the id that POST returns")
            dep = by_n.get(op["depends_on"])
            if (dep is None or dep.get("method") != "POST" or dep.get("section") != section
                    or dep.get("key") != key or dep.get("n") >= n):
                raise ChangeSetNotApplied(
                    f"op {n}: depends_on {op['depends_on']} is not an earlier POST of {section} "
                    f"{key} in this change set")
            want = f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/<{key}>/image/"
        else:
            oid, owned = op.get("id"), active.get((section, key))
            if owned is None and resume and method == "DELETE":
                # A DELETE caught in flight left its entry `deleting`, and one this run
                # already journalled `done` left no entry at all: its receipt in
                # removed[] is matched by op number.
                owned = deleting.get((section, key))
            if owned is None and resume and method == "DELETE":
                rec = removed_by_op.get(n) or {}
                if rec.get("section") == section and rec.get("key") == key:
                    owned = rec.get("id")
            if oid is None or owned is None or oid != owned:
                raise ChangeSetNotApplied(
                    f"op {n}: {method} names {section} {key} id {oid!r}, which is not an object this "
                    "run owns" + (f"; the manifest records id {owned}" if owned is not None else ""))
            want = f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/{oid}/"
            if method == "PUT":
                want += "image/"
        if op.get("path") != want:
            raise ChangeSetNotApplied(
                f"op {n}: path {op.get('path')!r} is not the route for {method} {section} {key} on "
                f"campaign {cid} ({want})")

    if cs.get("merged_plan") != plan:
        raise ChangeSetNotApplied(
            "the merged plan inside the change set is not the plan passed with --plan; pass the file "
            f"diff wrote ({cs.get('merged_plan_file')}) and nothing else")
    if plan_sha != cs.get("merged_plan_sha256"):
        raise ChangeSetNotApplied(
            f"--plan hashes to {plan_sha[:8]}, not the {str(cs.get('merged_plan_sha256'))[:8]} this "
            "change set names; diff is the only writer of that file")


def _set_entry(man: Manifest, section: str, key: str, **fields) -> dict:
    """`Manifest.mark` without the save, for the few places that have to make several
    changes land as one write."""
    e = man.entry(section, key)
    if e is None:
        e = {"key": key}
        man.data[section].append(e)
    e.update(fields)
    return e


def _set_op(man: Manifest, n, status: str, **fields) -> None:
    for x in man.data.get("ops") or []:
        if x.get("n") == n:
            x["status"] = status
            x.update(fields)


def _save_op(man: Manifest, n, status: str, **fields) -> None:
    """One op's state, durably. Saved as `in_flight` before its request goes out, so a
    lost response is never mistaken for an op that was never attempted."""
    _set_op(man, n, status, **fields)
    man.save()


def _start_update(man: Manifest, cs: dict, cs_sha: str) -> None:
    """The journal, before the first write: what this update is and every op queued."""
    man.data["active_update"] = {"change_set_sha256": cs_sha,
                                 "merged_plan_sha256": cs.get("merged_plan_sha256"),
                                 "started_at": utcnow()}
    man.data["ops"] = [{"n": o["n"], "method": o["method"], "section": o["section"],
                        "key": o["key"], "status": "queued"} for o in cs.get("ops") or []]
    man.save()


def _remove_entry(man: Manifest, section: str, key: str, n) -> None:
    """A deleted object's entry leaves the active section and becomes a receipt in
    `removed[]`, so the section only ever holds what is on the campaign."""
    kept, rec = [], None
    for e in man.data.get(section) or []:
        if e.get("key") == key and rec is None:
            rec = dict(e, section=section, status="deleted", op=n, removed_at=utcnow())
        else:
            kept.append(e)
    man.data[section] = kept
    if rec is not None:
        man.data.setdefault("removed", []).append(rec)
    man.save()


def _op_route(op: dict, cid, package_ids: dict) -> str:
    """The path one op goes to. A PUT on a package created in the same change set has
    no id when the operator reviews it, so its route is rebuilt from the id its POST
    returned."""
    if op.get("depends_on"):
        pid = (package_ids or {}).get(op["key"])
        if pid is None:
            raise CampaignAdminError(
                f"op {op['n']}: package {op['key']} has no id yet, so there is no image route to PUT "
                f"to; step {op['depends_on']} has to create it first")
        return f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[op['section']]}/{pid}/image/"
    return op["path"]


def _op_body(op: dict, plan: dict, package_ids: dict):
    """The body one op sends. `body: null` on an offer op means its scope names a
    package this change set creates, so the real body cannot exist until that POST
    has answered: it is built here from the merged plan and the ids in hand. A null
    body is never sent."""
    if op["method"] == "DELETE":
        return None
    if op.get("body") is not None:
        return op["body"]
    if op["section"] != "offers":
        raise CampaignAdminError(f"op {op['n']}: {op['method']} {op['section']} carries no body")
    o = next((x for x in plan.get("offers") or [] if x.get("key") == op["key"]), None)
    if o is None:
        raise CampaignAdminError(f"op {op['n']}: offer {op['key']} is not in the merged plan")
    keys = list(op.get("package_keys") or o["condition"]["package_keys"])
    missing = [k for k in keys if package_ids.get(k) is None]
    if missing:
        raise CampaignAdminError(
            f"op {op['n']}: offer {op['key']} is scoped to package key(s) {missing} that have no id "
            "yet; their POSTs come first in this change set")
    full = offer_body(o, {k: package_ids[k] for k in keys})
    tmpl = op.get("body_template")
    if op["method"] == "PATCH" and isinstance(tmpl, dict):
        # Send exactly the fields the operator reviewed, with the scope's ids filled in.
        return {f: (full[f] if f in ("condition", "benefit") else v) for f, v in tmpl.items()}
    return full


def _refresh_identity(man: Manifest, op: dict, resp: dict, package_ids=None) -> None:
    """The entry's identity and recorded mutable fields, from the response to its own
    op: a POST is what creates the entry, a PATCH refreshes what it changed."""
    section, key, method = op["section"], op["key"], op["method"]
    if section == "campaign":
        if resp.get("name") is not None:
            man.mark("campaign", "campaign", name=resp.get("name"))
        return
    if method == "POST":
        fields = {"status": "created", "id": resp.get("id"), "intent": None}
        if section == "packages":
            fields.update(name=resp.get("name"), product_variant_id=resp.get("product_variant_id"),
                          image_at_create=resp.get("image"))
        elif section == "shipping_methods":
            fields.update(shipping_method=resp.get("shipping_method"))
        else:
            fields.update(name=resp.get("name"), code=resp.get("code"))
        man.mark(section, key, **fields)
        if section == "packages" and package_ids is not None:
            package_ids[key] = resp.get("id")
        return
    fields, e = {}, man.entry(section, key) or {}
    if section in ("packages", "offers") and resp.get("name") is not None:
        fields["name"] = resp.get("name")
    if section == "shipping_methods" and resp.get("shipping_method") is not None:
        fields["shipping_method"] = resp.get("shipping_method")
    if section == "offers" and "code" in e:
        fields["code"] = resp.get("code")
    if fields:
        man.mark(section, key, **fields)


def _run_op(client: Client, man: Manifest, plan: dict, op: dict, package_ids: dict, cid,
            img_state: dict) -> None:
    """One request from the change set, journalled `in_flight` and durably saved before
    it goes out and `done` once the response is in."""
    section, key, method, n = op["section"], op["key"], op["method"], op["n"]
    path = _op_route(op, cid, package_ids)

    if method == "PUT":
        p = next((x for x in plan.get("packages") or [] if x.get("key") == key), None)
        e = man.entry("packages", key)
        if p is None or e is None or e.get("id") is None:
            raise CampaignAdminError(
                f"op {n}: package {key} is not an object this run owns, so its image cannot be set")
        # `set` is terminal in the shared image handler, so an approved re-send starts
        # that package's image over.
        man.mark("packages", key, image_status="pending")
        _save_op(man, n, "in_flight")
        _put_image(client, man, cid, p, e, img_state)
        if (man.entry("packages", key) or {}).get("image_status") == "pending":
            raise CampaignAdminError(
                "the package image endpoint gave no answer: " + "; ".join(img_state["failures"])
                + f"\n  Op {n} is left in flight and the image may or may not have landed. Re-run "
                "the same update with --resume.")
        _save_op(man, n, "done")
        return

    if method == "DELETE":
        man.mark(section, key, status="deleting")
        _save_op(man, n, "in_flight")
        status, body = client.request("DELETE", path)
        if status not in (200, 202, 204, 404):
            raise CampaignAdminError(
                f"op {n}: DELETE {path} returned {status}: {_short(body)}; the entry is left "
                "`deleting`. Re-run the same update with --resume, or close the update with "
                "--settle." + auth_hint(status, "DELETE", path))
        _remove_entry(man, section, key, n)
        _save_op(man, n, "done")
        print(f"deleted {section} {key}")
        return

    body = _op_body(op, plan, package_ids)
    _save_op(man, n, "in_flight")
    status, resp = client.request(method, path, body)
    if isinstance(resp, list) and len(resp) == 1 and isinstance(resp[0], dict):
        resp = resp[0]  # package create answers with a one-element list
    if status not in (200, 201) or not isinstance(resp, dict):
        raise CampaignAdminError(
            f"op {n}: {method} {path} returned {status}: {_short(resp)}; nothing after it was sent. "
            "Re-run the same update with --resume once the cause is fixed, or close the update with "
            "--settle." + auth_hint(status, method, path))
    _refresh_identity(man, op, resp, package_ids)
    _save_op(man, n, "done", **({"id": resp.get("id")} if method == "POST" else {}))
    if method == "POST":
        print(f"created {section} {key} id {resp.get('id')}")
    else:
        print(f"updated {section} {key}")


def _refresh_entries(man: Manifest, snap: dict, currency: str, patched: set) -> None:
    """Every active entry's recorded mutable fields, from the final live read: values
    this update preserved from the store included, so a later diff, verify or teardown
    compares against what is live rather than against what the plan used to say."""
    live = {s: {x.get("id"): x for x in snap.get(s) or []} for s in SECTION_ROUTES}
    lc = snap.get("campaign") or {}
    camp = man.data.get("campaign") or {}
    if lc.get("name") is not None:
        camp["name"] = lc.get("name")
    for section, fields in (("packages", ("name",)),
                            ("shipping_methods", ("shipping_method", "price")),
                            ("offers", ("name", "code"))):
        for e in man.data.get(section) or []:
            if e.get("status") != "created" or e.get("id") is None:
                continue
            x = live[section].get(e["id"])
            if x is None:
                continue
            for f in fields:
                if f == "price":
                    # Only entries that already record a price keep one (adopt writes it,
                    # apply does not); a refresh never adds a field the run did not have.
                    v = _money_or_none(_price_in(x, currency))
                    if "price" in e and v is not None:
                        e["price"] = v
                elif f == "code" and "code" not in e:
                    continue
                elif x.get(f) is not None or f in e:
                    e[f] = x.get(f)
            if section == "offers" and e.get("scope_conversion_pending") \
                    and (section, e.get("key")) in patched \
                    and not (x.get("condition") or {}).get("all_packages"):
                # The approved conversion landed: the offer is pinned to package ids now.
                e.pop("scope_conversion_pending", None)
    man.save()


# --------------------------------------------------------------------------- #
# promotion: the merged plan becomes the run's canonical plan, in 4 steps
# --------------------------------------------------------------------------- #

def archive_bytes(src, dest, expect_sha: str) -> None:
    """Copy `src` onto `dest` byte for byte, with both ends proved against
    `expect_sha`. Plan files are never re-serialised on the way: the bytes the
    operator reviewed are the bytes that land, so a merged plan re-indented by hand
    keeps its own formatting."""
    src, dest = Path(src), Path(dest)
    data = src.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != expect_sha:
        raise CampaignAdminError(
            f"{src} hashes to {got[:8]}, not the {expect_sha[:8]} this run recorded; refusing to copy "
            f"it onto {dest}")
    if _same_file(src, dest):
        return
    atomic_write_bytes(dest, data)
    landed = sha256_file(dest)
    if landed != expect_sha:  # pragma: no cover - a filesystem that changed the bytes
        raise CampaignAdminError(f"{dest} hashes to {landed[:8]} after the copy, not {expect_sha[:8]}")


def finish_promotion(man: Manifest, plan_path) -> bool:
    """Steps 3 and 4 of a promotion: copy the merged archive's bytes onto the canonical
    plan and write the manifest without `pending_promotion` or `active_update`. Safe to
    run again at any point, which is what makes an interrupted promotion recoverable.
    Returns True when it finished one."""
    pending = man.data.get("pending_promotion")
    if not pending:
        return False
    plan_path = Path(plan_path)
    src = plan_path.parent / str(pending.get("archived"))
    if not src.exists():
        raise CampaignAdminError(
            f"this run's plan promotion was interrupted and the merged plan it promotes to ({src}) is "
            "gone. Restore that file (the change set names it in merged_plan_file) and run the command "
            "again.")
    archive_bytes(src, plan_path, pending["sha256"])
    man.data.pop("pending_promotion", None)
    man.data.pop("active_update", None)
    man.data.pop("ops", None)
    man.data["promoted_at"] = utcnow()
    man.save()
    return True


def promote_plan(man: Manifest, plan_path, merged_archive, new_sha: str, *, history=None) -> None:
    """The merged plan becomes this run's canonical plan, in 4 steps that can each be
    interrupted without losing the run: 1 the old canonical plan is archived byte for
    byte, 2 the manifest commits to the new hash with a `history[]` entry and a
    `pending_promotion` naming the bytes to copy, 3 those bytes land on
    `campaign-plan.json`, 4 the manifest drops `pending_promotion` and
    `active_update`. Neither the archive nor the file the operator passed is consumed
    or moved."""
    plan_path, merged_archive = Path(plan_path), Path(merged_archive)
    old_sha = man.data.get("plan_sha256")
    archived = f"campaign-plan.{str(old_sha)[:8]}.json"
    if plan_path.exists():
        archive_bytes(plan_path, plan_path.parent / archived, old_sha)
    entry = dict(history or {})
    entry.setdefault("at", utcnow())
    entry["from_plan_sha256"] = old_sha
    entry["to_plan_sha256"] = new_sha
    entry["archived"] = archived
    man.data["plan_sha256"] = new_sha
    man.data.setdefault("history", []).append(entry)
    man.data["pending_promotion"] = {"archived": merged_archive.name, "sha256": new_sha}
    man.save()
    finish_promotion(man, plan_path)


def open_run(plan_arg, manifest_arg, *, allow_active_update: bool = False, guard: bool = True,
             need_plan: bool = True) -> tuple:
    """The one way a command opens a run directory: guard the manifest path, load the
    manifest, finish a promotion a crash interrupted, and only then read the plan and
    its hash in ONE read. Recovery can rewrite the canonical plan, so nothing may parse
    a plan before this has run. Returns (manifest, plan, plan_sha256, plan path)."""
    manifest_path = guard_manifest_path(manifest_arg) if guard else Path(manifest_arg)
    man = Manifest.load(manifest_path)
    check_origin_binding(man.data, "manifest")
    canonical = run_dir_for(manifest_path) / PLAN_NAME
    finish_promotion(man, canonical)
    if man.data.get("active_update") and not allow_active_update:
        raise CampaignAdminError(ACTIVE_UPDATE_MSG)
    if not need_plan:
        return man, None, None, canonical
    plan_path = Path(plan_arg).expanduser() if plan_arg else canonical
    plan, sha = load_json_and_hash(plan_path)
    return man, plan, sha, plan_path


# --------------------------------------------------------------------------- #
# update: resume and settle
# --------------------------------------------------------------------------- #

def _resume_view(section: str, obj: dict, key_of_package=None) -> dict:
    """One object in the field space the change set's `before` and `after` speak."""
    if section == "campaign":
        return {f: obj.get(f) for f in CAMPAIGN_DIFF_FIELDS}
    if section == "packages":
        return {f: obj.get(f) for f in RESUME_PACKAGE_FIELDS}
    if section == "shipping_methods":
        return {f: obj.get(f) for f in ("shipping_method", "price")}
    return _live_offer_n(obj, key_of_package)


def _key_map(man: Manifest) -> dict:
    """(section, live id) -> manifest key, for everything this run owns now plus the
    receipts of what it has deleted (the change set's baseline still names those)."""
    out = {}
    for section in SECTION_ROUTES:
        for e in man.data.get(section) or []:
            if e.get("id") is not None:
                out[(section, e["id"])] = e.get("key")
    for rec in man.data.get("removed") or []:
        if rec.get("id") is not None:
            out[(rec.get("section"), rec["id"])] = rec.get("key")
    return out


def _package_keys_for(norm: dict, key_by_id: dict) -> dict:
    """package id -> key for every live package, so an offer's scope reads in plan
    space even when one of its packages is not (yet) ours."""
    out = {pid: key for (section, pid), key in key_by_id.items() if section == "packages"}
    for x in norm.get("packages") or []:
        out.setdefault(x["id"], f"<unowned {x['id']}>")
    return out


def _one_live(section: str, obj: dict, currency: str, plan: dict, kop: dict) -> dict:
    """A single live object normalised into plan space, for a full read-back."""
    snap = {"campaign": {}, "packages": [], "shipping_methods": [], "offers": []}
    snap[section] = [obj]
    norm = normalize_snapshot(snap, currency, None, plan)
    return _resume_view(section, norm[section][0], kop)


def _resolve_in_flight(client: Client, man: Manifest, plan: dict, cs: dict, snap: dict,
                       norm: dict, currency: str, cid, journal: dict) -> None:
    """Every op left `in_flight` by an interruption, decided by reading the store: it
    landed with its response lost (mark it done), or it never went out (leave it to be
    sent). Anything in between refuses and names the object and the field, because no
    engine can tell a half-applied write from a dashboard edit."""
    live_raw = {s: {x.get("id"): x for x in snap.get(s) or []} for s in SECTION_ROUTES}
    for op in cs.get("ops") or []:
        if (journal.get(op["n"]) or {}).get("status") != "in_flight":
            continue
        section, key, method, n = op["section"], op["key"], op["method"], op["n"]
        kop = _package_keys_for(norm, _key_map(man))
        if method == "POST":
            cands = find_pending_candidates(client, man, plan, section, key)
            if not cands:
                continue  # never left, or never landed: the op is sent below
            if len(cands) > 1:
                raise CampaignAdminError(
                    f"op {n}: {section} {key} matches {len(cands)} objects on the campaign "
                    f"({[c.get('id') for c in cands]}); this engine never guesses which one it "
                    "created. Remove the duplicates in the dashboard, then resume.")
            full = client.get_ok(f"/api/admin/campaigns/{cid}/{SECTION_ROUTES[section]}/"
                                 f"{cands[0]['id']}/")
            got = _one_live(section, full, currency, plan, kop)
            want = dict(op["after"])
            diffs = [f"{f}: the store has {_show(got.get(f))}, this op would have created "
                     f"{_show(v)}" for f, v in want.items() if got.get(f) != v]
            if diffs:
                raise CampaignAdminError(
                    f"op {n}: {section} {key} was not created by this run: {section[:-1]} "
                    f"{full.get('id')} answers to the same identity but holds different values "
                    f"({'; '.join(diffs)}). It is not ours to claim. Remove it in the dashboard, or "
                    "close this update with update --settle.")
            # One save: ownership and the completed op land together or not at all.
            fields = {"status": "created", "id": full.get("id"), "intent": None, "reconciled": True}
            if section == "packages":
                fields.update(name=full.get("name"),
                              product_variant_id=full.get("product_variant_id"))
            elif section == "shipping_methods":
                fields.update(shipping_method=full.get("shipping_method"))
            else:
                fields.update(name=full.get("name"), code=full.get("code"))
            _set_entry(man, section, key, **fields)
            _set_op(man, n, "done", id=full.get("id"))
            journal[n]["status"] = "done"
            man.save()
            continue

        if method == "DELETE":
            x = live_raw[section].get(op.get("id"))
            if x is None:
                _remove_entry(man, section, key, n)
                _save_op(man, n, "done")
                journal[n]["status"] = "done"
                continue
            got = _resume_view(section, next(o for o in norm[section] if o["id"] == op["id"]), kop)
            diffs = [f for f, v in (op.get("before") or {}).items() if got.get(f) != v]
            if diffs:
                raise CampaignAdminError(
                    f"op {n}: {section} {key} (id {op['id']}) is still on the campaign and no longer "
                    f"matches what the diff showed ({', '.join(diffs)}); it was changed while this "
                    "update was not running. Review it, then close this update with update --settle.")
            continue

        if method == "PUT":
            x = live_raw["packages"].get(op.get("id") or (man.entry("packages", key) or {}).get("id"))
            if x is None:
                continue
            if (x.get("image") or None) == (op.get("before") or {}).get("image"):
                continue  # the PUT never landed: it is re-sent below
            # It landed, or the image was changed outside this run. Either way the live
            # URL is the receipt and nothing is overwritten.
            _set_entry(man, "packages", key, image_status="set", image=x.get("image"),
                       image_src=(op.get("after") or {}).get("image_src"), image_intent=None,
                       image_error=None)
            _set_op(man, n, "done")
            journal[n]["status"] = "done"
            man.save()
            continue

        # PATCH: every field it sends is either still at `before` or already at `after`.
        if section == "campaign":
            obj = norm["campaign"]
        else:
            obj = next((o for o in norm[section] if o["id"] == op.get("id")), None)
            if obj is None:
                continue
        got = _resume_view(section, obj, kop)
        before, after = op.get("before") or {}, op.get("after") or {}
        if all(got.get(f) == v for f, v in after.items()):
            _set_op(man, n, "done")
            journal[n]["status"] = "done"
            man.save()
            continue
        if all(got.get(f) == v for f, v in before.items()):
            continue
        detail = "; ".join(f"{f}: the store has {_show(got.get(f))}, before was {_show(before.get(f))}, "
                           f"after is {_show(v)}" for f, v in after.items() if got.get(f) != v)
        raise CampaignAdminError(
            f"op {n}: {section} {key} is at neither the value before this update nor the value after "
            f"it ({detail}); it was changed while the update was not running. Put it back to one of "
            "them in the dashboard and resume, or close this update with update --settle.")


def _resume_check(man: Manifest, cs: dict, norm: dict, journal: dict) -> None:
    """The whole live snapshot against the state this interrupted update left: the
    reviewed baseline with the `after` of every op journalled `done`. A `queued` op was
    never sent, so its fields are still at `before`; anything else that differs is a
    dashboard edit made during the interruption, and it refuses by name."""
    key_by_id = _key_map(man)
    kop = _package_keys_for(norm, key_by_id)
    baseline = cs.get("baseline") or {}
    exp = {("campaign", "campaign"): _resume_view("campaign", baseline.get("campaign") or {})}
    for section in SECTION_ROUTES:
        for obj in baseline.get(section) or []:
            k = key_by_id.get((section, obj.get("id")))
            if k is None:
                raise CampaignAdminError(
                    f"the change set's baseline names {section} {obj.get('id')}, which this run does "
                    "not own; the run directory and the change set are out of step")
            exp[(section, k)] = _resume_view(section, obj, kop)
    skip = set()
    for op in cs.get("ops") or []:
        if (journal.get(op["n"]) or {}).get("status") != "done":
            continue
        at = (op["section"], op["key"])
        if op["method"] == "DELETE":
            exp.pop(at, None)
        elif op["method"] == "POST":
            exp[at] = dict(op.get("after") or {})
            if op["section"] == "packages":
                skip.add((at, "image"))
        elif op["method"] == "PUT":
            # The store builds the thumbnail, so its value after a PUT is not knowable.
            skip.add((at, "image"))
        else:
            exp.setdefault(at, {}).update(op.get("after") or {})

    live, unmanaged = {("campaign", "campaign"): _resume_view("campaign", norm["campaign"])}, []
    for section in SECTION_ROUTES:
        for obj in norm[section]:
            k = key_by_id.get((section, obj["id"]))
            if k is None:
                what = obj.get("name") or obj.get("shipping_method")
                unmanaged.append(f"{section} {obj['id']} ({what!r})")
                continue
            live[(section, k)] = _resume_view(section, obj, kop)
    if unmanaged:
        raise CampaignAdminError(
            "the campaign carries object(s) this run does not own: " + ", ".join(sorted(unmanaged))
            + ". An interrupted update never claims an object it cannot prove it created. Remove them "
            "in the dashboard, or close this update with update --settle.")

    problems = []
    for at in sorted(exp, key=lambda x: (x[0], str(x[1]))):
        if at not in live:
            problems.append(f"{at[0]} {at[1]} is no longer on the campaign")
            continue
        for f, want in sorted(exp[at].items()):
            if (at, f) in skip:
                continue
            got = live[at].get(f)
            if got != want:
                problems.append(f"{at[0]} {at[1]}: {f} is {_show(got)} on the store, and this update "
                                f"left it at {_show(want)}")
    if problems:
        raise CampaignAdminError(
            "the campaign is not in the state this interrupted update left it in:\n  - "
            + "\n  - ".join(problems)
            + "\n  Something changed it while the update was not running, so resuming would overwrite "
              "that change. Review those values, then close this update with update --settle and run a "
              "fresh diff.")


def _settled_plan(base: dict, plan: dict, cs: dict, applied: set) -> dict:
    """The base plan with the `after` of every op that landed: keys an applied POST
    created are added from the merged plan, keys an applied DELETE removed are dropped,
    and nothing else moves. This is the plan that describes the campaign after an
    update that cannot finish."""
    out = json.loads(json.dumps(base))
    pk = {p["key"]: p for p in out.get("packages") or []}
    sm = {ship_key(s): s for s in out.get("shipping_methods") or []}
    of = {o["key"]: o for o in out.get("offers") or []}
    mp = {p["key"]: p for p in plan.get("packages") or []}
    ms = {ship_key(s): s for s in plan.get("shipping_methods") or []}
    mo = {o["key"]: o for o in plan.get("offers") or []}
    merged = {"packages": mp, "shipping_methods": ms, "offers": mo}
    drop = {s: set() for s in SECTION_ROUTES}
    add = {s: [] for s in SECTION_ROUTES}
    for op in cs.get("ops") or []:
        if op["n"] not in applied:
            continue
        section, key, method, after = op["section"], op["key"], op["method"], op.get("after") or {}
        if section == "campaign":
            out["campaign"].update(after)
            continue
        if method == "DELETE":
            drop[section].add(key)
            continue
        if method == "POST":
            add[section].append(key)
            continue
        if method == "PUT":
            if key in pk and key in mp:
                img = mp[key].get("image")
                if img:
                    pk[key]["image"] = img
            continue
        if section == "packages":
            n = dict(plan_package_n(pk[key]))
            n.update(after)
            new = _apply_package_n(pk[key], n)
            pk[key].clear()
            pk[key].update(new)
        elif section == "shipping_methods":
            sm[key]["price"] = after.get("price", sm[key]["price"])
        else:
            n = dict(plan_offer_n(of[key]))
            n.update(after)
            new = _apply_offer_n(of[key], n)
            of[key].clear()
            of[key].update(new)
    for section in ("packages", "shipping_methods", "offers"):
        kept = [x for x in out.get(section) or []
                if (ship_key(x) if section == "shipping_methods" else x.get("key"))
                not in drop[section]]
        out[section] = kept + [merged[section][k] for k in add[section] if k in merged[section]]
    return out


def _settle(client: Client, man: Manifest, base: dict, base_path: Path, plan: dict, cs: dict,
            cs_sha: str, snap: dict, norm: dict, currency: str, cid, journal: dict) -> Manifest:
    """Close an update that cannot finish. Sends nothing to the store: it reads back
    every op left in flight, writes the plan that describes what actually landed,
    promotes it through the same 4 steps and clears `active_update`. The operator then
    runs a fresh diff for the remainder."""
    _resolve_in_flight(client, man, plan, cs, snap, norm, currency, cid, journal)
    applied = {o["n"] for o in cs.get("ops") or []
               if (journal.get(o["n"]) or {}).get("status") == "done"}
    # An op that did not land leaves its object alone: a DELETE marked `deleting`
    # before the run stopped goes back to `created`, or every later command would read
    # the run as torn down.
    for op in cs.get("ops") or []:
        if op["n"] in applied or op["method"] != "DELETE":
            continue
        e = man.entry(op["section"], op["key"])
        if e is not None and e.get("status") == "deleting":
            _set_entry(man, op["section"], op["key"], status="created")
    outcomes = [{"n": o["n"], "method": o["method"], "section": o["section"], "key": o["key"],
                 "outcome": "applied" if o["n"] in applied else "not applied"}
                for o in cs.get("ops") or []]
    settled = _settled_plan(base, plan, cs, applied)
    errs = validate_plan(settled)
    if errs:
        raise CampaignAdminError(
            "the settled plan (the base plan plus what this update landed) is invalid, so it cannot "
            "become this run's plan:\n  - " + "\n  - ".join(errs)
            + "\n  Put the campaign into a state a plan can describe (the dashboard), then run "
              "update --settle again.")
    data = json_bytes(settled)
    new_sha = hashlib.sha256(data).hexdigest()
    archive = Path(base_path).parent / f"campaign-plan.{new_sha[:8]}.json"
    atomic_write_bytes(archive, data)
    patched = {(o["section"], o["key"]) for o in cs.get("ops") or []
               if o["method"] == "PATCH" and o["n"] in applied}
    _refresh_entries(man, snap, currency, patched)
    promote_plan(man, base_path, archive, new_sha, history={
        "change_set_sha256": cs_sha, "settled": True, "ops": outcomes})
    print(f"settled: {sum(1 for o in outcomes if o['outcome'] == 'applied')} of {len(outcomes)} "
          f"op(s) had landed; nothing was sent to the store")
    for o in outcomes:
        print(f"  {o['n']:>2}. {o['method']:<6} {o['section']} {o['key']}  {o['outcome']}")
    print(f"this run's plan is now {archive.name}'s bytes, as {PLAN_NAME}")
    print("Run diff again for the work that is left.")
    return man


def update(client: Client, man: Manifest, plan: dict, plan_sha: str, cs: dict, cs_sha: str, *,
           allow_delete: bool = False, resume: bool = False, settle: bool = False,
           plan_path=None) -> Manifest:
    """Apply one reviewed change set. `plan` is the merged plan `diff` wrote and `cs`
    the change set that names it; every gate runs before the first request, and the
    journal records each op `in_flight` before it is sent."""
    if resume and settle:
        raise ChangeSetNotApplied("pass --resume or --settle, not both")
    check_origin_binding(man.data, "manifest")
    camp = man.data.get("campaign") or {}
    if camp.get("status") != "created":
        raise CampaignAdminError(
            f"the manifest's campaign is {camp.get('status')!r}, not created; there is nothing to "
            "update")
    cid = camp["id"]
    base_path = run_dir_for(man.path) / PLAN_NAME
    base, base_sha = load_json_and_hash(base_path)
    check_change_set(man, base, base_sha, plan, plan_sha, cs, resume=resume or settle)

    active = man.data.get("active_update") or {}
    if resume or settle:
        if not active:
            raise ChangeSetNotApplied(
                "no update is in progress in this run, so there is nothing to resume or settle; run "
                "diff for the change you want")
        if active.get("change_set_sha256") != cs_sha:
            raise ChangeSetNotApplied(
                f"the update in progress was started from change set "
                f"{str(active.get('change_set_sha256'))[:8]}, not {cs_sha[:8]}; finish that one first")
        journal = {x.get("n"): x for x in man.data.get("ops") or []}
        missing = [o["n"] for o in cs.get("ops") or [] if o["n"] not in journal]
        if missing:
            raise CampaignAdminError(
                f"the journal in {man.path} does not describe op(s) {missing} of this change set; the "
                "manifest and the change set are out of step")
    elif active:
        raise ChangeSetNotApplied(
            ACTIVE_UPDATE_MSG + ", or close it with update --settle")
    else:
        journal = {}

    delete_keys = {(o["section"], o["key"]) for o in cs.get("ops") or [] if o["method"] == "DELETE"}
    for section in SECTION_ROUTES:
        for e in man.data.get(section) or []:
            st = e.get("status")
            if st == "created":
                continue
            if (resume or settle) and st == "deleting" and (section, e.get("key")) in delete_keys:
                continue  # this change set's own DELETE, caught mid-flight
            if st in TORN_DOWN_STATUSES:
                raise CampaignAdminError(
                    f"{section} {e.get('key')} is {st!r}: this run was torn down, fully or in part, "
                    "so there is nothing left to update")
            raise CampaignAdminError(
                f"{section} {e.get('key')} is {st!r}, not created; finish the run with apply --resume, "
                "or tear it down, before updating anything")

    # --settle sends nothing, so the delete approval is not its gate: it closes an
    # update by reading the store, and a DELETE that landed is already a fact.
    if not settle and any(o["method"] == "DELETE" for o in cs.get("ops") or []) \
            and not allow_delete:
        raise ChangeSetNotApplied(
            "this change set deletes object(s) from the campaign and --allow-delete was not passed; "
            "nothing was sent. Re-read the DELETE steps above, then add --allow-delete to the same "
            "command.")

    currency = base["campaign"]["currency"]
    snap = snapshot_live(client, cid)
    norm = normalize_snapshot(snap, currency, man, base)
    problems = check_identity(man, base, snap, ops=cs.get("ops"))
    if problems:
        raise CampaignAdminError("ownership check failed; refusing to update:\n  - "
                                 + "\n  - ".join(problems))
    if settle:
        return _settle(client, man, base, base_path, plan, cs, cs_sha, snap, norm, currency, cid,
                       journal)
    if resume:
        _resolve_in_flight(client, man, plan, cs, snap, norm, currency, cid, journal)
        # The claims above are local, so the snapshot in hand is still current; it is
        # re-normalised because a claimed entry changes how a package name reads.
        _resume_check(man, cs, normalize_snapshot(snap, currency, man, base), journal)
    else:
        got = baseline_sha256(norm)
        if got != cs.get("baseline_sha256"):
            raise ChangeSetNotApplied(
                f"campaign changed since you reviewed the diff; re-run diff (the campaign now reads "
                f"back as {got[:8]}, and the change set was reviewed against "
                f"{str(cs.get('baseline_sha256'))[:8]})")
        _start_update(man, cs, cs_sha)
        journal = {x.get("n"): x for x in man.data.get("ops") or []}

    package_ids = {e["key"]: e["id"] for e in man.data.get("packages") or []
                   if e.get("status") == "created" and e.get("id") is not None}
    img_state = {"unsupported": False, "failures": [],
                 "set_ok": any(e.get("image_status") == "set" for e in man.data.get("packages") or [])}
    ran = False
    for op in cs.get("ops") or []:
        if (journal.get(op["n"]) or {}).get("status") == "done":
            continue
        _run_op(client, man, plan, op, package_ids, cid, img_state)
        ran = True

    patched = {(o["section"], o["key"]) for o in cs.get("ops") or [] if o["method"] == "PATCH"}
    # The campaign is re-read only when this run changed it; a change set that sent
    # nothing (0 ops, or a resume with everything already done) is already in hand.
    _refresh_entries(man, snapshot_live(client, cid) if ran else snap, currency, patched)

    merged_archive = Path(base_path).parent / (cs.get("merged_plan_file")
                                               or f"campaign-plan.{plan_sha[:8]}.json")
    if not merged_archive.exists():
        # diff is the only writer of that file; if it is gone, the reviewed bytes are
        # still the ones --plan carries, and they are hash-bound to the change set.
        archive_bytes(plan_path or merged_archive, merged_archive, plan_sha)
    promote_plan(man, base_path, merged_archive, plan_sha, history={
        "change_set_sha256": cs_sha,
        "ops": [{"n": o["n"], "method": o["method"], "section": o["section"], "key": o["key"],
                 "outcome": "applied"} for o in cs.get("ops") or []]})

    if img_state["failures"]:
        raise CampaignAdminError(
            "package image override(s) did not land:\n    - " + "\n    - ".join(img_state["failures"])
            + f"\n  Everything else in this change set landed and {PLAN_NAME} is the merged plan now. "
              "A rejected src is terminal: set that image in the dashboard, or diff a corrected plan.")
    return man


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

    ad = sub.add_parser("adopt", help="take ownership of a campaign that already exists")
    ad.add_argument("--store", required=True, help="store subdomain, e.g. mystore or mystore.29next.store")
    ad.add_argument("--campaign", required=True, type=int,
                    help="campaign id (never a name: campaign names are not unique)")
    ad.add_argument("--out")
    ad.add_argument("--convert-scope", action="append", type=int, metavar="OFFER_ID",
                    help="approve converting that live all_packages offer to the campaign's current "
                         "packages, repeatable")

    df = sub.add_parser("diff", help="three-way diff of an edited plan against the base plan and the store")
    df.add_argument("--plan", required=True, help="the edited copy, e.g. campaign-plan.next.json")
    df.add_argument("--manifest", required=True)
    df.add_argument("--out")
    df.add_argument("--delete-changed", action="append", metavar="SECTION:KEY",
                    help="authorise deleting an object the store changed since the base plan, e.g. "
                         "offers:tier-2, repeatable")

    up = sub.add_parser("update", help="apply a reviewed change set to the campaign (gated)")
    up.add_argument("--plan", required=True,
                    help="the merged plan diff wrote, e.g. campaign-plan.<sha8>.json")
    up.add_argument("--manifest", required=True)
    up.add_argument("--change-set", required=True, help=f"the {CHANGE_SET_NAME} diff wrote")
    up.add_argument("--yes", action="store_true")
    up.add_argument("--change-set-sha256")
    up.add_argument("--allow-delete", action="store_true",
                    help="approve the DELETE step(s) this change set carries")
    ug = up.add_mutually_exclusive_group()
    ug.add_argument("--resume", action="store_true",
                    help="finish an update that was interrupted mid-run")
    ug.add_argument("--settle", action="store_true",
                    help="close an update that cannot finish: sends nothing, promotes what landed")

    m = sub.add_parser("metadata", help="audit (default) or create the campaign metadata definitions")
    m.add_argument("--store", required=True, help="store subdomain, e.g. mystore or mystore.29next.store")
    m.add_argument("--apply", action="store_true", help="create the missing definitions (needs metadata:write)")

    args = ap.parse_args(argv)
    try:
        return _dispatch(args)
    except ChangeSetNotApplied as e:
        # A gate, like the apply hash gate: nothing was sent, so it gets exit 2.
        print(f"ERROR: {e}\nNOT APPLIED: nothing was sent to the store.", file=sys.stderr)
        return 2
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
        path = out / PLAN_NAME
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
        with run_lock(out):
            if args.resume:
                # Recovery and the in-progress refusal before anything else reads the
                # run: a half-finished promotion must not hand apply a stale plan.
                open_run(None, args.resume, guard=False, need_plan=False)
            man = apply(client, plan, sha, out / MANIFEST_NAME,
                        Path(args.resume) if args.resume else None)
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
        # The manifest is guarded and opened first: recovering an interrupted
        # promotion rewrites the plan, so reading the plan before that could verify a
        # campaign against bytes the run has already moved past.
        manifest_path = guard_manifest_path(args.manifest)
        with run_lock(run_dir_for(manifest_path)):
            man, plan, plan_sha, _p = open_run(args.plan, manifest_path, guard=False)
            client = _client_for(plan["store_slug"])
            out = resolve_out_dir(args.out, run_dir_for(args.plan))
            cart = Client(CART_API_ORIGIN, man.data["campaign"].get("api_key"), auth_scheme="raw", send_version_header=False)
            report = verify(client, cart, man, plan, plan_sha)
            atomic_write_json(out / "verify-report.json", report)
            print_verify(report)
            return 0 if report["result"] == "PASS" else 1

    if args.cmd == "teardown":
        # Teardown saves status changes back to the manifest (a campaign GET answering
        # 404 writes before any confirmation), so it gets the run-directory guard
        # first, and the plan is read only after a half-finished promotion is settled.
        manifest_path = guard_manifest_path(args.manifest)
        with run_lock(run_dir_for(manifest_path)):
            man, plan, plan_sha, _p = open_run(args.plan, manifest_path, guard=False)
            client = _client_for(plan["store_slug"])

            def confirm():
                if args.yes:
                    return True
                if sys.stdin.isatty():
                    return input("Type DELETE to confirm: ").strip() == "DELETE"
                return False
            teardown(client, man, plan, plan_sha, confirm)
        return 0

    if args.cmd == "adopt":
        slug = normalize_store(args.store)
        client = _client_for(slug)
        out = resolve_out_dir(args.out, Path.cwd() / RUNS_DIR_NAME / f"{slug}-{args.campaign}")
        with run_lock(out):
            _plan_path, man_path = adopt(client, slug, args.campaign, out,
                                         convert_scope=args.convert_scope or [])
        return 0 if man_path is not None else 1

    if args.cmd == "diff":
        manifest_path = guard_manifest_path(args.manifest)
        with run_lock(run_dir_for(manifest_path)):
            return _diff_cmd(args, manifest_path)

    if args.cmd == "update":
        # The hash gate first: without --yes and a matching hash nothing is built, no
        # client exists and nothing can be sent.
        cs, cs_sha = load_json_and_hash(args.change_set)
        if not args.yes or args.change_set_sha256 != cs_sha:
            print_change_set(cs)
            print(f"\nNOT APPLIED: pass --yes --change-set-sha256 {cs_sha} to approve this exact "
                  "change set.", file=sys.stderr)
            return 2
        manifest_path = guard_manifest_path(args.manifest)
        with run_lock(run_dir_for(manifest_path)):
            man, plan, plan_sha, plan_path = open_run(args.plan, manifest_path, guard=False,
                                                      allow_active_update=True)
            client = _client_for(man.data["store_slug"])
            man = update(client, man, plan, plan_sha, cs, cs_sha, plan_path=plan_path,
                         allow_delete=args.allow_delete, resume=args.resume, settle=args.settle)
            if not args.settle:
                camp = man.data["campaign"]
                print(f"\nDONE: campaign {camp['id']} {camp.get('name')!r} on "
                      f"{man.data['store_origin']}")
                print(f"  {PLAN_NAME} is the merged plan now ({plan_sha[:8]})")
                for e in man.data["packages"]:
                    print(f"  package {e['key']}: id {e.get('id')}  "
                          f"(data-next-package-id=\"{e.get('id')}\")")
                for e in man.data["offers"]:
                    print(f"  offer {e['key']}: id {e.get('id')}")
                for rec in man.data.get("removed") or []:
                    print(f"  removed {rec.get('section')} {rec.get('key')} (id {rec.get('id')})")
                print(f"  the campaign api_key does not change; it stays in {man.path}")
        return 0

    if args.cmd == "metadata":
        slug = normalize_store(args.store)
        return metadata_provision(_client_for(slug), slug, args.apply)
    return 1  # pragma: no cover


def _diff_cmd(args, manifest_path: Path) -> int:
    """`diff` with the run directory already locked."""
    man, base, base_sha, base_path = open_run(None, manifest_path, guard=False)
    run_dir = run_dir_for(manifest_path)
    if base_sha != man.data.get("plan_sha256"):
        raise CampaignAdminError(
            f"{base_path} does not hash to the manifest's plan_sha256; the canonical plan has been "
            "edited in place. Restore it (the archives in this directory are byte copies), or adopt "
            "the campaign into a fresh run directory. Edits belong in a copy, not in this file.")
    cand_path = Path(args.plan)
    if _same_file(cand_path, base_path):
        raise CampaignAdminError(
            f"--plan is the canonical plan itself ({base_path}); diff compares an edited COPY with "
            "it. Copy it to campaign-plan.next.json, edit that, and pass the copy.")
    cand = load_json(cand_path)
    client = _client_for(base["store_slug"])
    snap = snapshot_live(client, man.data["campaign"]["id"])
    cs = diff_change_set(base, cand, man, snap, delete_changed=args.delete_changed or [])
    if not cs["ops"] and not cs["preserved"]:
        print("no changes: the candidate plan matches both the base plan and the store")
        return 0
    out = resolve_out_dir(args.out, run_dir)
    plan_path, cs_path = write_change_set(out, cs)
    print_change_set(cs)
    print(f"wrote {plan_path}")
    print(f"wrote {cs_path}")
    print("Approve with: "
          + update_command(cs, plan_path, manifest_path, cs_path, sha256_file(cs_path)))
    return 0



if __name__ == "__main__":
    sys.exit(main())
