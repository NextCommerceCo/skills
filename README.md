# Next Commerce AI Skills

Pre-built skills that give AI coding agents deep knowledge of the Next Commerce platform — APIs, CLI workflows, architecture patterns, and gotchas — so they can work autonomously on your store.

**Skills are structured markdown files.** Any AI tool that accepts a context file or system prompt can use them. They work with Claude Code, OpenAI Codex, Cursor, GitHub Copilot, and any other agent that reads markdown.

## Skills

| Skill | What It Does | When to Use |
|-------|-------------|-------------|
| [**Theme Development**](next-theme-dev/) | Build and customize storefront themes — DTL templates, ntk CLI, Tailwind CSS, settings, side cart | You're editing theme files, setting up a new storefront, or debugging template issues |
| [**Bulk Fulfillment Sync**](next-bulk-fulfill/) | Update orders to Fulfilled with tracking numbers from a CSV | Your fulfillment provider shipped orders but tracking didn't sync back — orders stuck in Processing |
| [**Bulk Fulfillment Move**](next-bulk-move/) | Move fulfillment orders between warehouse locations in bulk | Switching fulfillment providers — need to reassign open/processing orders to a new location |
| [**New CPK Campaign**](next-cpk-new-campaign/) | Scaffold a new campaign-page-kit campaign — brand init, starter template, campaigns.json seed | Starting a new CPK campaign for a brand |
| [**CPK Config Setup**](next-cpk-config-setup/) | Wire up `config.js` and `campaigns.json` with API key, store details, and analytics | Configuring a freshly scaffolded campaign or updating an existing one |

## Quick Start

### Claude Code

```bash
# Install all skills (recommended — symlinks stay up to date)
git clone git@github.com:NextCommerceCo/skills.git ~/next-commerce-skills
for skill in ~/next-commerce-skills/*/; do
  name=$(basename "$skill")
  [ -f "$skill/SKILL.md" ] && ln -sf "$skill" ~/.claude/skills/"$name"
done

# Or install a single skill
ln -sf ~/next-commerce-skills/next-theme-dev ~/.claude/skills/next-theme-dev
```

Once installed, Claude Code auto-detects when a skill is relevant, or you can invoke directly with `/<skill-name>` (e.g., `/next-theme-dev`).

### OpenAI Codex

```bash
git clone git@github.com:NextCommerceCo/skills.git ~/next-commerce-skills

# Use a skill as a system prompt
codex --system-prompt ~/next-commerce-skills/next-theme-dev/SKILL.md
```

### Cursor

Copy the skill file into your project's Cursor rules directory:

```bash
mkdir -p .cursor/rules
cp ~/next-commerce-skills/next-theme-dev/SKILL.md .cursor/rules/next-theme-dev.md
```

### GitHub Copilot

Reference the skill in your project's Copilot instructions:

```bash
mkdir -p .github
echo "See next-commerce-skills/next-theme-dev/SKILL.md for theme development patterns." >> .github/copilot-instructions.md
```

### Any Other AI Tool

Each skill is a single `SKILL.md` file — structured markdown with no proprietary format. Load it however your tool accepts context:

- **System prompt** — paste or reference the file
- **Context file** — point your tool at the SKILL.md path
- **Chat upload** — drag the file into your conversation

## Machine-Readable Index

For AI agents that need to programmatically discover available skills, [`skills.json`](skills.json) provides a structured manifest with skill IDs, descriptions, trigger phrases, and prerequisites. Agents can fetch this single file to decide which skill to load.

## Prerequisites

Each skill lists its own requirements in the file. Common across all skills:

- Access to a Next Commerce store
- An API key with the scopes specified by the skill (create at **Dashboard > Settings > API Access**)

## Update

```bash
cd ~/next-commerce-skills && git pull
```

Symlinked installations (Claude Code) pick up changes automatically. For copy-based installations (Cursor, Copilot), re-copy the updated files.

## Contributing

Each skill is a directory containing a single `SKILL.md` file. To add a new skill:

1. Create a directory with a descriptive name (e.g., `next-my-skill/`)
2. Add a `SKILL.md` with YAML frontmatter (`name`, `version`, `description`, `allowed-tools`) followed by the skill instructions in markdown
3. Add a "Using This Skill" section with cross-tool usage instructions (see existing skills for the format)
4. Add an entry to `skills.json`
5. Update this README's skills table
6. Open a PR
