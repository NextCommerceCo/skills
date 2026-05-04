---
name: next-campaigns-setup
version: 1.0.0
description: |
  End-to-end setup for a new campaign-page-kit (CPK) campaign — scaffolds the
  project, copies a starter template, seeds campaigns.json, downloads CLAUDE.md,
  then immediately wires up config.js and campaigns.json with API key, store
  details, optional policy links, and declared analytics in one pass.

  Use when: "new CPK campaign", "set up a campaign", "scaffold and configure",
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

# /next-campaigns-setup: Scaffold + Configure a New CPK Campaign

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-campaigns-setup/` (see repo README). Invoke with `/next-campaigns-setup`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-campaigns-setup/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. The instructions are tool-agnostic markdown. |

---

Scaffold and configure a new campaign-page-kit campaign in one pass.

Boundary with other campaign skills:
- Use this skill for repo/project bootstrap: brand folder, page-kit init, starter template copy, `campaigns.json`, `CLAUDE.md`, and first config values.
- Use `next-campaigns-build` when a CampaignSpec/design exists and pages need to be wired end-to-end from spec/API/design into page-kit.
- Use `next-campaigns-os` for CampaignSpec lifecycle work: map inspection, multi-funnel planning, QA results, Linear/run-through orchestration, promotion decisions, and split-test config.
- This skill may consume a CampaignSpec if one is provided, but it should not try to replace the build skill's page wiring or the OS skill's lifecycle decisions.

---

## Phase 1 — Scaffold

### Step 0: Resolve CPK Root

Check for a `CPK_ROOT` environment variable:
```bash
echo "${CPK_ROOT:-not set}"
```

If not set, ask the user:
> What is the path to your CPK project root?
> (e.g. `/Users/you/projects/my-campaigns`)

Use this value as `<CPK_ROOT>` for all paths below. Do not hardcode any absolute path.

### Step 1: Gather Campaign Details

Ask for all three in a single message — do not ask one at a time:

1. **Brand name** — the client/brand slug. Lowercase, hyphens only. Becomes the brand folder.
2. **Campaign slug** — the product name or product name + version (e.g. `grounding-mat`, `grounding-mat-v2`). Lowercase, hyphens only.
3. **Starter template** — which template to base this on:
   - `demeter` — standard single-step checkout
   - `limos` — single-step checkout (alternate layout)
   - `olympus` — single-step checkout (premium layout)
   - `olympus-mv-single-step` — single-step with variant/SKU selection
   - `olympus-mv-two-step` — two-step flow: variant picker → checkout
   - `shop-single-step` — shop-style single-step checkout
   - `shop-three-step` — multi-step checkout (information → shipping → billing)
   - `landing` — presell/landing page component library (not a checkout funnel; use when building a standalone landing or presell page)

---

### Paths

- **Brand folder:** `<CPK_ROOT>/[brand-name]/`
- **Campaign folder:** `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/`
- **Starter templates repo:** `https://github.com/NextCommerceCo/campaign-cart-starter-templates` (subfolder: `src/[template-slug]`)

---

### Step 2 — Safety Check

If `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/` already exists → **stop and warn the user**. Do not overwrite.

### Step 3 — Create Brand Folder (if needed)

If `<CPK_ROOT>/[brand-name]/` does not exist, create it.

### Step 4 — Initialize CPK Project (if needed)

Check if `<CPK_ROOT>/[brand-name]/package.json` exists.

If **not**, run these commands sequentially inside the brand folder:

```bash
cd <CPK_ROOT>/[brand-name]
npm init -y
npm install next-campaign-page-kit
npx campaign-init
```

`npx campaign-init` creates `_data/campaigns.json` and adds npm scripts to `package.json`. It does **not** create any `src/` folders.

After running, verify it succeeded:
- Check `_data/campaigns.json` exists — if not, stop and warn the user that `campaign-init` may have failed
- Check `package.json` contains a `dev` script — if not, warn the user to run `npx campaign-init` manually

If `package.json` **already exists**, skip — the project is already initialized.

### Step 5 — Copy Starter Template

Before copying, confirm the template slug is valid. Valid slugs are:
`demeter`, `limos`, `olympus`, `olympus-mv-single-step`, `olympus-mv-two-step`, `shop-single-step`, `shop-three-step`, `landing`

If the provided template slug is not in this list → **stop and ask the user to pick a valid template**.

Run from the brand folder:

```bash
cd <CPK_ROOT>/[brand-name]
npx degit NextCommerceCo/campaign-cart-starter-templates/src/[template-slug] src/[campaign-slug]
```

If degit exits with a non-zero code or reports that the source path was not found, stop and warn the user — the template slug may not exist in the upstream repo.

After degit completes (even with exit code 0), verify the directory is not empty:

```bash
[ "$(ls -A src/[campaign-slug])" ] || echo "EMPTY"
```

If the output is `EMPTY`, **stop and warn the user** — degit returned success but extracted nothing. This can happen when the subfolder path is wrong or a GitHub cache issue occurred. Do not continue to Phase 2.

### Step 6 — Fetch the Template's campaigns.json Entry

Fetch the upstream campaigns.json to get the canonical entry for the chosen template:

```
https://raw.githubusercontent.com/NextCommerceCo/campaign-cart-starter-templates/HEAD/_data/campaigns.json
```

If the fetch fails or returns non-JSON, stop and warn the user.

Find the entry matching `[template-slug]`. **Note:** the `olympus-mv-single-step` folder is keyed as `olympus-mv-single` in the upstream campaigns.json — look up `olympus-mv-single` when that template is chosen. For all other templates the key matches the folder name. If no entry is found → **stop and warn the user**. Use its `sdk_version`, `description`, and field structure as the base. Then customise:

- Key: change from `[template-slug]` to `[campaign-slug]`
- `name`: title-case derived from the campaign slug (hyphens → spaces)
- `store_name`, `store_url`, `store_terms`, `store_privacy`, `store_contact`, `store_returns`, `store_shipping`: set to `""`
- `store_phone`, `store_phone_tel`: set to `""`
- `entry_url`: keep as it appears in the upstream entry (typically `"presell"` for checkout funnels)
- `gtm_id`, `fb_pixel_id`: set to `""` unless real values are provided later. Do not preserve placeholder pixel/container IDs from the template.
- `sdk_version`: keep exactly as it appears in the upstream entry — do not hardcode

Merge this entry into `<CPK_ROOT>/[brand-name]/_data/campaigns.json` — do not replace the whole file.

### Step 7 — Copy CLAUDE.md

Download and save the AI context file into the brand project root as `CLAUDE.md`:

```bash
curl -sL "https://raw.githubusercontent.com/NextCommerceCo/campaign-cart-starter-templates/HEAD/docs/campaign-page-kit-template-context.md" \
  -o <CPK_ROOT>/[brand-name]/CLAUDE.md
```

If `CLAUDE.md` already exists in the brand folder, skip — do not overwrite.

---

## Phase 2 — Configure

### Step 8 — Gather Config Inputs

Ask for all of the following in a single message — do not ask one at a time:

If the user supplied a CampaignSpec, pre-fill from:
- `campaign.store_name` for store name when present
- `campaign.tracking` for analytics intent
- `campaign.footer_links[]` for policy/support URLs
- `campaign.seo` for later build/QA notes only; setup does not need to write SEO tags directly

**Required:**
- **API key** — the Campaign Cart API key for this store
- **Store name** — short display name (e.g. "Winter Gloves Co")
- **Store URL** — the main store domain (e.g. `https://wintergloves.com`)

**Recommended but optional:**
- **Store phone** — display format (e.g. `1-800-555-0100`) and tel format (e.g. `+18005550100`)
- **Policy/support URLs** — terms, privacy, contact, returns/refund, shipping. Storefront policy URLs are acceptable; campaign-specific pages are not required.

Validate all URLs before writing any files:
- Each provided URL must start with `https://` and contain at least one `.` after the domain
- If an optional URL is absent, write `""` for that field rather than inventing a placeholder
- If any provided URL fails this check, show which ones are invalid and ask the user to correct or omit them before proceeding. Do not write partial data.

**Optional tracking contract:**
- **Tracking status** — `unknown`, `not_configured`, `configured`, or `custom_required`
- **GTM container ID** — e.g. `GTM-XXXXXXX`
- **Facebook Pixel ID** — e.g. `123456789012345`
- **Custom analytics endpoint or notes** — record in the report unless the template config already supports a matching custom analytics provider

Do not make the user guess through a wall of tracking surfaces. Ask for GTM/Facebook/custom only when they already have values or the CampaignSpec declares them. If tracking is `unknown` or `not_configured`, leave provider IDs blank/disabled and record that tracking is intentionally not configured yet.

### Step 9 — Update config.js

Read `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/assets/config.js`. Make these changes:

1. **`apiKey`** — replace the placeholder value with the provided API key
2. **`storeName`** — replace with a lowercase-hyphenated slug derived from the store name (e.g. "Winter Gloves Co" → `'winter-gloves-co'`). This is an analytics identifier, not a display name.
3. **GTM** (only if a real ID is provided):
   - Set `gtm.enabled` to `true`
   - Set `gtm.settings.containerId` to the provided ID
4. **Facebook Pixel** (only if a real ID is provided):
   - Set `facebook.enabled` to `true`
   - Set `facebook.settings.pixelId` to the provided ID
5. **Absent tracking**:
   - If tracking status is `unknown` or `not_configured`, keep GTM/Facebook disabled or blank. Remove obvious placeholder IDs if the template copied them.
   - If tracking status is `custom_required`, do not invent custom event wiring here; report it as a follow-up for `next-campaigns-build`.

Do not change any other fields. Preserve all comments.

### Step 10 — Update campaigns.json

Read `<CPK_ROOT>/[brand-name]/_data/campaigns.json`. Find the entry with key matching `[campaign-slug]`. If the entry is missing, stop and warn the user — Phase 1 may not have completed successfully.

Update these fields:

- `store_name` → provided store name
- `store_url` → provided store URL
- `store_phone` → provided phone display format, or `""`
- `store_phone_tel` → provided phone tel format, or `""`
- `store_terms` → provided terms URL, or `""`
- `store_privacy` → provided privacy URL, or `""`
- `store_contact` → provided contact URL, or `""`
- `store_returns` → provided returns URL, or `""`
- `store_shipping` → provided shipping URL, or `""`
- `gtm_id` → provided GTM ID, or `""`
- `fb_pixel_id` → provided pixel ID, or `""`

Do not change any other fields in the file.

---

## Report Back

Summarise everything done:

```
Phase 1 — Scaffold
  ✓ Brand folder: created / already existed
  ✓ CPK project: initialized / already present
  ✓ Template copied: [template-slug] → src/[campaign-slug]/
  ✓ campaigns.json seeded (sdk_version: [version])
  ✓ CLAUDE.md: copied / already present

Phase 2 — Configure
  config.js
    ✓ apiKey set
    ✓ storeName set to '[value]'
    ✓ GTM enabled with real ID / left disabled
    ✓ Facebook Pixel enabled with real ID / left disabled
    ✓ Tracking status: unknown / not_configured / configured / custom_required
  campaigns.json ([campaign-slug])
    ✓ store_name and store_url set
    ✓ phone set / left blank
    ✓ policy/support URLs set / left blank
```

Then show next steps:

```
Next steps:
1. npm run dev  → pick [campaign-slug] and verify the page loads + SDK initialises
2. Check the Campaigns app → confirm the API key is valid for this store
3. npm run config  → alternative interactive config editor if you need to adjust settings later
4. QA checklist: check the SDK QA checklist in docs/
```

Dev server preview URLs for the chosen template:

| Template | Pages |
|----------|-------|
| demeter | /[slug]/presell/ · /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| limos | /[slug]/presell/ · /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| olympus | /[slug]/presell/ · /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| olympus-mv-single-step | /[slug]/presell/ · /[slug]/checkout/ · /[slug]/upsell-mv/ · /[slug]/receipt/ |
| olympus-mv-two-step | /[slug]/presell/ · /[slug]/select/ · /[slug]/checkout/ · /[slug]/upsell-mv/ · /[slug]/receipt/ |
| shop-single-step | /[slug]/presell/ · /[slug]/checkout/ · /[slug]/upsell/ · /[slug]/receipt/ |
| shop-three-step | /[slug]/presell/ · /[slug]/information/ · /[slug]/shipping/ · /[slug]/billing/ · /[slug]/upsell/ · /[slug]/receipt/ |
| landing | /[slug]/landing/ (component library — no checkout funnel) |
