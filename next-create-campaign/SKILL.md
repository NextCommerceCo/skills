---
name: next-create-campaign
version: 0.3.0
description: |
  Provision a launch-ready Campaigns App campaign over the NEXT Admin API:
  read the store's catalogue, gateway groups and shipping methods, recommend a
  campaign structure from the offer doctrine, show the operator every request
  and the landed prices, then create the campaign, its packages, shipping
  methods and offers in one approval-gated run. Hands back the campaign api_key
  and the package ids the funnel needs.

  Use when: "create a campaign for {store}", "set up the campaign in the
  Campaigns App", "provision a campaign over the API", "recommend a campaign
  structure", "build the offers for {product}", or when a new campaign needs to
  exist on a store before funnel work starts. Creating a NEW campaign only;
  editing a campaign that already exists is out of scope.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - TodoWrite
---

# /next-create-campaign: Provision a Campaigns App campaign over the Admin API

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-create-campaign` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Creates a new Campaigns App campaign on a NEXT store over the Admin API and
hands back the campaign api_key and the package ids the funnel needs. The
bundled engine (`scripts/campaign_admin.py`, run through the
`next-create-campaign.sh` launcher) owns the API contract, the approval gate,
the run manifest and the safety checks. This skill drives it and makes the
operator decisions the engine refuses to guess.

If this file and the engine ever disagree, the engine wins for behaviour and
`references/admin-api-contract.md` wins for the API contract.

---

## Scope

This skill creates a NEW campaign. Editing a campaign that already exists is out
of scope: the engine never changes a campaign the run manifest does not own, and
teardown removes only what that manifest records. Changes to a live campaign are
made in the Campaigns App dashboard.

Boundary with other campaign skills:
- Use this skill to make the campaign exist on the store: the campaign, its
  packages, shipping methods and offers, plus the api_key and package ids that
  come out of creating them.
- Use `next-campaigns-setup` after this skill to scaffold the campaign-page-kit
  project. It consumes the api_key and package ids this skill produces.
- Use the Campaigns OS skills, starting with `next-campaigns-os`, for
  CampaignSpec lifecycle work. This skill does not read or write a CampaignSpec
  and builds no pages.
- The API does not cover Allowed Domains, PayPal account linking or Map Builder.
  Phase 6 hands those to the operator as dashboard steps.

## Admin API Conventions

The engine sends every authenticated store request. It follows the public
Admin API conventions from https://developers.nextcommerce.com/docs/admin-api,
and its errors make more sense with them in view:

- Base URL: `https://{subdomain}.29next.store/api/admin/`
- Auth header: `Authorization: Bearer $NEXT_ADMIN_API_TOKEN`
- Version header: `X-29next-API-Version: 2024-04-01`. The campaign endpoints
  are not there without it.

Do not use `/api/v1/...` paths or `Authorization: Token ...` on the store's
Admin API; those are not the documented NEXT Admin API convention and commonly
return storefront HTML 404 pages instead of JSON. A raw key or a `Token` prefix
on the Admin API gets a 401 that looks like a bad key.

The Campaign Cart API is a different host with a different credential. `verify`
calls `POST https://campaigns.apps.29next.com/api/v1/carts/calculate/` with the
campaign api_key sent raw in the `Authorization` header: no scheme prefix, and
never the Admin token. The engine builds both kinds of request; do not
hand-craft either one.

## Prerequisites

- **bash and Python 3.9 or newer.** `NEXT_CREATE_CAMPAIGN_PYTHON` can point the
  launcher at a specific interpreter.
- **Windows without bash:** run
  `python3 <skill-dir>/scripts/campaign_admin.py <subcommand> ...` directly,
  with the token set by the operator in their own shell. The run manifest's
  mode-600 protection is not enforced there, so keep the run directory
  somewhere only the operator can read.
- **The Campaigns App installed on the store.**
- **git on the PATH** when the working directory is inside a git repository.
  The engine asks git whether the run directory is ignored before it writes.
- **An Admin API key** created in the store admin under Dashboard > Settings >
  API Access, with all six of these permissions:

| Permission | Used for |
|---|---|
| `campaigns:read` | reading existing campaigns and reading back what a run created |
| `campaigns:write` | creating the campaign, packages, package images, shipping methods and offers; teardown deletes |
| `catalogue:read` | the product and variant ids the packages point at |
| `gateways:read` | gateway groups and the payment method codes a campaign may enable |
| `metadata:read` | the metadata definition audit in `discover` |
| `metadata:write` | `metadata --apply` |

If any of the six is missing, the key has to be re-created with all six;
retrying with the same key does not help. Permission reference:
https://developers.nextcommerce.com/docs/admin-api/permissions

## Write inventory

Read this before running. Every mutation the skill can make, and the one
confirmation that covers it:

| # | Mutation | Covered by |
|---|---|---|
| 1 | `POST /api/admin/campaigns/` (create the campaign) | Phase 4 plan review + `apply --yes --plan-sha256` |
| 2 | `POST /api/admin/campaigns/{id}/packages/` (one per variant) | same |
| 3 | `PUT /api/admin/campaigns/{id}/packages/{id}/image/` (only when the plan sets an override, only on a package this run created) | same |
| 4 | `POST /api/admin/campaigns/{id}/shipping-methods/` | same |
| 5 | `POST /api/admin/campaigns/{id}/offers/` (tiers and vouchers) | same |
| 6 to 9 | `DELETE` offers, shipping methods, packages, campaign | teardown only: manifest-bound, identity read-back, `--yes` |
| 10 | `POST /api/admin/metadata/` (only the missing campaign metadata definitions) | `metadata --apply` after an `AskUserQuestion` |
| 11 | local JSON under the run directory (discovery, plan, manifest, verify report) | no gate |
| 12 | `POST /api/v1/carts/calculate/` on the Cart API in `verify` | none: creates nothing, reads pricing back |

The engine refuses the writes in rows 1 to 5 unless `apply` receives `--yes` and
a `--plan-sha256` equal to the hash of the plan file it is about to send. The
deletes in rows 6 to 9 are gated differently: `teardown` takes
`--manifest --plan --yes` and no hash argument, computing the plan's hash itself
and refusing if it does not match the manifest. There is no `PUT` or `DELETE` on
a package this run did not create, and no write that edits an existing metadata
definition.

---

## Phase 0: Locate the executable

`<skill-dir>` is the directory this skill is installed into, which depends on
the install target: `~/.claude/skills/next-create-campaign` (Claude Code),
`~/.codex/skills/next-create-campaign` (Codex),
`~/.agents/skills/next-create-campaign` (other agents), or the
`next-create-campaign/` folder of a repo checkout.

Every command in this file is written
`bash <skill-dir>/next-create-campaign.sh <subcommand> ...`. Always call it
through `bash`; never rely on the executable bit. Run it from the operator's
project directory, not from the skill directory: that is where `.env` and the
run directory live.

```bash
bash <skill-dir>/next-create-campaign.sh --version
```

This prints the installed version. If the launcher exits 2 with a Python
message, install Python 3.9 or newer, or set `NEXT_CREATE_CAMPAIGN_PYTHON` to
one, then retry.

The full command surface, as a synopsis (run each line as
`bash <skill-dir>/next-create-campaign.sh ...`):

```
next-create-campaign.sh --version
next-create-campaign.sh discover  --store <subdomain> [--out <dir>]
next-create-campaign.sh metadata  --store <subdomain> [--apply]
next-create-campaign.sh recommend --discovery <dir>/discovery.json --hero <product_id> --ctc low|high --anchor-price <decimal> --shipping <code>:<price> [--shipping ...] [--name <campaign name>] [--gateway-group <id>] [--payment-methods a,b] [--express-methods a,b] [--currency USD] [--language en] [--countries US,CA] [--tiers 50,55,60] [--exit 10] [--exit-code CODE] [--bump <variant_id>:<price>] [--upsell <variant_id>:<price>:<pct>] [--free-shipping] [--rounding 0.95] [--statement-descriptor <text>] [--out <dir>]
next-create-campaign.sh plan      --plan <dir>/campaign-plan.json [--check-store]
next-create-campaign.sh apply     --plan <dir>/campaign-plan.json --yes --plan-sha256 <plan-sha256> [--resume <dir>/run-manifest.json] [--out <dir>]
next-create-campaign.sh verify    --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json [--out <dir>]
next-create-campaign.sh teardown  --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --yes
```

`--store` accepts a bare subdomain (`mystore`) or `mystore.29next.store`.

Exit codes, for every subcommand:

| Code | Meaning |
|---|---|
| 0 | success |
| 1 | refused or failed: invalid input, credential missing, a store error, or a verify FAIL |
| 2 | the argument parser rejected the command, the apply gate printed `NOT APPLIED`, or a launcher precondition failed |

Every exit 2 means nothing was sent to the store.

---

## Phase 1: Store and token

### Step 1: Store subdomain

Ask the user:

> Which store gets the new campaign? Provide the subdomain (for example
> `mystore` for mystore.29next.store).

Check the store exists. This request is unauthenticated:

```bash
curl -s -o /dev/null -w "%{http_code}" https://{subdomain}.29next.store/
```

If it returns anything other than `200`, warn the user and ask them to confirm
the subdomain.

### Step 2: Admin API token

The engine reads the token itself, in this order:

1. `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` from the environment. The subdomain is
   upper-cased with hyphens turned into underscores, so `my-store` becomes
   `MY_STORE_NEXT_ADMIN_API_TOKEN`.
2. Otherwise the one line `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN=...` in `.env` in
   the current working directory. The engine reads only that line and never
   executes the file.
3. Otherwise `NEXT_ADMIN_API_TOKEN`.

A value that still looks like a placeholder, such as `<paste-token-here>`, is
rejected.

The token never enters CLI arguments, chat, echoes, scripts or result files. You
never load, export, print or `curl` with it, and there is no separate validation
request: `discover` is the validation (Phase 2). The key needs the six
permissions listed under Prerequisites.

If the store's variable is already set in the environment (check presence
only, never the value), go to Phase 2. `NEXT_ADMIN_API_TOKEN` is used for
whichever store is named, so rely on it only when it belongs to this store.
Otherwise the token lives in `.env` in the current working directory (the
user's project, not the skill checkout), one line per store. The user pastes
the token in with a text editor, so it never touches the chat.

1. If this directory is inside a git repository, make sure the repository's
   `.gitignore` ignores both `.env` and `next-create-campaign-runs/`. Add a line
   for whichever is missing.
2. Create the file (or append the store's line), then lock it so only this user
   can read it (`chmod 600 .env`):

   ```
   # Next Commerce Admin API tokens, one line per store.
   MYSTORE_NEXT_ADMIN_API_TOKEN=
   ```

3. If the value is empty: have the user open `.env` in a text editor (offer to
   open it; the file is hidden in file managers), paste the token after `=`,
   save, and reply "saved".

Do not read the file back to check the value. Go to Phase 2.

---

## Phase 2: Discover

```bash
bash <skill-dir>/next-create-campaign.sh discover --store <subdomain>
```

Read-only. The first request is `GET /api/admin/store/`, so a bad or
under-scoped key stops there with a 401/403 message. That means the key was
rejected or lacks one of the six permissions; the fix is a new key with all six,
and retrying does not help.

After the store, `discover` reads the gateway groups, shipping methods,
catalogue and existing campaigns, probes whether the Offers API is live on this
store, and audits the 11 campaign metadata definitions. It writes
`discovery.json` to `./next-create-campaign-runs/<subdomain>/` under the current
working directory (or `--out <dir>`) and prints a summary. Show the operator the
product table (the hero candidates with their variant ids and prices), the
gateway groups, the shipping methods and the existing campaign names.

### Metadata definitions

A store needs 11 campaign metadata definitions before its campaigns run; without
them, orders lose campaign attribution silently. They are set once per store and
shared by every campaign on it. The list is in `references/offer-doctrine.md`,
section "Metadata definitions". The discovery records:

| Field | Meaning |
|---|---|
| `metadata_checked` | whether the audit ran |
| `metadata_missing` | definitions the store does not have |
| `metadata_conflicts` | keys defined on the wrong object |
| `metadata_error` | null when the audit ran; when it failed, a status and a fixed reason |

Settle these before Phase 4, because `recommend` copies the discovery's metadata
state into the plan's blockers.

- **Missing definitions.** Ask the operator with `AskUserQuestion`:

  > Discovery found {N} of the 11 campaign metadata definitions missing on
  > {subdomain}: {keys}. Without them, orders lose campaign attribution. They
  > are created once per store and shared by every campaign. Create the missing
  > ones now?
  >
  > - A) Yes, create the missing definitions
  > - B) No, stop here (the plan stays blocked until they exist)

  On A, run:

  ```bash
  bash <skill-dir>/next-create-campaign.sh metadata --store <subdomain> --apply
  ```

  It is idempotent, creates only the missing definitions, and needs
  `metadata:write`. Without `--apply` it only reports what it would create.
  Then run `discover` again before `recommend`: a discovery taken before
  `metadata --apply` still lists the definitions as missing and produces a
  blocked plan.

- **Conflicts.** Metadata keys are unique per store, so a key already defined on
  a different object blocks the right definition. The tool never edits an
  existing definition. The operator fixes it by hand in Settings > Metadata,
  then you re-run `discover`. Conflicts block `recommend` until then.

- **Audit failed.** In `metadata_error`, 401 or 403 means the key lacks
  `metadata:read`: create a key with all six permissions and re-run `discover`.
  404 or 405 means the metadata endpoint is not on this store. Either way the
  plan carries a blocker; report the status to the operator rather than working
  around it.

---

## Phase 3: Gather inputs

Collect these via `AskUserQuestion` when the request does not already settle
them:

- **Hero product id**: from the discovery product table.
- **CTC, low or high**: operator judgement, never inferred. Cost-to-consumer is
  what the customer pays. Low CTC gets Buy 1/2/3 tier offers; high CTC gets
  single units plus bumps and upsells.
- **Anchor price**: the package price the tiers discount from. This is not
  always the catalogue price; a catalogue price may already be discounted.
- **Markets**: currency, language and shipping countries (`--currency`,
  `--language`, `--countries`). Defaults come from the store's enabled set. The
  currency cannot be changed after the campaign is created, and the gateway
  group (`--gateway-group`) must list that currency and every payment method
  code the campaign enables.
- **Shipping**: at least one `<code>:<price>`, using a shipping method code
  from the discovery.
- **Bumps and upsells**: each as an explicit variant id, package price and, for
  upsells, voucher percentage. Nothing is inferred from catalogue prices.
- **Standing checkout bump**: ask whether the store requires a bump on every
  campaign, such as a shipping-insurance product. If yes, it goes in as
  `--bump <variant_id>:<price>`.
- **Campaign name** (`--name`): the doctrine uses the hero product name. It must
  not match a campaign that already exists on the store.

The remaining flags have defaults; override them only when the operator asks.
The tier percentages (`--tiers`) default to 50,55,60 off the anchor and the exit
voucher (`--exit`) to 10 percent, both from `references/offer-doctrine.md`. The
others are `--exit-code`, price rounding (`--rounding`: `0.00`, `0.95`, `0.97`
or `0.99`), `--free-shipping`, `--payment-methods`, `--express-methods` and
`--statement-descriptor`.

---

## Phase 4: Recommend and plan review

This phase is the gate.

```bash
bash <skill-dir>/next-create-campaign.sh recommend \
  --discovery ./next-create-campaign-runs/<subdomain>/discovery.json \
  --hero <product_id> --ctc <low|high> --anchor-price <decimal> \
  --shipping <code>:<price> [--name "<campaign name>"] [--countries US,CA] \
  [--bump <variant_id>:<price> ...] [--upsell <variant_id>:<price>:<pct> ...] \
  [--exit 10] [--rounding 0.95] [--free-shipping]
```

`recommend` writes `campaign-plan.json` next to the discovery file. It refuses a
directory that already holds a `run-manifest.json`, because that manifest's plan
is the only file that can resume or tear that campaign down; pass
`--out <new dir>` for a second campaign on the same store.

Then print the plan for review:

```bash
bash <skill-dir>/next-create-campaign.sh plan \
  --plan ./next-create-campaign-runs/<subdomain>/campaign-plan.json --check-store
```

`--check-store` also asks the store whether a campaign with the same name
already exists. `plan` prints the ordered request list, the landed prices table,
the rationale, any blockers, and the plan's SHA-256.

If there are blockers, stop and clear them (see Failure modes), then re-run
`recommend` and `plan`. The engine will not apply a plan with blockers.

Otherwise show the operator the request list and the landed prices, and get an
explicit go/no-go with `AskUserQuestion`:

> Ready to create campaign "{name}" on {subdomain}.29next.store: {N} requests
> ({P} packages, {S} shipping methods, {O} offers). The request list and the
> landed prices are above. Proceed?
>
> - A) Yes, create it exactly as planned
> - B) Change something first (I re-run `recommend` and `plan` and show you the
>   new plan)
> - C) No, stop here (nothing has been written to the store)

Halt on C. Stopping after `plan` writes nothing remote. Any change to the plan
changes its SHA-256 and needs a fresh `plan` and a fresh approval. Commands in
this file write the hash as `<plan-sha256>`; substitute the value the latest
`plan` printed, never one from an earlier plan.

---

## Phase 5: Apply

Only after an A at the gate:

```bash
bash <skill-dir>/next-create-campaign.sh apply \
  --plan ./next-create-campaign-runs/<subdomain>/campaign-plan.json \
  --yes --plan-sha256 <plan-sha256>
```

Emit a `TodoWrite` with the resources being created. The engine journals every
id the moment it exists in `run-manifest.json` next to the plan, written
atomically with mode 600. That manifest holds the campaign api_key; never print
it.

Two different failure shapes, and they need different handling:

**It stops.** Any failure creating a campaign, package, shipping method or offer
halts the run there, leaving the manifest intact. The options are
`--resume <manifest>` after fixing the cause, or `teardown`. Surface that choice
to the operator; do not retry blindly.

**It finishes, then reports.** Offers and package images degrade instead of
halting, because neither is worth stranding a half-built campaign over. The run
creates everything it can, writes `completed_at`, and *then* exits non-zero with
every problem listed together. The campaign is real and usable at that point, so
read the message rather than assuming a rollback:

- Offers answering 404/405 means this store does not take offers over the API.
  Everything else exists; build the offers in the dashboard (Offers & Discounts).
- Package images answering 404 or 405 on the first attempt means the store's build
  predates them. Packages keep whatever image the catalogue supplied; set overrides
  in the dashboard. A 404 on one package *after* another has succeeded is read as
  that package being gone, not as the endpoint missing: it is marked `failed`, not
  `unsupported`, and the rest still go.
- A package image left `pending` (a 429, a 5xx, or no answer at all) is retried by
  `--resume` against the same plan.
- A rejected package image is terminal, and `--resume` will not fix it. The
  `src` cannot change without changing the plan hash, which a resume refuses and a
  fresh run refuses as a duplicate campaign. Set that image in the dashboard, or
  teardown and recreate from a corrected plan. Tell the operator this plainly
  instead of sending them to `--resume`.

To resume after fixing the cause, pass the same plan and hash with the manifest:

```bash
bash <skill-dir>/next-create-campaign.sh apply \
  --plan ./next-create-campaign-runs/<subdomain>/campaign-plan.json \
  --yes --plan-sha256 <plan-sha256> \
  --resume ./next-create-campaign-runs/<subdomain>/run-manifest.json
```

A resume refuses a changed plan, and refuses a destination that holds a
different run.

To remove what the run created instead, ask the operator first with
`AskUserQuestion`:

> Teardown deletes what this run created on {subdomain}: campaign {id} with its
> offers, shipping methods and packages. It reads each one back first and
> refuses if anything does not match the manifest. Delete them?
>
> - A) Yes, tear it down
> - B) No, keep it

On A:

```bash
bash <skill-dir>/next-create-campaign.sh teardown \
  --manifest ./next-create-campaign-runs/<subdomain>/run-manifest.json \
  --plan ./next-create-campaign-runs/<subdomain>/campaign-plan.json --yes
```

Teardown takes no hash argument: it computes the plan's hash itself and refuses
if it does not match the manifest. It deletes only what the manifest records,
reading each object back and checking its identity first, with the campaign
last.

---

## Phase 6: Verify and hand off

```bash
bash <skill-dir>/next-create-campaign.sh verify \
  --manifest ./next-create-campaign-runs/<subdomain>/run-manifest.json \
  --plan ./next-create-campaign-runs/<subdomain>/campaign-plan.json
```

`verify` reads every resource back and calls `carts/calculate` for each tier, a
mixed-variant cart, the exit voucher and shipping, comparing totals to the cent.
It writes `verify-report.json` next to the plan and exits 1 on FAIL. Report PASS
or FAIL, with the failing checks. Then hand off:

- Campaign id, and the manifest path where the full api_key lives (gitignored,
  never echoed). Point the operator at the file; do not read the key into chat.
- Package ids for `data-next-package-id` in the funnel markup.
- Offer codes for the funnel's voucher wiring.
- Manual dashboard steps the API does not cover: Allowed Domains
  (Development/Production), PayPal account linking, Map Builder.
- For the funnel itself, `next-campaigns-setup` scaffolds the campaign-page-kit
  project that takes this api_key and these package ids.

Clean up: run `unset NEXT_ADMIN_API_TOKEN` if it was exported during the
session. Keep `.env`; it holds the per-store tokens for future runs. Keep the
run directory too: its manifest is what `--resume`, `verify` and `teardown`
need.

---

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `credential missing` | no token found for this store in the environment, `.env` or `NEXT_ADMIN_API_TOKEN` | add the `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` line to `.env` (Phase 1); never paste it in chat |
| `still holds a placeholder` | the token value is a placeholder such as `<paste-token-here>` | have the user paste the real token over it in a text editor |
| 401 or 403 from the store | key rejected, or missing one of the six permissions | re-create the key with all six under Dashboard > Settings > API Access; retrying does not help |
| launcher exits 2 with a Python message | no Python 3.9 or newer found | install Python 3.9 or newer, or set `NEXT_CREATE_CAMPAIGN_PYTHON` |
| `NOT APPLIED: pass --yes --plan-sha256` | the gate | re-run `plan`, copy the hash, pass it to `apply` |
| `plan has blockers` | metadata missing or conflicting, or a stale discovery after `metadata --apply` | run `metadata --apply` if needed, re-run `discover`, re-run `recommend` |
| `a campaign named ... already exists` | name collision | rename in the plan (re-run `recommend` with `--name`), or resume that run's manifest |
| `already holds a run` | `recommend` into a directory that has a `run-manifest.json` | pass `--out <new dir>` |
| `already holds a different run` | `--resume` or `--out` points at another run's manifest | resume with that run's own plan and manifest, or choose a new `--out` |
| `not gitignored` | the run directory is inside a git repository that does not ignore it | add `next-create-campaign-runs/` to `.gitignore`, or pass `--out` outside the repository |
| `cannot confirm ... is safe to write` | a `.git` directory was found but git could not be asked | install git, or pass `--out` outside the repository |
| offers POST returns 404/405 | store's Offers API not live | campaign, packages and shipping are created; build offers in the dashboard |
| image PUT returns 404/405 | store's build predates package images | everything is created; packages keep the catalogue image, set overrides in the dashboard |
| image PUT rejected (`image_status: failed`) | bad `src`, unreachable URL, wrong type, over 10 MB or 25 MP | terminal: `--resume` cannot help, the hash pins the `src`; fix in the dashboard, or teardown and recreate |
| `verify` fails `package ... image` | the catalogue image fetch failed silently at create | add an `image.src` override and recreate, or set it in the dashboard and accept the row |
| apply stopped mid-run | any non-2xx | `apply ... --resume <manifest>` after fixing, or `teardown` |
| `identity mismatch` on teardown | the manifest does not match what is live | do not force; investigate which campaign the manifest points at |

---

## Invariants

- Never scope a tier offer to `all_packages`; always name the hero package ids.
- Never create quantity packages (`2x ...`); tiers are offers.
- Never infer CTC or the anchor price; both come from the operator.
- Never touch a campaign the run manifest does not own. Teardown deletes only
  what this run created, after reading each object back.
- Never PUT an image on a package this run did not create, and never use the
  DELETE image route. Both are outside what this skill does.
- Never put base64 in a plan. A package image is an https URL, and it should be a
  durable public one: whatever goes in `src` is printed at the gate and stored in
  the plan and the manifest.
- Never print or paste the Admin token or the full campaign api_key.
- A token that reached a chat or ticket is rotated after the work.
- Never edit an existing metadata definition. `metadata --apply` only creates
  missing ones.

---

## Output files

`discover` writes to `./next-create-campaign-runs/<subdomain>/` under the current
working directory. `recommend` writes next to the discovery file it reads;
`apply` and `verify` write next to the plan file. `--out` overrides each.

| File | Written by | Holds |
|---|---|---|
| `discovery.json` | `discover` | the store snapshot: catalogue, gateway groups, shipping methods, existing campaigns, the Offers API probe and the metadata audit |
| `campaign-plan.json` | `recommend` | the campaign, packages, shipping methods and offers `apply` will create, plus the rationale and blockers |
| `run-manifest.json` | `apply` | every id the run created and its status, plus the campaign api_key |
| `verify-report.json` | `verify` | every read-back and cart check, with PASS or FAIL |

One directory per campaign run. `recommend` refuses a directory that already
holds a `run-manifest.json` (pass `--out <new dir>` for a second campaign on the
same store), and a resume refuses a destination holding a different run.

If the directory is inside a git repository it must be gitignored, or the
engine refuses to write, because the manifest holds the campaign api_key.
`run-manifest.json` is written atomically with mode 600 and is the only file
holding a live secret.

---

## Staying up to date

- The installed version is the `version:` line in this file's frontmatter, also
  printed by `bash <skill-dir>/next-create-campaign.sh --version`.
- From a checkout of `NextCommerceCo/skills`,
  `git pull --ff-only && ./skills.sh status` reports `stale` when a newer
  version exists, and
  `./skills.sh install <claude|codex|agents|all> next-create-campaign` updates
  it. Use `--force` only after reviewing a `modified` row.
- Without a checkout, run `npx skills update`.
- To check for a newer version without a checkout, compare `--version` with the
  `version` of the `next-create-campaign` entry in
  https://raw.githubusercontent.com/NextCommerceCo/skills/main/skills.json.
- Release notes and breaking changes live in the merged pull request that
  carried each version bump, titled `next-create-campaign X.Y.Z: ...` (the first
  one is `Add next-create-campaign public skill`). They are listed at
  https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-create-campaign.
- There are no tags, releases or notifications today, so check before starting a
  campaign.

---

## References

- [`references/offer-doctrine.md`](references/offer-doctrine.md): the offer
  rules `recommend` encodes, including the section "Metadata definitions".
- [`references/admin-api-contract.md`](references/admin-api-contract.md): the
  API contract the engine implements.
- [`examples/campaign-plan.example.json`](examples/campaign-plan.example.json):
  an example of the plan `recommend` writes.
- Campaigns Admin API: https://developers.nextcommerce.com/docs/campaigns/admin-api
- Admin API permissions: https://developers.nextcommerce.com/docs/admin-api/permissions

| Subcommand | Method | Endpoint |
|---|---|---|
| `discover` | GET | `/api/admin/store/`, `/api/admin/gateway-groups/`, `/api/admin/shipping-methods/`, `/api/admin/products/`, `/api/admin/campaigns/`, `/api/admin/campaigns/{id}/offers/` (the Offers API probe), `/api/admin/metadata/` |
| `metadata --apply` | POST | `/api/admin/metadata/` |
| `apply` | POST, PUT | rows 1 to 5 of the write inventory |
| `verify` | GET, POST | campaign read-back, then `/api/v1/carts/calculate/` on the Cart API |
| `teardown` | GET, DELETE | read-back, then rows 6 to 9 of the write inventory |

API version: `2024-04-01`. The metadata POST is the one request sent without the
version header; the API gotcha in `references/offer-doctrine.md` explains why.
