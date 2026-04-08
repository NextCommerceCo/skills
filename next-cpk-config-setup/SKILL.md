---
name: next-cpk-config-setup
version: 1.0.0
description: |
  Configure a campaign-page-kit (CPK) campaign — writes API key, store details,
  and analytics settings into config.js and campaigns.json.

  Use after scaffolding with /next-cpk-new-campaign, or to update an existing
  campaign's config. Use when: "configure campaign", "set up API key", "wire up
  store details", "add GTM", "add Facebook Pixel", or "cpk config".
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

# /next-cpk-config-setup: Configure a CPK Campaign

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-cpk-config-setup/` (see repo README). Invoke with `/next-cpk-config-setup`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-cpk-config-setup/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. The instructions are tool-agnostic markdown. |

---

Wire up `config.js` and `campaigns.json` for a campaign-page-kit campaign.

## Step 0 — Resolve CPK Root

Check for a `CPK_ROOT` environment variable:
```bash
echo "${CPK_ROOT:-not set}"
```

If not set, ask the user:
> What is the path to your `sellmore.campaigns.cpk` folder?
> (e.g. `/Users/you/projects/sellmore.campaigns.cpk`)

Use this value as `<CPK_ROOT>` for all paths below. Do not hardcode any absolute path.

## Paths

- **config.js:** `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/assets/config.js`
- **campaigns.json:** `<CPK_ROOT>/[brand-name]/_data/campaigns.json`

---

## Step 1 — Gather Inputs

If brand name and campaign slug were not provided as args, ask for them now.

Then confirm the campaign exists:
- If `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/assets/config.js` does not exist → **stop and warn**. The campaign has not been scaffolded yet — run `/next-cpk-new-campaign` first.

Then gather the following. Ask for all of them in a single message — do not ask one at a time:

**Required:**
- **API key** — the Campaign Cart API key for this store
- **Store name** — short display name (e.g. "Winter Gloves Co") — used for `storeName` in `config.js` and `store_name` in `campaigns.json`
- **Store URL** — the main store domain (e.g. `https://wintergloves.com`)
- **Store phone** — display format (e.g. `1-800-555-0100`) and tel format (e.g. `+18005550100`)
- **Terms URL** — full URL to terms of service page
- **Privacy URL** — full URL to privacy policy page
- **Contact URL** — full URL to contact page
- **Returns URL** — full URL to returns/refund policy page
- **Shipping URL** — full URL to shipping policy page

After collecting, validate all URLs before writing any files:
- Store URL, Terms URL, Privacy URL, Contact URL, Returns URL, and Shipping URL must each start with `https://` and contain at least one `.` after the domain — e.g. `https://example.com/path`
- If any URL fails this check, show which ones are invalid and ask the user to correct them before proceeding. Do not write partial data.

**Optional (press enter to skip):**
- **GTM container ID** — e.g. `GTM-XXXXXXX` (leave blank to keep disabled)
- **Facebook Pixel ID** — e.g. `123456789012345` (leave blank to keep disabled)

---

## Step 2 — Update config.js

Read the current `config.js`. Make the following changes:

1. **`apiKey`** — replace the placeholder value with the provided API key
2. **`storeName`** — replace with a lowercase-hyphenated slug derived from the store name (e.g. "Winter Gloves Co" → `'winter-gloves-co'`)
3. **GTM** (if GTM container ID was provided):
   - Set `gtm.enabled` to `true`
   - Set `gtm.settings.containerId` to the provided ID
4. **Facebook Pixel** (if pixel ID was provided):
   - Set `facebook.enabled` to `true`
   - Set `facebook.settings.pixelId` to the provided ID

Do not change any other fields. Preserve all comments.

---

## Step 3 — Update campaigns.json

Read the current `campaigns.json`. Find the entry with key matching `[campaign-slug]`. Update these fields:

- `store_name` → provided store name
- `store_url` → provided store URL
- `store_phone` → provided phone display format
- `store_phone_tel` → provided phone tel format
- `store_terms` → provided terms URL
- `store_privacy` → provided privacy URL
- `store_contact` → provided contact URL
- `store_returns` → provided returns URL
- `store_shipping` → provided shipping URL
- `gtm_id` → provided GTM ID (or leave as existing placeholder if skipped)
- `fb_pixel_id` → provided pixel ID (or leave as existing placeholder if skipped)

Do not change any other fields in the file.

---

## Step 4 — Report Back

Summarise what was written:

```
config.js
  ✓ apiKey set
  ✓ storeName set to '[value]'
  ✓ GTM enabled / GTM left disabled
  ✓ Facebook Pixel enabled / left disabled

campaigns.json ([campaign-slug])
  ✓ store_name, store_url, store_phone set
  ✓ store_terms, store_privacy, store_contact, store_returns, store_shipping set
```

Then show next steps:

```
Next steps:
1. npm run dev  → pick [campaign-slug] and verify the page loads + SDK initialises
2. Check the Campaigns app → confirm the API key is valid for this store
3. QA checklist: docs/olympus-v0.4.0-sdk-qa-checklist.md
```
