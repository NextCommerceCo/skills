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
| [**New Campaign Setup**](next-campaigns-setup/) | Scaffold and fully configure a new CPK campaign — brand init, starter template, campaigns.json seed, API key, store details, and analytics in one pass | Starting a new CPK campaign for a brand |

### Campaigns OS Skill Boundary

This repo is the public Next Commerce skill catalog. It hosts `next-campaigns-setup`, which covers CPK repo bootstrap and first configuration.

The canonical Campaigns OS lifecycle/build/QA skills live with the public [`campaigns-os`](https://github.com/NextCommerceCo/campaigns-os) package. Internal Sellmore orchestration addenda live in `next-campaigns-ops` and should wrap the public Campaigns OS contract instead of redefining CampaignSpec, Build Packet, doctor, Assembly Report, or QA semantics.

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
5. Update this README's skills table
6. Open a PR
