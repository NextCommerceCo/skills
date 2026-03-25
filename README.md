# NEXT Commerce — Claude Code Skills

Claude Code skills for building, customizing, and deploying NEXT Commerce storefronts.

## Available Skills

| Skill | Description |
|-------|-------------|
| [`next-theme-dev`](next-theme-dev/) | Theme development — build, modify, and debug storefront themes using DTL, ntk CLI, and the NEXT Commerce platform |

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

Copy a skill into your theme repo so it's available to anyone who clones it:

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

Skills in this repo assume you have:

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) installed
- [next-theme-kit](https://pypi.org/project/29next-theme-kit/) (`pip install next-theme-kit`)
- Python 3
- Access to a NEXT Commerce store with an API key

## Contributing

Each skill is a directory containing a single `SKILL.md` file. To add a new skill:

1. Create a directory with a descriptive name (e.g., `my-skill/`)
2. Add a `SKILL.md` with frontmatter (`name`, `version`, `description`, `allowed-tools`)
3. Update this README's skill index
4. Open a PR
