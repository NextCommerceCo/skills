---
name: next-campaigns-setup
version: 2.0.0
description: |
  Start or continue a campaign from a public URL, design, existing repo, or
  saved Map. Follow the public agent setup procedure for intake; scaffold and
  configure campaign-page-kit when a new CPK project is the chosen path, with
  or without a campaign key. Preserve an existing builder and deployment
  workflow, and hand off to the relevant Campaigns OS skill.

  Use when: "new CPK campaign", "set up a campaign", "new funnel",
  "build a campaign from this URL", "continue this campaign repo", or when
  a user provides a brand name + public route slug + template choice.
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

# /next-campaigns-setup: Start or Continue a Campaign

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-campaigns-setup` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Start with the public [agent setup procedure](https://developers.nextcommerce.com/agent-setup/prompt.md).
It owns intake and continuation across tools; this skill owns Page Kit
scaffolding and first configuration. If that URL is unavailable, read the
[same guide in its source repository](https://github.com/NextCommerceCo/developer-docs/blob/main/content/agent-setup.md)
and record the revision used.

## Before Scaffolding

Read the supplied inputs and any existing handoff before asking for details.

| Starting point | Next step |
|---|---|
| Public funnel/store URL or design | Record observed pages, source URLs and capture dates; separate the proposed campaign from what the source proves. Save a guided Campaign Build Brief and a commerce dependency list. |
| Existing or cloned repo | Inspect its README, package/lockfile, build commands, routes, config, deployment configuration, spec and QA evidence. Preserve its builder and deployment conventions. Do not run `campaign-init` over it. |
| External build or export | Identify the source root, assets, page routes and build instructions. Keep the original intact. Use the Campaigns OS source-adapter procedure only if that is the selected integration path. |
| Saved Map / CampaignSpec / Build Packet | Read the existing identity and artifacts. Follow the installed Campaigns OS `next` result; do not create a second spec, scaffold or stage record. |

A cloned repository is a code starting point. It does not clone the Campaigns
App configuration or transfer the source campaign's QA. Treat inherited keys,
package refs, tracking IDs and domains as source-campaign values until checked,
and never print credentials.

Use the [Campaign Build Brief](https://github.com/NextCommerceCo/campaigns-os/blob/main/docs/campaign-build-brief.md)
format for presentation intent (`schema_version: campaigns-os-build-brief/v1`,
`brief_mode: guided`). Keep observations, unresolved commerce dependencies and
continuation instructions in the project's README or existing handoff document.
Do not invent campaign IDs, package refs, offers, shipping IDs or a saved Map to
make Campaigns OS accept incomplete inputs.

For resume, the handoff names the source and target paths and revisions, the
builder and deploy convention, the brief, spec and packet paths that exist, the
checks completed, the open business decisions and the next action. Earlier QA
applies only to the revision and configuration it tested.

### Choose the Next Capability

- New Page Kit project: continue to Phase 1. A campaign key is optional; without
  one, the scaffold keeps the starter templates' empty `apiKey` and the pages
  render for visual review with no commerce calls and no SDK analytics events. The SDK still
  fetches locale and country rules from `i18n-rules.nextcommerce.com`, and
  vendor snippets set through `_data/campaigns.json` keys load regardless.
- Store campaign provisioning: load [next-campaigns-create](https://github.com/NextCommerceCo/skills/tree/main/next-campaigns-create)
  when the store and authorized API access are available. It creates, edits,
  adopts and clones campaign configuration behind its plan gate; copying a Git
  repo does none of these.
- Campaigns OS lifecycle or build: use the project's pinned
  `@nextcommerce/campaigns-os` and its bundled skills. Read its
  [local-spec entry](https://github.com/NextCommerceCo/campaigns-os/blob/main/docs/build-packet.md#local-spec-entry)
  once real commerce bindings are available.
- Existing Build Packet: `next-campaigns-os-setup` owns setup, `next-campaigns-build`
  owns wiring, and Polish and QA follow the packet. Those skills ship with
  [campaigns-os](https://github.com/NextCommerceCo/campaigns-os), not this repository.

For a no-write task, do not provision campaigns, run cart or order probes,
publish Map, QA or run records, or deploy. Record the untested boundary so the
next session continues under the same constraint.

---

## Phase 1 — Scaffold

Enter this phase for a new Page Kit campaign. Gather the inputs before creating
files or installing dependencies.

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

Reuse supplied choices and ask for only missing business decisions together:

1. **Brand name** — the client/brand slug. Lowercase, hyphens only. Becomes the brand folder.
2. **Public route slug** — the pretty campaign URL/page-kit folder slug, usually product name + version (e.g. `grounding-mat`, `grounding-mat-v2`). Lowercase, hyphens only. This is not the Campaign Map Builder Map ID.
3. **Starter template** — recommend a family from the public
   [template catalog](https://github.com/NextCommerceCo/campaign-cart-starter-templates/blob/main/templates.json)
   based on the required behavior, then confirm only choices the input leaves
   open. The catalog currently recommends `apollo` for most single-step
   campaigns and `apollo-mv-single-step` for variant products. Two-step and
   three-step families are available when the intended flow needs them.
   `landing` is a component library, not a checkout funnel. Read the catalog
   rather than offering a family from memory.

### Step 2 — Gather Config Inputs

Gather only the missing inputs, together. The campaign key is the
browser-visible Campaign Cart API key for the selected campaign, never an Admin
API token. If it is not available yet, scaffold without it and bind it later
with `npm run config`.

If the user supplied a CampaignSpec, pre-fill from:
- `campaign.store_name` for store name when present
- `campaign.slug` for the public route slug when present
- `spec_identity.map_id` for the report/provenance only; never use the Map ID as the page-kit folder unless the user explicitly asks
- `campaign.tracking` for analytics intent
- `campaign.footer_links[]` for policy/support URLs
- `campaign.seo` for later build/QA notes only; setup does not need to write SEO tags directly

**Required:**
- **Store name** — short display name (e.g. "Example Store")
- **Store URL** — the main store domain (e.g. `https://example-store.com`)

**Recommended but optional:**
- **API key** — the Campaign Cart API key for the selected campaign
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

---

### Paths

- **Brand folder:** `<CPK_ROOT>/[brand-name]/`
- **Campaign folder:** `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/`
- **Starter templates repo:** `https://github.com/NextCommerceCo/campaign-cart-starter-templates` (subfolder: `src/[template-slug]`)

---

### Step 3 — Safety Check

If `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/` already exists → **stop and warn the user**. Do not overwrite.

### Step 4 — Create Brand Folder (if needed)

If `<CPK_ROOT>/[brand-name]/` does not exist, create it.

### Step 5 — Install and Scaffold with campaign-init

`campaign-init --non-interactive` handles the full scaffold in one command: adds npm scripts, creates `_data/campaigns.json`, downloads the starter template, seeds the campaigns.json entry, and installs the AI context file.

From the brand folder, use the existing dependency manager and lockfile; do not
upgrade a pinned Page Kit dependency as part of setup. If no `package.json`
exists, run `npm init -y` and `npm install --save-exact next-campaign-page-kit`.
If the package is declared but not installed, restore it from the lockfile.

Run the project-local executable so the pinned version is the one that runs.
Replace `<ai-context>` with the agent's context target (`claude`, `codex`,
`cursor`, `copilot`, or `none`); `--keep-ai-context` leaves an existing context
file in place. With a key:

```bash
cd <CPK_ROOT>/[brand-name] &&
./node_modules/.bin/campaign-init --non-interactive --json \
  --template [template-slug] \
  --slug [campaign-slug] \
  --name "[Campaign Display Name]" \
  --api-key "${CAMPAIGN_API_KEY:?set CAMPAIGN_API_KEY first}" \
  --ai-context <ai-context> \
  --keep-ai-context
```

Without a key:

```bash
cd <CPK_ROOT>/[brand-name] &&
./node_modules/.bin/campaign-init --non-interactive --json \
  --template [template-slug] \
  --slug [campaign-slug] \
  --name "[Campaign Display Name]" \
  --ai-context <ai-context> \
  --keep-ai-context
```

Without a key the scaffold keeps the starter templates' empty `apiKey`. The
Campaign Cart SDK does not boot without a key, so the pages make no commerce
calls and emit no SDK analytics events; it still fetches locale and country
rules. Templates fetched before they shipped empty carried a demo campaign key:
if the top-level `apiKey` in `assets/config.js` is not empty after a no-key
scaffold, clear it in Step 6 and say so in the report.

Capture the JSON output: `writes.api_key_applied`, `writes.files_extracted` and
the context result go in the scaffold report. A local key write does not prove
a valid campaign or working commerce.

**Exit code handling:**

| Code | Meaning | Action |
|------|---------|--------|
| `0` | Success | Continue |
| `2` | Template not found | Stop — ask user to pick a valid template |
| `3` | Target slug already exists | Stop — Step 3 should have caught this; warn user |
| `4` | Upstream fetch failed | Stop — check network, retry once |
| `5` | Missing required flag | Stop — internal error; report which flag |
| `6` | Invalid input | Stop — report the validation message |
| `7` / `8` | Partial write / rollback failed | Stop — report the error; do not attempt Phase 2 |

Do not fall back to manual `degit` + `campaigns.json` patching + `curl CLAUDE.md`. If `campaign-init` fails, surface the error and stop.

### Existing Campaigns OS Packet

If this task already has `campaign-runtime.build.json`, stop the standalone
scaffold flow and follow `next-campaigns-os-setup` from the matching installed
Campaigns OS release. Read its Build Context and Assembly Report, preserve the
selected template and source, and use its documented `record setup` command.
Never hand-write `setup-handoff.json` or edit OS stage evidence directly.

---

## Phase 2 — Configure

### Step 6 — Update config.js

Read `<CPK_ROOT>/[brand-name]/src/[campaign-slug]/assets/config.js`. Make these changes:

1. **`apiKey`** — the top-level field must hold the selected key, or `''` when there is none. If an older template copy left a demo key in place, set it to `''` here and record "cleared by hand" in the report. If the key arrived after scaffolding, bind it with `npm run config`.
2. **`storeName`** — replace with a lowercase-hyphenated slug derived from the store name (e.g. "Example Store" → `'example-store'`). This is an analytics identifier, not a display name.
3. **GTM** (only if a real ID is provided):
   - Set `gtm.enabled` to `true`
   - Set `gtm.settings.containerId` to the provided ID
4. **Facebook Pixel** (only if a real ID is provided):
   - Set `facebook.enabled` to `true`
   - Set `facebook.settings.pixelId` to the provided ID
5. **Absent tracking**:
   - If tracking status is `unknown` or `not_configured`, disable GTM/Facebook and clear their IDs. Remove inherited demo IDs if the template copied them. Inspect any other enabled analytics providers and leave them disabled unless the operator declared them.
   - If tracking status is `custom_required`, do not invent custom event wiring here; report it as a follow-up for `next-campaigns-build`.

Do not change any other fields. Preserve all comments.

### Step 7 — Update campaigns.json

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
- Any other vendor key from the starter templates' [analytics key table](https://github.com/NextCommerceCo/campaign-cart-starter-templates/blob/main/_shared/analytics/README.md) → only when the operator declared that vendor. These snippets load from `campaigns.json` regardless of the SDK key, so an unconfigured campaign keeps every vendor key empty.

Do not change any other fields in the file.

---

## Report Back

Summarise everything done:

```
Phase 1 — Scaffold
  ✓ Brand folder: created / already existed
  ✓ campaign-init: [N] files extracted, campaigns.json seeded (sdk_version: [version])
  ✓ Agent context: [target file] written / already present (--keep-ai-context)

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

Keep the continuation instructions in the project's README or existing handoff
document so another agent needs no chat history. Before starting `npm run dev`,
check the state it will open in: for an unconfigured campaign, the top-level
`apiKey` in `config.js` is `''` and the slug's vendor keys in `campaigns.json`
are empty, so the pages show placeholder prices and send nothing to a vendor;
for a configured campaign, the key, store identity and every vendor key in the
[analytics key table](https://github.com/NextCommerceCo/campaign-cart-starter-templates/blob/main/_shared/analytics/README.md) are the ones the operator selected, because
the dev server talks to that campaign and fires that tracking.
Do not submit forms or create orders as a setup check. Read the generated
routes from the selected template rather than assuming an `upsell.html` exists.
