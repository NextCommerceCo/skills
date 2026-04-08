---
name: next-cpk-new-campaign
version: 1.0.0
description: |
  Scaffold a new campaign-page-kit (CPK) campaign — initializes the CPK project
  for a brand if needed, copies a starter template, seeds campaigns.json with the
  correct entry from upstream, and downloads the CLAUDE.md context file.

  Use when: "new CPK campaign", "scaffold a campaign", "new campaign-kit project",
  "new funnel", or when a user provides a brand name + campaign slug + template choice.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - WebFetch
  - AskUserQuestion
---

# /next-cpk-new-campaign: Scaffold a New CPK Campaign

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-cpk-new-campaign/` (see repo README). Invoke with `/next-cpk-new-campaign`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-cpk-new-campaign/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. The instructions are tool-agnostic markdown. |

---

Scaffold a new campaign-page-kit campaign inside a CPK project root.

## Gather Inputs

### Step 0: Resolve CPK Root

Check for a `CPK_ROOT` environment variable:
```bash
echo "${CPK_ROOT:-not set}"
```

If not set, ask the user:
> What is the path to your `sellmore.campaigns.cpk` folder?
> (e.g. `/Users/you/projects/sellmore.campaigns.cpk`)

Use this value as `<CPK_ROOT>` for all paths below. Do not hardcode any absolute path.

### Step 1: Gather Campaign Details

If not provided as arguments, ask for all three in a single message — do not ask one at a time:

1. **Brand name** — the client/brand slug. Lowercase, hyphens only. Becomes the brand folder.
2. **Campaign slug** — the product name or product name + version (e.g. `grounding-mat`, `grounding-mat-v2`). Lowercase, hyphens only. Becomes `src/[slug]/` and drives the URL.
3. **Starter template** — which template to base this on:
   - `demeter` — standard single-step checkout
   - `limos` — single-step checkout (alternate layout)
   - `olympus` — single-step checkout (premium layout)
   - `olympus-mv-single-step` — single-step with variant/SKU selection
   - `olympus-mv-two-step` — two-step flow: variant picker → checkout
   - `shop-single-step` — shop-style single-step checkout
   - `shop-three-step` — multi-step checkout (information → shipping → billing)

---

## Paths

- **Brand folder:** `<CPK_ROOT>/[brand-name]/`
- **Campaign folder:** `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/`
- **Starter templates repo:** `https://github.com/NextCommerceCo/campaign-cart-starter-templates` (subfolder: `campaign-kit-templates/src/[template-slug]`)

---

## Steps

### 1 — Safety Check

If `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/` already exists → **stop and warn the user**. Do not overwrite.

### 2 — Create Brand Folder (if needed)

If `<CPK_ROOT>/[brand-name]/` does not exist, create it.

### 3 — Initialize CPK Project (if needed)

Check if `<CPK_ROOT>/[brand-name]/package.json` exists.

If **not**, run these commands sequentially inside the brand folder:

```bash
cd <CPK_ROOT>/[brand-name]
npm init -y
npm install next-campaign-page-kit
npx campaign-init
```

`npx campaign-init` creates `_data/campaigns.json` and adds npm scripts to `package.json`. It does **not** create any `src/` folders.

If `package.json` **already exists**, skip — the project is already initialized.

### 4 — Copy Starter Template

Run from the brand folder:

```bash
cd <CPK_ROOT>/[brand-name]
npx degit NextCommerceCo/campaign-cart-starter-templates/campaign-kit-templates/src/[template-slug] src/[campaign-slug]
```

The destination folder name is the **campaign slug**, not the template name.

### 5 — Fetch the Template's campaigns.json Entry

Fetch the upstream campaigns.json to get the canonical entry for the chosen template:

```
https://raw.githubusercontent.com/NextCommerceCo/campaign-cart-starter-templates/HEAD/campaign-kit-templates/_data/campaigns.json
```

Find the entry matching `[template-slug]`. Use its `sdk_version`, `description`, and field structure as the base. Then customise:

- Key: change from `[template-slug]` to `[campaign-slug]`
- `name`: title-case derived from the campaign slug (hyphens → spaces)
- `store_name`: title-case derived from the brand slug
- `store_url`, `store_terms`, `store_privacy`, `store_contact`, `store_returns`, `store_shipping`: set to `""`
- `store_phone`, `store_phone_tel`: set to `""`
- `gtm_id`, `fb_pixel_id`: keep as placeholder values from the template entry (e.g. `"GTM-XXXXXXX"`, `"123456789012345"`)
- `sdk_version`: keep exactly as it appears in the upstream entry — do not hardcode

Merge this entry into `<CPK_ROOT>/[brand-name]/_data/campaigns.json` — do not replace the whole file.

### 6 — Copy CLAUDE.md

Download and save the AI context file into the brand project root as `CLAUDE.md`:

```bash
curl -sL "https://raw.githubusercontent.com/NextCommerceCo/campaign-cart-starter-templates/HEAD/docs/campaign-page-kit-template-context.md" \
  -o <CPK_ROOT>/[brand-name]/CLAUDE.md
```

If `CLAUDE.md` already exists in the brand folder, skip — do not overwrite.

---

## Report Back

Summarise what was done:
- Brand folder created or existing
- CPK project initialized or already present
- Template copied from `[template-slug]` → `src/[campaign-slug]/`
- campaigns.json updated (with sdk_version from upstream)
- CLAUDE.md copied or already present

Then show the user their next steps:

```
Next steps:
1. /next-cpk-config-setup [brand-name] [campaign-slug]   ← when you have your API key + store details ready
2. npm run dev   → pick [campaign-slug] from the list and start the dev server
```

Dev server preview URLs for the chosen template:

| Template | Pages |
|----------|-------|
| demeter | /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| limos | /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| olympus | /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| olympus-mv-single-step | /[slug]/checkout/ · /[slug]/upsell-mv/ · /[slug]/receipt/ |
| olympus-mv-two-step | /[slug]/select/ · /[slug]/checkout/ · /[slug]/upsell-mv/ · /[slug]/receipt/ |
| shop-single-step | /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| shop-three-step | /[slug]/information/ · /[slug]/shipping/ · /[slug]/billing/ · /[slug]/upsell/ · /[slug]/receipt/ |
