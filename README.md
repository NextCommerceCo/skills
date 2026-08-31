# Next Commerce AI Skills

Pre-built skills that give AI coding agents deep knowledge of the Next Commerce platform — APIs, CLI workflows, architecture patterns, and gotchas — so they can work autonomously on your store.

**Skills are structured markdown files.** Any AI tool that accepts a context file or system prompt can use them. They work with Claude Code, OpenAI Codex, Cursor, GitHub Copilot, and any other agent that reads markdown.

## Skills

<!-- BEGIN GENERATED SKILLS TABLE -->
| Skill | Domain | What It Does |
|-------|--------|--------------|
| [**Theme Figma Handoff**](next-theme-figma/) | Storefronts | Prepare Figma storefront designs for NEXT Commerce theme implementation by validating source structure, identifying the theme family and runtime contract, classifying sections and assets, recording platform divergences, and generating a low-inference handoff for next-theme-dev. |
| [**Theme Development**](next-theme-dev/) | Storefronts | Build, modify, and debug Next Commerce storefront themes, including Spark, Intro Bootstrap, Theme Settings, ntk CLI, DTL templates, and storefront GraphQL. |
| [**Bulk Fulfillment Tracking Sync**](next-bulk-fulfill/) | Operations | Update orders to Fulfilled status with tracking numbers from a CSV when a fulfillment provider's automation fails to sync back. |
| [**Bulk Fulfillment Order Move**](next-bulk-move/) | Operations | Move fulfillment orders between warehouse locations in bulk — driven either by a flat file of order numbers or by a Product ID / SKU list. Handles cancellation requests for processing FOs, location discovery, and dry-run validation. |
| [**Bulk Subscription Actions**](next-bulk-subscription/) | Operations | Apply official subscription actions (pause, cancel) or PATCH updates (renewal date, interval, gateway, address) to a list of subscription IDs from a CSV/XLSX via the Admin API. Handles baseline selection, dry-run, rate limiting, and verification. |
| [**Daily Ops Risk Scan**](next-ops-scan/) | Operations | Run a read-only daily operations risk scan for one Next Commerce store, finding Incomplete orders, Rejected orders, and Delivery Tracking failures or stale shipments with manual next steps. |
| [**New Campaign Setup**](next-campaigns-setup/) | Campaigns | End-to-end setup for a new CPK campaign — scaffolds the project, copies a starter template, seeds campaigns.json, downloads CLAUDE.md, then wires up config.js and campaigns.json with API key, store details, and analytics in one pass. |
<!-- END GENERATED SKILLS TABLE -->

For design-led theme work, run `next-theme-figma` before `next-theme-dev`.
Both skills expect real screenshot evidence for visual QA, but they reuse tools
already available to the agent or operator and support manual capture. They do
not require a bundled browser automation package or screenshot service. When a
Theme Development task targets an active theme, the skill adds an exact file
manifest, rollback source, upload-count checks, and served-revision
verification.

Each skill directory contains two documents: a `README.md` — a plain-language
guide for the person running the skill (what it does, what you need, how to
ask for it), written for non-technical readers — and a `SKILL.md`, the
technical instructions the AI agent follows. Skill-specific detail lives in
those per-skill docs, not here. Related skills cross-reference each other in
their READMEs (for example, `next-theme-figma` runs upstream of
`next-theme-dev`, and `next-campaigns-setup` documents its boundary with the
[`campaigns-os`](https://github.com/NextCommerceCo/campaigns-os) package).

## Quick Start

### Recommended: Local Guided Installer

For most Next Commerce users, the most reliable path is to clone this repo and
run the bundled installer. It previews changes before writing, supports common
local agent profiles, and does not depend on external installer UX changing over
time.

**Clone and run the guided installer:**

```bash
git clone https://github.com/NextCommerceCo/skills.git
cd skills
./skills.sh
```

**Preview or install directly:**

```bash
git pull --ff-only
./skills.sh status
./skills.sh install codex
./skills.sh install codex next-ops-scan
./skills.sh status --target /tmp/next-skills next-ops-scan
./skills.sh install --force --target /tmp/next-skills next-ops-scan
```

`status` is read-only and prints both the source and installed `SKILL.md`
versions. A `stale` row means the installed semantic version is older;
`modified` means the versions match but files differ; `local-newer` means the
installed copy has a later version; and `unknown-version` means one side does
not use the required `X.Y.Z` format. `install` upgrades missing or stale copies,
but refuses to overwrite `modified`, `local-newer`, or `unknown-version` copies
unless you pass `--force`. Review those directories before forcing an update.
The older `dry-run` command remains as a deprecated alias for `status`.

Targets:

- `claude` -> `~/.claude/skills`
- `codex` -> `~/.codex/skills`
- `agents` -> `~/.agents/skills`
- `all` -> all of the above

Restart local agent sessions after updating skills so the refreshed instructions
are loaded.

First installs are staged and moved into place. Updating an existing skill
directory uses `rsync --delete` so the destination path remains present while
files are refreshed; a forced update therefore removes files that are not in the
source package. Install `rsync` before updating on minimal environments that do
not include it by default.

### Ask Your AI Tool to Install

If you use an AI coding tool, you can also ask it to run the local guided
installer for you:

> Install the Next Commerce AI skill I need from https://github.com/NextCommerceCo/skills.
> Prefer cloning the repo and running `./skills.sh`, choosing the installation
> location for my current AI tool. If a local checkout is not appropriate, use the
> public `npx skills` installer or load the relevant `SKILL.md` as context.

Tell it which skill you want, or ask it to inspect [`skills.json`](skills.json) and choose the relevant one.

### No-Checkout Install

If you want a one-liner without keeping a local checkout, use the
[`skills` CLI](https://github.com/vercel-labs/skills). It can pull `SKILL.md`
files from GitHub and install them into many agent-specific skill directories.

**Install all skills globally for your detected agent:**

```bash
npx skills add NextCommerceCo/skills -g
```

**Install a single skill globally for Codex:**

```bash
npx skills add NextCommerceCo/skills -g -a codex --skill next-theme-dev
```

**List available skills without installing:**

```bash
npx skills add NextCommerceCo/skills --list
```

**Target a specific agent:**

```bash
npx skills add NextCommerceCo/skills -g -a claude-code
```

**Skip prompts for scripted installs:**

```bash
npx skills add NextCommerceCo/skills -g -a codex --skill next-ops-scan -y
```

Use `npx skills update` to refresh skills installed through the `skills` CLI.

### Manual Fallback

Each skill is plain markdown. If your tool does not support native skill
directories or the installers above, load the relevant `SKILL.md` as a system
prompt, context file, rule, or chat upload according to that tool's conventions.

## Machine-Readable Index

For AI agents that need to programmatically discover available skills,
[`skills.json`](skills.json) is the canonical catalog. It drives installer
enumeration, the generated table above, and CI package-parity checks. Agents can
fetch this single file to decide which skill to load.

## Prerequisites

Each skill lists its own runtime, access, and tool requirements in `skills.json`
and its package documentation. Requirements differ: some packages are read-only
markdown, while others include Node.js or Python helpers and need platform API
access.

## Contributing

Each skill is a directory containing a `SKILL.md` (the agent's technical
instructions) and a `README.md` (a plain-language guide for the person running
the skill). To add a new skill:

1. Create a directory with a descriptive name (e.g., `next-my-skill/`)
2. Add a `SKILL.md` with YAML frontmatter (`name`, `version`, `description`, `allowed-tools`) followed by the skill instructions in markdown
3. Add a "Using This Skill" section that points to the repo install guidance
4. Add a `README.md` written for non-technical readers: what the skill does, what the person needs, and how to ask for it — plain language, no code examples (use tables for data examples and `[!IMPORTANT]` callouts for things that matter)
5. Add an entry to `skills.json`; this is the canonical catalog used by the installer
6. Run `python3 scripts/skill_catalog.py readme --write` to regenerate the root skills table
7. Run `./skills.sh list` to confirm the installer sees the catalog entry
8. Bump the skill version in both `SKILL.md` and `skills.json`
9. Run `python3 scripts/check_skill_versions.py --base origin/main`
10. Open a PR

CI auto-discovers `tests/` directories and `.js` files, so adding a new skill does not require editing the CI workflow.

### Skill versioning

Every tracked file inside a skill directory is part of that skill's versioned
package. A PR that changes any file under `next-*/` must advance that skill's
version in both its `SKILL.md` frontmatter and `skills.json` entry.

- **Patch**: fixes or clarifies existing behavior, safety rules, validation, or
  routing without adding a new execution surface.
- **Minor**: adds a new command, automation lane, detector/check family, output
  contract, or approved write surface.
- **Major**: changes ownership, safety posture, default side effects, or removes
  a supported contract.

CI compares each changed skill package with the pull request's base commit and
rejects missing or non-increasing version bumps.
## Issue tracking

Work in this repo is tracked with GitHub Issues and coordinated on the
org-level **[Operations](https://github.com/orgs/NextCommerceCo/projects/10)**. <!-- public-safety: allow private-repo high-entropy: intentional org project link -->
Kanban board (Todo / In Progress / Done). New issues are added to the board
automatically by the `add-to-project` workflow.

Before starting work on an issue: check it is not assigned to someone else,
assign yourself (`gh issue edit <n> --add-assignee @me`), and move the card to
In Progress. Open PRs with `Closes #<n>`; when the issue closes on merge, the
board's built-in "Item closed" automation moves the card to Done. Contributors
have a `/next-board` skill that wraps these board operations.
