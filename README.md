# Next Commerce AI Skills

Pre-built skills that give AI coding agents deep knowledge of the Next Commerce platform — APIs, CLI workflows, architecture patterns, and gotchas — so they can work autonomously on your store.

**Skills are structured markdown files.** Any AI tool that accepts a context file or system prompt can use them. They work with Claude Code, OpenAI Codex, Cursor, GitHub Copilot, and any other agent that reads markdown.

## Skills

| Skill | What It Does | When to Use |
|-------|-------------|-------------|
| [**Theme Figma Handoff**](next-theme-figma/) | Prepare Figma storefront designs for implementation - source validation, section classification, asset manifests, Spark divergence ledger, and visual refs | You have a Figma storefront/PDP/homepage design and need a low-inference handoff before `next-theme-dev` implements it |
| [**Theme Development**](next-theme-dev/) | Build and customize storefront themes — DTL templates, ntk CLI, Tailwind CSS, settings, side cart | You're editing theme files, setting up a new storefront, or debugging template issues |
| [**Bulk Fulfillment Sync**](next-bulk-fulfill/) | Update orders to Fulfilled with tracking numbers from a CSV | Your fulfillment provider shipped orders but tracking didn't sync back — orders stuck in Processing |
| [**Bulk Fulfillment Move**](next-bulk-move/) | Move fulfillment orders between warehouse locations in bulk — by order-number file or by Product ID / SKU list | Switching fulfillment providers, or moving every FO containing a given SKU/product to a new location |
| [**Bulk Subscription Actions**](next-bulk-subscription/) | Pause, cancel, or PATCH subscription fields for a list of subscription IDs | Merchant wants to bulk-pause until a date, bulk-shift renewals, bulk-cancel, or migrate subscriptions between gateways |
| [**Daily Ops Risk Scan**](next-ops-scan/) | Read-only scan for Incomplete orders, Rejected orders, and Delivery Tracking failures/staleness | You want a daily queue of risky orders and stuck shipments to reduce support friction and dispute risk |
| [**New Campaign Setup**](next-campaigns-setup/) | Scaffold and fully configure a new Next Commerce campaign repo — brand init, starter template, campaigns.json seed, API key, store details, and analytics in one pass | Starting a new Next Commerce campaign for a brand |

### Theme Figma Handoff And Theme Development

Use [`next-theme-figma`](next-theme-figma/) upstream of
[`next-theme-dev`](next-theme-dev/) when a storefront implementation starts from
Figma. `next-theme-figma` validates the design source, extracts/classifies
assets, records Spark/platform divergences, captures desktop/tablet/mobile
references, and writes an implementation handoff package. `next-theme-dev` then
consumes that package for the actual DTL, Spark, CSS, ntk, and storefront QA
work.

Install just the Figma handoff skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-theme-figma
```

Then ask your AI tool:

> Use `/next-theme-figma` to prepare this Spark storefront Figma design for a
> `next-theme-dev` implementation handoff.

### Daily Ops Risk Scan

Use [`next-ops-scan`](next-ops-scan/) when a merchant or agency wants a daily,
read-only queue of operational issues that can turn into customer friction,
refund misses, or disputes. It checks one store for:

- Incomplete orders that likely need refund review in NEXT.
- Rejected orders that need Shop Sync / order-data correction review.
- Delivery Tracking failures or stale shipments when Delivery Tracking is installed.

Install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-ops-scan
```

Then ask your AI tool:

> Run `/next-ops-scan` for my store and help me review today's risky order queues.

The skill asks you to bring your own limited-scope Admin API token from
**Dashboard > Settings > API Access**. Keep API tokens private: do not commit
them, paste them into shared docs, or include them in screenshots. Rotate any
token that is exposed. The scanner accepts `NEXT_STORE_DOMAIN` and
`NEXT_ADMIN_API_TOKEN` environment variables. The scan is read-only: it produces
a Markdown summary and CSV, then points your team to the right manual
remediation flow instead of refunding, canceling, fulfilling, editing, or
messaging customers.

### Campaigns OS Skill Boundary

This repo hosts `next-campaigns-setup`, which covers Next Commerce campaign repo bootstrap and first configuration.

The canonical Campaigns OS lifecycle skills — build, polish, and QA — ship with the public [`campaigns-os`](https://github.com/NextCommerceCo/campaigns-os) package itself and install via `campaigns-os install-skills`. They're version-locked to the package's CLI and contract versions, which is why they live with the package rather than in this catalog.

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
./skills.sh status
./skills.sh install codex
./skills.sh install codex next-ops-scan
./skills.sh status --target /tmp/next-skills next-ops-scan
./skills.sh dry-run --target /tmp/next-skills next-ops-scan
```

Targets:

- `claude` -> `~/.claude/skills`
- `codex` -> `~/.codex/skills`
- `agents` -> `~/.agents/skills`
- `all` -> all of the above

Restart local agent sessions after updating skills so the refreshed instructions
are loaded.

Updating an existing skill directory uses `rsync` so the destination path remains
present while files are refreshed. Install `rsync` before using `skills.sh` on
minimal environments that do not include it by default.

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

For AI agents that need to programmatically discover available skills, [`skills.json`](skills.json) provides a structured manifest with skill IDs, descriptions, trigger phrases, and prerequisites. Agents can fetch this single file to decide which skill to load.

## Prerequisites

Each skill lists its own requirements in the file. Common across all skills:

- Access to a Next Commerce store
- An API key with the scopes specified by the skill (create at **Dashboard > Settings > API Access**)

## Contributing

Each skill is a directory containing a single `SKILL.md` file. To add a new skill:

1. Create a directory with a descriptive name (e.g., `next-my-skill/`)
2. Add a `SKILL.md` with YAML frontmatter (`name`, `version`, `description`, `allowed-tools`) followed by the skill instructions in markdown
3. Add a "Using This Skill" section that points to the repo install guidance
4. Add an entry to `skills.json`
5. Update this README's skills table and any installer notes that should mention the skill
6. Run `./skills.sh status` to confirm the local installer can discover the skill
7. Open a PR
