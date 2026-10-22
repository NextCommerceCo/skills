---
name: next-campaigns-setup
version: 1.1.4
description: |
  Start or continue a campaign from a public URL, design, existing repo, or
  saved Map. Follow the public agent setup procedure for intake and a safe
  visual concept before credentials; scaffold and configure campaign-page-kit
  only when a new CPK project is the chosen path. Preserve an existing builder
  and deployment workflow, and hand off to the relevant Campaigns OS skill.

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
It owns intake and continuation across tools. This skill owns Page Kit
scaffolding and first configuration; load only the capability the next step needs.
If that URL fails or does not return the guide, report the URL and error, then
read the [same guide in its public source repository](https://github.com/NextCommerceCo/developer-docs/blob/main/content/agent-setup.md)
and record the revision used. If both are unavailable, continue only the
observation, brief and isolated static-concept work specified below. Leave
canonical-guide-dependent transitions pending; do not reconstruct missing
commerce, Campaigns OS or deployment instructions from memory.

## Before Scaffolding

Read the supplied inputs and any existing handoff before asking for details.

| Starting point | Next step |
|---|---|
| Public funnel/store URL or design | Record observed pages, source URLs and capture dates; separate the proposed campaign from what the source proves. Save a guided Campaign Build Brief and a commerce dependency list before requesting credentials. |
| Existing or cloned repo | Inspect its README, package/lockfile, build commands, routes, config references, deployment configuration, spec and QA evidence without executing them. Preserve its builder and deployment conventions. Do not run `campaign-init` over it. |
| External build or export | Identify the source root, assets, page routes and build instructions. Keep the original intact. Use the published Campaigns OS source-adapter procedure only if that is the selected integration path; unsupported formats stay an explicit gap. |
| Saved Map / CampaignSpec / Build Packet | Read the existing identity and artifacts. Follow the installed Campaigns OS `next` result; do not create a second spec, scaffold, or stage record. |

A cloned repository is a code starting point. It does not clone the Campaigns
App configuration or transfer the source campaign's QA. Treat inherited keys,
package refs, tracking IDs, domains and evidence as source-campaign values until
checked. Do not load `.env` files, print credentials, run install/build scripts,
or open the original campaign just to identify its structure.

Use the existing [Campaign Build Brief](https://github.com/NextCommerceCo/campaigns-os/blob/main/docs/campaign-build-brief.md)
format for presentation intent (`campaign-build-brief.yaml` or `.json`,
`schema_version: campaigns-os-build-brief/v1`, `brief_mode: guided`; use the
linked schema/example for fields). Keep observations, unresolved commerce dependencies and
continuation instructions in the project's README or existing handoff document.
A brief can precede a spec. Do not invent campaign IDs, package refs, offers,
shipping IDs or a saved Map to make Campaigns OS accept incomplete inputs.

For resume, the handoff must name the source and target paths/revisions, chosen
builder and deploy convention, brief/spec/packet paths that actually exist,
completed checks and their limits, unresolved business decisions, and the next
safe action. Re-read those files in a new session. Earlier QA applies only to
the source revision and configuration it tested; changed commerce or pages
need new evidence.

### Before a Campaign Key Is Available

Do useful local work without credentials or commerce API calls: capture public observations,
propose the page flow, record product/variant, price, offer, shipping, payment,
policy and tracking dependencies, and prepare a visual concept. Label source
prices as observations, not verified NEXT campaign values. Catalogue product
creation/import is separate from campaign provisioning: `next-campaigns-create`
reads existing products and variants; it does not import a Shopify catalogue.

**Page Kit 0.3.0 does not make no-key scaffolds inert.** Its template output can
retain a demo campaign key and analytics. Do not run `npm run dev` or open that
output in the no-key/no-write flow. Do not assume an unmerged PR or a newer
version number supplies this guarantee; check the installed release's documented
preview behavior before using it.

Until a released inert-preview feature is verified, make a separate static
concept directory from approved copy and local assets. Do not copy or execute
source scripts, SDK/config files, forms, frames, remote assets, tracking, or
payment/navigation actions. Author plain HTML/CSS, use disabled controls, add
"Commerce unconfigured — visual concept only" visibly, and put this policy
before all other head content (apart from charset):

```html
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'none'; script-src 'none'; frame-src 'none'; form-action 'none'; base-uri 'none'">
```

Serve only that directory on loopback, using an available static server. Inspect
its HTML and local asset references before opening it; external links and CSS
URLs must be absent. Record the local start command, preview path and observed
network behavior. This is visual evidence only: no commerce integration, SDK
initialization, prices, shipping, payment, tracking receipt or launch readiness
has been verified. If the isolated preview cannot be checked, return the source
files and explain that it has not been opened.

### Choose the Next Capability

- New Page Kit project with a selected template: continue to Phase 1. In a
  no-key/no-write task, stay with the separate concept above.
- Store campaign provisioning: load [next-campaigns-create](https://github.com/NextCommerceCo/skills/tree/main/next-campaigns-create)
  when the selected store and authorized API access are available. It supports
  create, `edit` for its own runs, `adopt` + `diff` + `update` for existing
  campaigns, and a separately approved campaign clone. Copying a Git repo does
  none of these. Each remote write follows that skill's reviewed plan gate.
- Campaigns OS lifecycle or build: use the project's pinned
  `@nextcommerce/campaigns-os` and its bundled skills. Its runtime requirement
  is separate from Page Kit's; follow the installed package's setup instructions.
  Read its [local-spec entry](https://github.com/NextCommerceCo/campaigns-os/blob/main/docs/build-packet.md#local-spec-entry)
  only after real commerce bindings are available. A saved Map is optional for
  that entry; retain any Map requirement imposed by the delivery or pilot flow.
- Existing Build Packet: `next-campaigns-os-setup` owns OS-directed setup,
  `next-campaigns-build` owns wiring, and Polish/QA follow the packet. Those
  skills come from [campaigns-os](https://github.com/NextCommerceCo/campaigns-os),
  not this repository. Do not replace them with this standalone scaffolder.

For a no-write task, do not provision campaigns, run cart/order probes, publish
Map/QA/run records, or deploy. Record the exact untested boundary so the next
session can continue under the same constraint.

---

## Phase 1 — Configured Setup

Enter this phase only for a new Page Kit campaign with a selected campaign key
and permission to configure its local runtime. A no-key or no-write task stays
in the isolated concept procedure above; it does not run the commands below.
Complete the inputs and checks before creating files or installing dependencies.

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
   `landing` is a component library, not a checkout funnel. Re-read the catalog
   rather than offering removed families such as `limos`.

### Step 2 — Gather Config Inputs

For a configured campaign, gather only missing inputs together. If the key or
store binding is unavailable, return to the no-key procedure; do not substitute
a template/demo key. Have the operator supply credentials through a local
configuration file or the approved secret mechanism, never in chat.

If the user supplied a CampaignSpec, pre-fill from:
- `campaign.store_name` for store name when present
- `campaign.slug` for the public route slug when present
- `spec_identity.map_id` for the report/provenance only; never use the Map ID as the page-kit folder unless the user explicitly asks
- `campaign.tracking` for analytics intent
- `campaign.footer_links[]` for policy/support URLs
- `campaign.seo` for later build/QA notes only; setup does not need to write SEO tags directly

**Required:**
- **API key** — the public Campaign Cart API key for the selected campaign; never the store Admin API token
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

`campaign-init --non-interactive` (available since `next-campaign-page-kit` v0.1.1) handles the full scaffold in one command: adds npm scripts, creates `_data/campaigns.json`, downloads the starter template, seeds the campaigns.json entry, and installs the AI context file.

From the brand folder, inspect `package.json`, the lockfile and install scripts
before running them. Use the existing dependency manager and lockfile; do not
upgrade a pinned Page Kit dependency as part of setup.

- If no `package.json` exists, create it with `npm init -y`.
- If the project does not declare `next-campaign-page-kit`, select a reviewed
  exact release and install it, for example
  `npm install --save-exact next-campaign-page-kit@<selected-version>`.
- If it is declared but not installed, restore dependencies from the existing
  lockfile with the project's documented install command. If that fails, report
  the error; do not fall back to an unpinned/global install.

Verify the project-local package and executable before invoking init:

```bash
cd <CPK_ROOT>/[brand-name]
node -p "require('./node_modules/next-campaign-page-kit/package.json').version"
test -x ./node_modules/.bin/campaign-init
./node_modules/.bin/campaign-init --help
```

All three must succeed. If they do not, resolve the missing dependency/bin in
this project before continuing; `npx --no-install` is not an install fallback.
Check the installed help supports the flags used below.

Use the key resolved in Step 2 as `CAMPAIGN_API_KEY` through the approved local
secret mechanism; do not echo it. Set `AI_CONTEXT` to the current agent's
supported target (`claude`, `codex`, `cursor`, `copilot`, or `none`).
`--keep-ai-context` is a boolean flag accepted by Page Kit 0.3.0: it skips an
existing target, still creates a missing target, and does nothing for `none`.
It does not require a value or a pre-existing file.

```bash
cd <CPK_ROOT>/[brand-name]
: "${CAMPAIGN_API_KEY:?Selected campaign key is required for configured setup}"
npx --no-install campaign-init --non-interactive --json \
  --template [template-slug] \
  --slug [campaign-slug] \
  --name "[Campaign Display Name]" \
  --api-key "$CAMPAIGN_API_KEY" \
  --ai-context "$AI_CONTEXT" \
  --keep-ai-context
```

Capture the JSON output. Page Kit 0.3.0 emits these fields after a successful
non-dry-run key write (other fields omitted):

```json
{
  "status": "ok",
  "dry_run": false,
  "writes": { "api_key_applied": true }
}
```

The released [init implementation](https://unpkg.com/next-campaign-page-kit@0.3.0/lib/actions/init.js)
defines this result and the context-file behavior above. Confirm those values
before reporting the key bound locally; the result does not prove a valid
campaign or working commerce. If the field is absent, false, or the installed
release uses a different contract, report binding as unconfirmed and consult
that release's docs/source rather than guessing success. Include
`writes.files_extracted` and the context result in the scaffold report.

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

1. **`apiKey`** — confirm init bound the selected key without printing it. If binding is still needed, use the installed Page Kit `campaign-config` command. Keep any static-preview marker in place until its documented configuration flow enables runtime; never clear a marker by hand.
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

For a local concept, report the brief and preview paths, the commerce dependencies,
the checks actually performed, and the next safe action instead of the completed
scaffold/config report above. Keep the continuation instructions in the project's
README or existing handoff document so another agent needs no chat history.

For a configured campaign, check the selected campaign identity and tracking
configuration before opening `npm run dev`; a local origin does not make remote
commerce calls safe. Follow the authorized QA depth and the relevant installed
Campaigns OS skill. Do not submit forms or create orders as a setup check.
Read generated page routes from the selected template; do not assume an
`upsell.html` exists when the family uses a named upsell variant.
