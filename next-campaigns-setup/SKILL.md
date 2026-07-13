---
name: next-campaigns-setup
version: 1.1.0
description: |
  End-to-end setup for a new campaign-page-kit (CPK) campaign — uses
  campaign-init --non-interactive to scaffold the project, download the starter
  template, seed campaigns.json, and install AI context in one command, then
  wires up config.js and campaigns.json with API key, store details, optional
  policy links, and declared analytics in one pass.

  Use when: "new CPK campaign", "set up a campaign", "scaffold and configure",
  "new funnel", or when a user provides a brand name + public route slug + template choice.
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
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-campaigns-setup` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Scaffold and configure a new campaign-page-kit campaign in one pass.

Boundary with other campaign skills:
- Use this skill for repo/project bootstrap: brand folder, page-kit init, starter template copy, `campaigns.json`, `CLAUDE.md`, and first config values.
- In the linked Campaigns OS runtime flow, use this skill only when public `campaigns-os next --packet <packet>` or `campaigns-os doctor --packet <packet>` routes the handoff to setup. Consume `campaign-runtime.build.json` and `.campaign-runtime/build-context.json`, then emit `.campaign-runtime/setup-handoff.json` for `next-campaigns-build`.
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
2. **Public route slug** — the pretty campaign URL/page-kit folder slug, usually product name + version (e.g. `grounding-mat`, `grounding-mat-v2`). Lowercase, hyphens only. This is not the Campaign Map Builder Map ID.
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

### Step 4 — Scaffold with campaign-init

`campaign-init --non-interactive` (available since `next-campaign-page-kit` v0.1.1) handles the full scaffold in one command: adds npm scripts, creates `_data/campaigns.json`, downloads the starter template, seeds the campaigns.json entry, and installs the AI context file.

**If `package.json` does not exist** in the brand folder, bootstrap first:

```bash
cd <CPK_ROOT>/[brand-name]
npm init -y
npm install next-campaign-page-kit
```

**Then run campaign-init** (whether or not the project was just bootstrapped):

```bash
cd <CPK_ROOT>/[brand-name]
npx campaign-init --non-interactive --json \
  --template [template-slug] \
  --slug [campaign-slug] \
  --name "[Campaign Display Name]" \
  --ai-context claude \
  $([ -f CLAUDE.md ] && echo "--keep-ai-context")
```

Capture the JSON output. On success (`exit 0`) it contains the campaign slug, template used, files extracted, and AI context write status — include the extracted file count in your Phase 1 report.

**Exit code handling:**

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Continue |
| `2` | Template not found | Stop — ask user to pick a valid template |
| `3` | Target slug already exists | Stop — Step 2 should have caught this; warn user |
| `4` | Upstream fetch failed | Stop — check network, retry once |
| `5` | Missing required flag | Stop — internal error; report which flag |
| `6` | Invalid input | Stop — report the validation message |
| `7` / `8` | Partial write / rollback failed | Stop — report the error; do not attempt Phase 2 |

Do not fall back to manual `degit` + `campaigns.json` patching + `curl CLAUDE.md`. If `campaign-init` fails, surface the error and stop.

### Linked Runtime Handoff

When invoked from Campaign Runtime Assembly, read the generated artifacts first:

- `campaign-runtime.build.json`
- `.campaign-runtime/build-context.json`
- `.campaign-runtime/assembly-report.json`

Only scaffold when the build context says:

```json
{
  "scaffold": {
    "mode": "fresh",
    "required": true
  }
}
```

If the context says `mode: "existing"`, do not run setup; hand back to `next-campaigns-build`.

After successful scaffold, write `.campaign-runtime/setup-handoff.json`:

```json
{
  "schema_version": "campaign-runtime-setup-handoff/v0",
  "stage": "setup",
  "status": "completed",
  "packet_path": "campaign-runtime.build.json",
  "context_path": ".campaign-runtime/build-context.json",
  "report_path": ".campaign-runtime/assembly-report.json",
  "scaffold": {
    "mode": "fresh",
    "template_family": "<locked-family>",
    "campaign_slug": "<public-route-slug>",
    "campaign_dir": "src/<public-route-slug>",
    "campaign_init_result_path": ".campaign-runtime/campaign-init-result.json",
    "files_created": 0
  },
  "next": {
    "stage": "assembly",
    "owner": "next-campaigns-build"
  }
}
```

Also update only the `setup` stage in `.campaign-runtime/assembly-report.json`. Preserve OS/build/polish/QA sections.

---

## Phase 2 — Configure

### Step 8 — Gather Config Inputs

Ask for all of the following in a single message — do not ask one at a time:

If the user supplied a CampaignSpec, pre-fill from:
- `campaign.store_name` for store name when present
- `campaign.slug` for the public route slug when present
- `spec_identity.map_id` for the report/provenance only; never use the Map ID as the page-kit folder unless the user explicitly asks
- `campaign.tracking` for analytics intent
- `campaign.footer_links[]` for policy/support URLs
- `campaign.seo` for later build/QA notes only; setup does not need to write SEO tags directly

**Required:**
- **API key** — the Campaign Cart API key for this store
- **Store name** — short display name (e.g. "Example Store")
- **Store URL** — the main store domain (e.g. `https://example-store.com`)

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
2. **`storeName`** — replace with a lowercase-hyphenated slug derived from the store name (e.g. "Example Store" → `'example-store'`). This is an analytics identifier, not a display name.
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
  ✓ campaign-init: [N] files extracted, campaigns.json seeded (sdk_version: [version])
  ✓ CLAUDE.md: written / already present (--keep-ai-context)

Phase 2 — Configure
  provenance
    ✓ Map ID: [map-id] / not provided
    ✓ public route slug: [campaign-slug]
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
