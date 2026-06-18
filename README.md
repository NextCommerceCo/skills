# Next Commerce AI Skills

Pre-built skills that give AI coding agents deep knowledge of the Next Commerce platform — APIs, CLI workflows, architecture patterns, and gotchas — so they can work autonomously on your store.

**Skills are structured markdown files.** Any AI tool that accepts a context file or system prompt can use them. They work with Claude Code, OpenAI Codex, Cursor, GitHub Copilot, and any other agent that reads markdown.

## Skills

| Skill | What It Does | When to Use |
|-------|-------------|-------------|
| [**Theme Development**](next-theme-dev/) | Build and customize storefront themes — DTL templates, ntk CLI, Tailwind CSS, settings, side cart | You're editing theme files, setting up a new storefront, or debugging template issues |
| [**Bulk Fulfillment Sync**](next-bulk-fulfill/) | Update orders to Fulfilled with tracking numbers from a CSV | Your fulfillment provider shipped orders but tracking didn't sync back — orders stuck in Processing |
| [**Bulk Fulfillment Move**](next-bulk-move/) | Move fulfillment orders between warehouse locations in bulk — by order-number file or by Product ID / SKU list | Switching fulfillment providers, or moving every FO containing a given SKU/product to a new location |
| [**Bulk Subscription Actions**](next-bulk-subscription/) | Pause, cancel, or PATCH subscription fields for a list of subscription IDs | Merchant wants to bulk-pause until a date, bulk-shift renewals, bulk-cancel, or migrate subscriptions between gateways |
| [**Daily Ops Risk Scan**](next-ops-scan/) | Read-only scan for Incomplete orders, Rejected orders, and Delivery Tracking failures/staleness | You want a daily queue of risky orders and stuck shipments to reduce support friction and dispute risk |
| [**New Campaign Setup**](next-campaigns-setup/) | Scaffold and fully configure a new Next Commerce campaign repo — brand init, starter template, campaigns.json seed, API key, store details, and analytics in one pass | Starting a new Next Commerce campaign for a brand |

### Daily Ops Risk Scan

Use [`next-ops-scan`](next-ops-scan/) when a merchant or agency wants a daily,
read-only queue of operational issues that can turn into customer friction,
refund misses, or disputes. It checks one store for:

- Incomplete orders that likely need refund review in NEXT.
- Rejected orders that need Shop Sync / order-data correction review.
- Delivery Tracking failures or stale shipments when Delivery Tracking is installed.

Install just this skill:

```bash
npx skills add NextCommerceCo/skills -s next-ops-scan
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

### Ask Your AI Tool to Install

If you use an AI coding tool, the easiest path is to ask it to install the skill for you:

> Install the Next Commerce AI skill I need from https://github.com/NextCommerceCo/skills.
> Use the installation location for my current AI tool and operating system. If my tool
> supports native skills, install each skill as a directory containing its `SKILL.md`.
> If it only supports rules, prompts, or context files, add the relevant `SKILL.md`
> there instead. Prefer HTTPS clone unless my GitHub SSH access is already configured.

Tell it which skill you want, or ask it to inspect [`skills.json`](skills.json) and choose the relevant one.

### Manual Install

We recommend the [`skills` CLI](https://github.com/vercel-labs/skills) — a friendly skill installer utility that works across every major AI coding tool. It pulls `SKILL.md` files from a GitHub repo and drops them into the right config directory for whichever assistant you use, with built-in support for Claude Code, Cursor, Codex, GitHub Copilot, Gemini CLI, Windsurf, and 50+ other LLM-powered agents.

**Install all skills from this repo:**

```bash
npx skills add NextCommerceCo/skills
```

**Install a single skill:**

```bash
npx skills add NextCommerceCo/skills -s next-theme-dev
```

**List available skills without installing:**

```bash
npx skills add NextCommerceCo/skills --list
```

**Target a specific agent** (auto-detected by default):

```bash
npx skills add NextCommerceCo/skills -a claude-code
```

Once installed, Claude Code auto-detects when a skill is relevant, or you can invoke it directly with `/<skill-name>` (e.g. `/next-theme-dev`). If the skills directory did not exist before Claude Code started, restart Claude Code so it can discover the new directory.

**Update all installed skills:**

```bash
npx skills update
```

**Update a single skill:**

```bash
npx skills update next-theme-dev
```

For tools the `skills` CLI doesn't support, each `SKILL.md` is plain markdown — load it as a system prompt, context file, or chat upload.

### Local Guided Installer

From a local checkout, `skills.sh` can preview or sync the bundled skill
directories into common local agent profiles:

```bash
./skills.sh
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
3. Add a "Using This Skill" section with cross-tool usage instructions (see existing skills for the format)
4. Add an entry to `skills.json`
5. Update this README's skills table and any installer notes that should mention the skill
6. Run `./skills.sh status` to confirm the local installer can discover the skill
7. Open a PR
