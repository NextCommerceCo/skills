# Next Commerce AI Skills

Pre-built skills that give AI coding agents deep knowledge of the Next Commerce
platform — APIs, CLI workflows, architecture patterns, and gotchas — so they can
work autonomously on your store.

**Skills are structured markdown files.** Any AI tool that accepts a context
file or system prompt can use them. They work with Claude Code, OpenAI Codex,
Cursor, GitHub Copilot, and any other agent that reads markdown.

## Skills

<!-- BEGIN GENERATED SKILLS TABLE -->
| Skill | Domain | What It Does |
|-------|--------|--------------|
| [**Theme Figma Handoff**](next-theme-figma/) | Storefronts | Turn a Figma storefront design into a validated, low-inference handoff package for next-theme-dev: sections, assets, geometry, copy, and design tokens. |
| [**Theme Development**](next-theme-dev/) | Storefronts | Build, modify, and debug Next Commerce storefront themes: Spark, Intro Bootstrap, Theme Settings, ntk CLI, DTL templates, and storefront GraphQL. |
| [**Bulk Fulfillment Tracking Sync**](next-bulk-fulfill/) | Operations | Mark orders Fulfilled with tracking numbers from a CSV when a fulfillment provider's sync-back fails. |
| [**Bulk Fulfillment Order Move**](next-bulk-move/) | Operations | Move fulfillment orders between warehouse locations in bulk, from a file of order numbers or a product/SKU list. |
| [**Bulk Subscription Actions**](next-bulk-subscription/) | Operations | Pause, cancel, or update subscriptions in bulk from a CSV/XLSX of subscription IDs, with dry-run and verification. |
| [**Daily Ops Risk Scan**](next-ops-scan/) | Operations | Read-only daily risk scan for one store: Incomplete and Rejected orders, delivery-tracking failures, and stale shipments. |
| [**New Campaign Setup**](next-campaigns-setup/) | Campaigns | Scaffold and configure a new campaign-page-kit campaign end to end: project, starter template, config, and analytics. |
| [**Campaign Provisioning**](next-create-campaign/) | Campaigns | Create a launch-ready Campaigns App campaign over the Admin API: packages, shipping, and tier and voucher offers, behind a plan-hash approval gate. |
<!-- END GENERATED SKILLS TABLE -->

Each skill directory holds a `README.md` for the person running it (what it
does, what you need, how to ask for it) and a `SKILL.md` with the technical
instructions the agent follows. For design-led theme work, run
`next-theme-figma` before `next-theme-dev`.

## Quick Start

Clone the repo and run the guided installer. It previews changes before
writing and installs into the skill directory of the agent you pick:

```bash
git clone https://github.com/NextCommerceCo/skills.git
cd skills
./skills.sh
```

Then restart your agent session so the new instructions are loaded, and ask
for the work in plain language. For example:

> Run the daily ops risk scan for my store and give me the CSV.

Two alternatives:

- **No checkout.** The [`skills` CLI](https://github.com/vercel-labs/skills)
  pulls `SKILL.md` files straight from GitHub:
  `npx skills add NextCommerceCo/skills -g` installs everything for your
  detected agent; add `--skill next-theme-dev` for one skill or `-a codex` to
  target an agent. `npx skills update` refreshes them later.
- **Ask your AI tool.** Paste this into the agent:

  > Install the Next Commerce AI skill I need from
  > https://github.com/NextCommerceCo/skills. Prefer cloning the repo and
  > running `./skills.sh`, choosing the installation location for my current
  > AI tool. If a local checkout is not appropriate, use the public
  > `npx skills` installer or load the relevant `SKILL.md` as context.

If your tool has no native skill directory, load the relevant `SKILL.md` as a
system prompt, context file, rule, or chat upload.

## Installer Reference

`./skills.sh` reads [`skills.json`](skills.json) with Python 3 and manages
these targets:

| Target | Directory |
|--------|-----------|
| `claude` | `~/.claude/skills` |
| `codex` | `~/.codex/skills` |
| `agents` | `~/.agents/skills` |
| `all` | all of the above |

```bash
./skills.sh status                              # compare source and installed versions
./skills.sh install codex                       # install or upgrade every skill for Codex
./skills.sh install codex next-ops-scan         # one skill
./skills.sh status --target /tmp/next-skills    # any directory
./skills.sh install --force --target /tmp/next-skills next-ops-scan
```

`status` is read-only. A `stale` row means the installed version is older;
`modified` means the versions match but files differ; `local-newer` means the
installed copy has a later version; `unknown-version` means one side does not
use `X.Y.Z`. `install` upgrades missing or stale copies and refuses to
overwrite `modified`, `local-newer`, or `unknown-version` copies unless you
pass `--force`. Review those directories first. `dry-run` remains as a
deprecated alias for `status`.

First installs are staged and moved into place. Updates use `rsync --delete`,
so a forced update removes files that are not in the source package; install
`rsync` on minimal environments that lack it. Pull with `git pull --ff-only`
before running `status` or `install` from a checkout.

## Updating

An installed skill is a copy. It does not change when this repository does,
and nothing notifies you when a new version ships, so check before starting
work that depends on a skill.

- **See what you have.** A skill's version is the `version:` line in its
  installed `SKILL.md`. From a checkout, `./skills.sh status` compares each
  installed copy with the source and marks older copies `stale`. A skill that
  ships a launcher prints it too, for example
  `bash ~/.claude/skills/next-create-campaign/next-create-campaign.sh --version`.
- **Check for a newer one.** From a checkout, run `git pull --ff-only` and then
  `./skills.sh status`. Without a checkout, compare your version with that
  skill's `version` in
  [`skills.json` on `main`](https://github.com/NextCommerceCo/skills/blob/main/skills.json).
- **Update.** From a checkout, `./skills.sh install <target> <skill>` replaces
  a `stale` copy; the Installer Reference above covers `modified` rows and
  `--force`. With the `skills` CLI, run `npx skills update`. Restart the agent
  session afterwards.
- **Read what changed.** Each version bump ships in a pull request titled
  `<skill> X.Y.Z: summary`, and its description is the release note, breaking
  changes included. Search the
  [merged pull requests](https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged)
  for the skill's name.

The repository has no git tags, GitHub Releases or changelog files yet.

## Machine-Readable Index

[`skills.json`](skills.json) is the canonical catalog. It drives installer
enumeration, the generated table above, and CI package-parity checks, and it
lists each skill's runtime, access, and tool requirements. An agent can fetch
this one file to decide which skill to load.

## Contributing

Adding or changing a skill, the versioning rules CI enforces, the public-safety
scanner, and issue tracking are covered in [CONTRIBUTING.md](CONTRIBUTING.md).
Release notes for each version bump live in the merged pull request that
carried it.
