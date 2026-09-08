# Contributing

This file covers adding or changing a skill, the versioning rules CI enforces,
and how work is tracked. If you only want to install and use skills, the
[README](README.md) is the right document.

## Adding a skill

Each skill is a directory containing a `SKILL.md` (the agent's technical
instructions) and a `README.md` (a plain-language guide for the person running
the skill). To add a new skill:

1. Create a directory with a descriptive name (e.g., `next-my-skill/`).
2. Add a `SKILL.md` with YAML frontmatter (`name`, `version`, `description`,
   `allowed-tools`) followed by the skill instructions in markdown.
3. Add a "Using This Skill" section that points to the repo install guidance.
4. Add a `README.md` written for non-technical readers: what the skill does,
   what the person needs, and how to ask for it. Plain language, no code
   examples; use tables for data examples and `[!IMPORTANT]` callouts for
   things that matter.
5. Add an entry to `skills.json`; this is the canonical catalog used by the
   installer, and its `description` is what the root README's skills table
   shows, so keep it to one clause.
6. Run `python3 scripts/skill_catalog.py readme --write` to regenerate the
   root skills table.
7. Run `./skills.sh list` to confirm the installer sees the catalog entry.
8. Bump the skill version in both `SKILL.md` and `skills.json`.
9. Run `python3 scripts/check_skill_versions.py --base origin/main`.
10. Open a PR.

CI auto-discovers `tests/` directories and `.js` files, so adding a new skill
does not require editing the CI workflow.

## Public-safety scanner

This repository is public. CI runs `scripts/check_public_safety.py` over every
tracked file and fails on private repository references, customer evidence,
credentials, high-entropy strings, and personal data. It reads tracked files
only, so stage or intent-add (`git add -N`) new files before running it
locally. Prefer fixing the line over adding a suppression.

## Skill versioning

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
rejects missing or non-increasing version bumps. Release notes live in the
merged PR that carried the bump; the PR body is the changelog entry.

## Issue tracking

Work in this repo is tracked with GitHub Issues and coordinated on the
org-level **[Operations](https://github.com/orgs/NextCommerceCo/projects/10)** <!-- public-safety: allow private-repo high-entropy: intentional org project link -->
Kanban board (Todo / In Progress / Done). New issues are added to the board
automatically by the `add-to-project` workflow.

Before starting work on an issue: check it is not assigned to someone else,
assign yourself (`gh issue edit <n> --add-assignee @me`), and move the card to
In Progress. Open PRs with `Closes #<n>`; when the issue closes on merge, the
board's built-in "Item closed" automation moves the card to Done. Contributors
have a `/next-board` skill that wraps these board operations.
