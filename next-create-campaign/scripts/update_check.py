#!/usr/bin/env python3
"""Tell the operator whether this installed skill is behind the published version.

Normally run through `next-create-campaign.sh check-update`, which prints the
installed version line first and guarantees exit 0 even if this script cannot
start. Run directly (`python3 scripts/update_check.py`) where there is no bash.

What it does:
- Reads `name:` and `version:` from the SKILL.md next to this script. The
  installed version is always read from disk and never cached.
- Reads the latest published version of that skill from the public catalog,
  skills.json on the main branch of NextCommerceCo/skills. One anonymous GET,
  bounded to a few seconds, cached for 24 hours (1 hour after a failure) in
  ${XDG_CACHE_HOME:-~/.cache}/next-skills/catalog.json, shared by every NEXT skill.
- Prints one notice: up to date, update available (with the update command for
  the way this copy was installed), installed is newer, or could not check.

What it never does: download or install anything, write inside the skill
directory, read .env or any token, send credentials, or exit non-zero.

Environment:
  NEXT_SKILLS_NO_UPDATE_CHECK=1  skip the check entirely
  NEXT_SKILLS_CATALOG_URL        test-only catalog override; bypasses the cache and
                                 labels the notice with the override URL

Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

REPO = "NextCommerceCo/skills"
CATALOG_URL = "https://raw.githubusercontent.com/NextCommerceCo/skills/main/skills.json"
CATALOG_PAGE = "https://github.com/NextCommerceCo/skills/blob/main/skills.json"
UPDATING_DOCS = "https://github.com/NextCommerceCo/skills#updating"
NOTES_URL = "https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+{skill}"

SUCCESS_TTL = 24 * 3600
FAILURE_TTL = 3600
FETCH_DEADLINE = 3.0
MAX_BYTES = 1 << 20
USER_AGENT = "next-skills-update-check/1"

# Same pattern as scripts/skill_catalog.py, which does not travel with an install.
SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
FRONTMATTER_FIELD_RE = re.compile(r"^(name|version):\s*(.*?)\s*$")

DISABLE_ENV = "NEXT_SKILLS_NO_UPDATE_CHECK"
OVERRIDE_ENV = "NEXT_SKILLS_CATALOG_URL"


# ---------------------------------------------------------------- versions

def read_frontmatter(skill_md: Path) -> dict:
    """`name` and `version` from the leading --- block of a SKILL.md, quotes stripped."""
    fields: dict = {}
    try:
        lines = skill_md.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return fields
    if not lines or lines[0].strip() != "---":
        return fields
    for line in lines[1:]:
        if line.strip() == "---":
            break
        match = FRONTMATTER_FIELD_RE.match(line)
        if match and match.group(1) not in fields:
            fields[match.group(1)] = match.group(2).strip("\"'")
    return fields


def parse_semver(value) -> tuple | None:
    if not isinstance(value, str):
        return None
    match = SEMVER_RE.fullmatch(value)
    return tuple(int(part) for part in match.groups()) if match else None


# ---------------------------------------------------------------- catalog

def urllib_transport(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (constant or test URL)
        final = response.geturl() if hasattr(response, "geturl") else url
        if url.startswith("https://") and not final.startswith("https://"):
            raise ValueError("catalog redirected away from https")
        body = response.read(MAX_BYTES + 1)
    if len(body) > MAX_BYTES:
        raise ValueError("catalog response too large")
    return body


def fetch_with_deadline(transport, url: str, deadline: float) -> bytes:
    """Run the fetch in a daemon thread so a hung DNS lookup cannot outlive the deadline."""
    result: dict = {}

    def target():
        try:
            result["body"] = transport(url, deadline)
        except BaseException as error:  # reported to the caller below
            result["error"] = error

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    worker.join(deadline)
    if worker.is_alive():
        raise TimeoutError("catalog fetch exceeded the deadline")
    if "error" in result:
        raise result["error"]
    return result["body"]


def parse_catalog(body: bytes) -> dict:
    """Map of skill id to version. Only well-formed semver strings are kept."""
    data = json.loads(body.decode("utf-8"))
    skills = data.get("skills") if isinstance(data, dict) else None
    if not isinstance(skills, list):
        raise ValueError("catalog has no skills list")
    versions: dict = {}
    for entry in skills:
        if not isinstance(entry, dict):
            continue
        skill_id, version = entry.get("id"), entry.get("version")
        if isinstance(skill_id, str) and parse_semver(version):
            versions[skill_id] = version
    return versions


def cache_file(env) -> Path | None:
    base = env.get("XDG_CACHE_HOME")
    if base:
        root = Path(base)
    else:
        home = _home(env)
        if home is None:
            return None
        root = home / ".cache"
    return root / "next-skills" / "catalog.json"


def load_cache(path: Path | None, now: float) -> dict | None:
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    fetched_at, ok, versions = data.get("fetched_at"), data.get("ok"), data.get("versions")
    if not isinstance(fetched_at, (int, float)) or not isinstance(ok, bool) or not isinstance(versions, dict):
        return None
    age = now - fetched_at
    if age < -300 or age > (SUCCESS_TTL if ok else FAILURE_TTL):
        return None
    clean = {k: v for k, v in versions.items() if isinstance(k, str) and parse_semver(v)}
    return {"ok": ok, "versions": clean}


def save_cache(path: Path | None, now: float, ok: bool, versions: dict) -> None:
    if path is None:
        return
    tmp = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"fetched_at": now, "ok": ok, "versions": versions}, handle, indent=2)
            handle.write("\n")
        os.replace(tmp, path)
        tmp = None
    except OSError:
        pass  # an unwritable cache only costs a fetch next time
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def latest_versions(env, now: float, transport, no_cache: bool, deadline: float):
    """(versions or None, source label). Never raises."""
    override = env.get(OVERRIDE_ENV)
    url = override or CATALOG_URL
    label = f"in {override}" if override else "on main"
    path = None if override else cache_file(env)
    if not no_cache:
        cached = load_cache(path, now)
        if cached is not None:
            return (cached["versions"] if cached["ok"] else None), label
    try:
        if not override and not url.startswith("https://"):
            raise ValueError("catalog URL must be https")
        versions = parse_catalog(fetch_with_deadline(transport, url, deadline))
    except Exception:
        save_cache(path, now, False, {})
        return None, label
    save_cache(path, now, True, versions)
    return versions, label


# ---------------------------------------------------------------- install method

def read_git_head(checkout: Path) -> tuple:
    """("branch", name), ("detached", short id) or ("unknown", None). Reads files only."""
    try:
        git = checkout / ".git"
        if git.is_dir():
            head = git / "HEAD"
        elif git.is_file():
            first = git.read_text(encoding="utf-8").splitlines()[0].strip()
            if not first.startswith("gitdir:"):
                return ("unknown", None)
            gitdir = Path(first[len("gitdir:"):].strip())
            if not gitdir.is_absolute():
                gitdir = checkout / gitdir
            head = gitdir / "HEAD"
        else:
            return ("unknown", None)
        text = head.read_text(encoding="utf-8").strip()
    except (OSError, IndexError, UnicodeDecodeError):
        return ("unknown", None)
    if text.startswith("ref: refs/heads/"):
        return ("branch", text[len("ref: refs/heads/"):])
    if re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", text):
        return ("detached", text[:12])
    return ("unknown", None)


def _home(env) -> Path | None:
    try:
        return Path(env["HOME"]) if env.get("HOME") else Path.home()
    except (RuntimeError, OSError):
        return None


def _same(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def detect_install(skill_dir: Path, skill_id: str, env) -> dict:
    """How the copy that is running was installed, judged from its own directory."""
    parent = skill_dir.parent
    home = _home(env)

    # 1. npx skills global store with a lock entry for this skill from this repo.
    state = env.get("XDG_STATE_HOME")
    lock_path = (Path(state) / "skills" / ".skill-lock.json") if state else (home / ".agents" / ".skill-lock.json" if home else None)
    stores = [p for p in (home / ".agents" / "skills" if home else None,
                          lock_path.parent / "skills" if lock_path else None) if p is not None]
    if lock_path is not None and any(_same(parent, store) for store in stores):
        try:
            lock = json.loads(lock_path.read_text(encoding="utf-8"))
            entry = lock.get("skills", {}).get(skill_id) if isinstance(lock, dict) else None
        except (OSError, ValueError, AttributeError, UnicodeDecodeError):
            entry = None
        if isinstance(entry, dict) and str(entry.get("source", "")).lower() == REPO.lower():
            ref = entry.get("ref")
            return {"method": "npx", "ref": ref if isinstance(ref, str) and ref and ref != "main" else None}

    # 2. A checkout of the skills repository.
    if (parent / "skills.json").is_file() and (parent / "skills.sh").is_file():
        kind, value = read_git_head(parent)
        return {"method": "checkout", "checkout": str(parent), "head": kind, "head_value": value}

    # 3. A copy in one of the installer's named targets.
    if home is not None:
        for target, directory in (("claude", home / ".claude" / "skills"),
                                  ("codex", home / ".codex" / "skills"),
                                  ("agents", home / ".agents" / "skills")):
            if _same(parent, directory):
                return {"method": "target", "target": target}

    # 4. Anything else, including ./skills.sh install --target <dir>.
    return {"method": "unknown", "target_dir": str(parent)}


# ---------------------------------------------------------------- notice

def update_lines(skill_id: str, installed: str, latest: str, label: str, install: dict) -> list:
    lines = [f"Update available: {installed} installed, {latest} {label}."]
    notes = f"  Notes:   {NOTES_URL.format(skill=skill_id)}"
    replaces = "  Updating replaces this skill folder (an older copy is replaced even if you edited it)."
    copy_out = "  If you edited any file in it, copy your changes out first."
    trailer = f"  Then restart your agent session. You can keep going on {installed}; nothing here is required."
    method = install["method"]

    if method == "npx":
        if install.get("ref"):
            lines += [f"  This copy was installed with npx skills pinned to {install['ref']}; npx skills update follows that pin.",
                      f"  To move it to main: npx skills add {REPO} -g --skill {skill_id}"]
        else:
            lines.append(f"  Update:  npx skills update -g {skill_id}")
        lines += [notes, replaces, copy_out, trailer]
    elif method == "checkout":
        checkout = install["checkout"]
        q = shlex.quote(checkout)
        pull = f"git -C {q} pull --ff-only && bash {shlex.quote(checkout + '/skills.sh')} status"
        if install["head"] == "branch" and install["head_value"] != "main":
            lines += [f"  This copy is the checkout at {checkout}, on branch {install['head_value']}. Latest is {latest} {label}.",
                      "  Switch this checkout to main, or update from a checkout that is on main:",
                      "           git -C '<checkout on main>' pull --ff-only && bash '<checkout on main>/skills.sh' status",
                      "  If the branch is intentional, ignore this."]
        elif install["head"] == "detached":
            lines += [f"  This copy is the checkout at {checkout}, detached at {install['head_value']}. Latest is {latest} {label}.",
                      "  Check out main there, or update from a checkout that is on main. If the pin is intentional, ignore this."]
        else:
            where = f"the checkout at {checkout}"
            if install["head"] == "unknown":
                where += " (branch unknown)"
            lines += [f"  This copy is {where}.", f"  Update:  {pull}"]
        lines += [notes, trailer]
    elif method == "target":
        lines += [f"  Update:  cd <your checkout of {REPO}> && git pull --ff-only && ./skills.sh install {install['target']} {skill_id}",
                  notes, replaces, copy_out, trailer]
    else:
        target_dir = shlex.quote(install["target_dir"])
        lines += ["  How this copy was installed could not be identified. To update this copy:",
                  f"           cd <your checkout of {REPO}> && git pull --ff-only && ./skills.sh install --target {target_dir} {skill_id}",
                  f"  See {UPDATING_DOCS}",
                  notes, replaces, copy_out, trailer]
    return lines


def check(skill_dir: Path, env, *, now: float | None = None, transport=urllib_transport,
          no_cache: bool = False, deadline: float = FETCH_DEADLINE) -> dict:
    skill_dir = Path(skill_dir).resolve()
    fields = read_frontmatter(skill_dir / "SKILL.md")
    skill_id = fields.get("name") or skill_dir.name
    installed = fields.get("version")
    result = {"skill": skill_id, "installed": installed, "latest": None, "status": None, "lines": []}

    if env.get(DISABLE_ENV, "").strip() not in ("", "0", "false", "no"):
        result["status"] = "disabled"
        result["lines"] = [f"Update check disabled ({DISABLE_ENV} is set)."]
        return result
    if not parse_semver(installed):
        result["status"] = "unknown-version"
        result["lines"] = ["Installed version unknown: SKILL.md has no valid version line.",
                           f"  Compare with the {skill_id} entry in {CATALOG_PAGE}"]
        return result

    versions, label = latest_versions(env, time.time() if now is None else now, transport, no_cache, deadline)
    latest = versions.get(skill_id) if versions else None
    result["latest"] = latest
    if not latest:
        result["status"] = "could-not-check"
        result["lines"] = [f"Could not check for updates (offline, GitHub unreachable, or {skill_id} not in the catalog).",
                           f"  Latest is listed at {CATALOG_PAGE}"]
        return result

    order = (parse_semver(installed) > parse_semver(latest)) - (parse_semver(installed) < parse_semver(latest))
    if order == 0:
        result["status"] = "up-to-date"
        result["lines"] = [f"Up to date ({latest} {label})."]
    elif order > 0:
        result["status"] = "local-newer"
        result["lines"] = [f"Installed {installed} is newer than {latest} {label}. Expected on a branch or an unreleased copy."]
    else:
        install = detect_install(skill_dir, skill_id, env)
        result["status"] = "update-available"
        result["install"] = install
        result["lines"] = update_lines(skill_id, installed, latest, label, install)
    return result


def main(argv=None, env=None, transport=urllib_transport) -> int:
    env = os.environ if env is None else env
    try:
        parser = argparse.ArgumentParser(description="Check whether this skill has a newer published version.")
        parser.add_argument("--no-cache", action="store_true", help="fetch the catalog even if the cache is fresh")
        parser.add_argument("--json", action="store_true", help="print the result as JSON")
        parser.add_argument("--skill-dir", type=Path, default=Path(__file__).resolve().parents[1],
                            help=argparse.SUPPRESS)
        args = parser.parse_args(argv)
        result = check(args.skill_dir, env, transport=transport, no_cache=args.no_cache)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print("\n".join(result["lines"]))
    except SystemExit:  # --help, or a usage error argparse already printed
        return 0
    except Exception:
        print(f"Could not check for updates.\n  Latest is listed at {CATALOG_PAGE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
