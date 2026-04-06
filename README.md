# NEXT — Claude Code Skills

Claude Code skills for the NEXT platform. Each skill encodes deep domain knowledge — platform architecture, CLI workflows, gotchas, and best practices — so Claude can work autonomously across Next Commerce storefronts, campaigns, automations, and merchant tooling.

## Available Skills

| Skill | Domain | Description |
|-------|--------|-------------|
| [`next-theme-dev`](next-theme-dev/) | Storefronts | Theme development — build, modify, and debug storefront themes using DTL, ntk CLI, and the NEXT platform |
| [`next-bulk-fulfill`](next-bulk-fulfill/) | Operations | Bulk fulfillment tracking sync — update orders to Fulfilled from a CSV when the fulfillment provider's automation fails |

## Install

### Option 1: Global install (recommended)

Clone once, symlink the skills you want:

```bash
git clone https://github.com/NextCommerceCo/skills.git ~/next-commerce-skills

# Install all skills
for skill in ~/next-commerce-skills/*/; do
  name=$(basename "$skill")
  [ -f "$skill/SKILL.md" ] && ln -sf "$skill" ~/.claude/skills/"$name"
done

# Or install a single skill
ln -sf ~/next-commerce-skills/next-theme-dev ~/.claude/skills/next-theme-dev
```

### Option 2: Project-local install

Copy a skill into your project repo so it's available to anyone who clones it:

```bash
mkdir -p .claude/skills/next-theme-dev
cp ~/next-commerce-skills/next-theme-dev/SKILL.md .claude/skills/next-theme-dev/
```

## Update

```bash
cd ~/next-commerce-skills && git pull
```

Symlinked skills pick up changes automatically.

## Prerequisites

Each skill lists its own prerequisites. Common requirements across skills:

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- Access to a NEXT store with an API key

## Contributing

Each skill is a directory containing a single `SKILL.md` file. To add a new skill:

1. Create a directory with a descriptive name (e.g., `my-skill/`)
2. Add a `SKILL.md` with frontmatter (`name`, `version`, `description`, `allowed-tools`)
3. Update this README's skill index
4. Open a PR
