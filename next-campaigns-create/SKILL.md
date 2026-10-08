---
name: next-campaigns-create
version: 1.1.0
description: |
  Create and update a Campaigns App campaign over the NEXT Admin API. Create:
  read the store's catalogue, gateway groups and shipping methods, recommend a
  campaign structure from the offer doctrine, show the operator every request
  and the landed prices, then create the campaign, its packages, shipping
  methods and offers in one approval-gated run. Update: take ownership of an
  existing campaign with adopt, show a three-way diff of an edited plan against
  the store, then apply that reviewed change set. Hands back the campaign
  api_key and the package ids the funnel needs. Offer kinds: quantity Buy 1/2/3,
  buy-X-get-Y as a labeled percentage approximation, and gift-with-purchase as
  a gift package plus a gift-scoped 100% offer. Also clone an existing campaign
  through a separate approval gate, preserving its funnel reference IDs.

  Use when: "create a campaign for {store}", "set up the campaign in the
  Campaigns App", "provision a campaign over the API", "recommend a campaign
  structure", "build the offers for {product}", "buy X get Y", "BOGO",
  "gift with purchase", "change the price", "change the tier percentages",
  "pause that offer", "add an offer", "update a campaign", "change a package
  price", "change the shipping price", "retire an offer", "add a bump to an
  existing campaign", "adopt an existing campaign", "clone a campaign", "copy campaign 42", or when a
  campaign needs to exist, or needs changing, on a store before funnel work starts.
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

# /next-campaigns-create: Provision a Campaigns App campaign over the Admin API

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-campaigns-create` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Creates a Campaigns App campaign on a NEXT store over the Admin API, changes one
that already exists, and hands back the campaign api_key and the package ids the
funnel needs. The bundled engine (`scripts/campaign_admin.py`, run through the
`next-campaigns-create.sh` launcher) owns the API contract, the approval gate,
the run manifest and the safety checks. This skill drives it and makes the
operator decisions the engine refuses to guess.

If this file and the engine ever disagree, the engine wins for behaviour and
`references/admin-api-contract.md` wins for the API contract. Offer reasoning
is in `references/offer-doctrine.md`; worked numbers for quantity, buy-X-get-Y
and gift-with-purchase are in `references/worked-examples.md`. The full update
procedure is in `references/update-path.md`.

---

## Scope

This skill creates a campaign, clones one, and changes a campaign it owns. Four paths:

- **Create** (Phases 1 to 6): `discover`, `recommend`, `plan`, `apply`,
  `verify`, `teardown`.
- **Edit in place** (Phase 7): `edit`, on a campaign a run of this skill
  created. One or more field changes, previewed and hashed, with a receipt and
  `--undo`.
- **Update** (U1 to U6, summarised below and written out in
  `references/update-path.md`): `adopt`, `diff`, `update`, then `verify`, for a
  campaign that already exists or a change the edit ops do not cover.
- **Clone** (the section after the write inventory): `clone`, then `verify`
  and `teardown` without `--plan`, for a copy of an existing campaign that
  keeps its funnel reference ids. To change the copy afterwards, adopt it by
  its new id and use the update path.

The run manifest is the boundary for all four: the engine never writes to a
campaign, package, shipping method or offer the manifest does not record. `edit`
changes only objects in it, `teardown` removes only objects in it, and an offer
someone added in the dashboard is listed and left alone. A campaign this skill
created is owned by its run directory. A campaign built in the dashboard, or one
whose run directory was lost, becomes owned only through
`adopt --store <subdomain> --campaign <id>`, by id, after the operator confirms
the campaign's id, name and created_at. Nothing adopts a campaign implicitly, and `adopt` refuses a directory
that already holds a manifest.

Teardown and recreate is the fallback for the few fields neither path can change,
never the first offer: it mints a new campaign id and api_key and removes offers
this run does not own.

Still outside this skill: deleting a campaign it did not create (`teardown`
refuses an adopted manifest, so that is a dashboard action), and per-customer
limits or date ranges on an offer, which are not offer fields at all.

Boundary with other campaign skills:
- Use this skill to make the campaign exist on the store and to change it
  afterwards: the campaign, its packages, shipping methods and offers, plus the
  api_key and package ids that come out of creating them.
- Use `next-campaigns-setup` after this skill to scaffold the campaign-page-kit
  project. It consumes the api_key and package ids this skill produces.
- Use the Campaigns OS skills, starting with `next-campaigns-os`, for
  CampaignSpec lifecycle work. This skill does not read or write a CampaignSpec
  and builds no pages.
- The API does not cover Allowed Domains, PayPal account linking or Map Builder.
  Phase 6 hands those to the operator as dashboard steps.
- A change made in the dashboard is not a problem for this skill. `diff` reads
  the store every time and preserves a field the candidate plan did not touch,
  so a dashboard edit is kept rather than overwritten. Only a field edited on
  both sides, to different values, refuses.

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

The Campaign Cart API is a different host with a different credential. For creation manifests, `verify`
calls `POST https://campaigns.apps.29next.com/api/v1/carts/calculate/` with the
campaign api_key sent raw in the `Authorization` header: no scheme prefix, and
never the Admin token. The engine builds both kinds of request; do not
hand-craft either one.

## Prerequisites

- **bash and Python 3.9 or newer.** `NEXT_CAMPAIGNS_CREATE_PYTHON` can point the
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
  API Access, with all seven of these permissions:

| Permission | Used for |
|---|---|
| `store:read` | the store's enabled currencies and languages; the first request `discover` sends |
| `campaigns:read` | reading existing campaigns and reading back what a run created |
| `campaigns:write` | creating the campaign, packages, package images, shipping methods and offers; the in-place edits and their undo; the update path's PATCH, POST, PUT and DELETE; teardown deletes |
| `catalogue:read` | the product and variant ids the packages point at |
| `gateways:read` | gateway groups and the payment method codes a campaign may enable |
| `metadata:read` | the metadata definition audit in `discover` |
| `metadata:write` | `metadata --apply` |

If any of the seven is missing, the key has to be re-created with all seven;
retrying with the same key does not help. The store's shipping methods list
needs no permission of its own. A 401 or 403 from the engine names the
permission the failing request needed. Permission reference:
https://developers.nextcommerce.com/docs/admin-api/permissions

Changing a campaign that already exists needs less than that, because every
request those commands send is on `/api/admin/campaigns/`: `adopt` and `diff`
need `campaigns:read` alone, and `edit` (its undo and a rollback included) and
`update` need `campaigns:read` and `campaigns:write`. None of the four touches
the store settings, the catalogue, the gateway groups or the metadata
definitions. This does not loosen anything above: `discover` still needs all
seven, so that is the key an operator creates, and it already covers everything
`edit`, `adopt`, `diff` and `update` send. Never ask for a second key to take
over or change a campaign. The per-command breakdown is in
`references/admin-api-contract.md`.

## Platform uniqueness rules

A campaign takes **one package per variant** and **one campaign shipping
method per store code**.
A different price for the same product or the same shipping method is an offer
or voucher, never a second package or method. The engine applies both rules in
`recommend`, `plan` and `apply` on every store, so a plan that breaks them fails
before any request. `verify` and `teardown` do not apply them, so a run created
before the rules can still be read back, priced and removed.

The platform enforces the same rules in its dashboard and, on newer releases,
in the API. The engine's check does not depend on which release the store runs.
The details are in
[the Admin API contract](references/admin-api-contract.md#field-notes-and-live-gotchas).

## Write inventory

Read this before running. Every mutation the skill can make, and the one
confirmation that covers it:

| # | Mutation | Covered by |
|---|---|---|
| 1 | `POST /api/admin/campaigns/` (create the campaign) | Phase 4 plan review + `apply --yes --plan-sha256` |
| 2 | `POST /api/admin/campaigns/{id}/packages/` (one per variant) | same |
| 3 | `PUT /api/admin/campaigns/{id}/packages/{id}/image/` (only when the plan sets an override, only on a package the manifest owns) | same; on a campaign that already exists it is a PUT op in the change set, under the same gate as rows 13 to 22 |
| 4 | `POST /api/admin/campaigns/{id}/shipping-methods/` | same |
| 5 | `POST /api/admin/campaigns/{id}/offers/` (tiers and vouchers) | same |
| 6 to 9 | `DELETE` offers, shipping methods, packages, campaign | teardown only: manifest-bound, identity read-back, `--yes`; refused on an adopted campaign |
| 10 | `PATCH /api/admin/campaigns/{id}/packages/{id}/` (the `prices[]` rows, on a package this run created) | Phase 7 edit preview + `edit --yes --edit-sha256 --live-traffic` |
| 11 | `PATCH /api/admin/campaigns/{id}/offers/{id}/` (benefit value and rounding, the condition, `available`, on an offer this run created) | same |
| 12 | `POST /api/admin/campaigns/{id}/offers/` (an offer added to a campaign this run created) | same |
| 13 | `PATCH /api/admin/campaigns/{id}/` (name, language, gateway group, payment and express method lists, shipping countries, additional currencies, statement descriptor, PayPal account id; never `currency`) | U4 change-set review + `update --yes --change-set-sha256` |
| 14 | `PATCH /api/admin/campaigns/{id}/packages/{id}/` (name, `prices[]` for the campaign currency only, recurring interval) | same |
| 15 | `POST /api/admin/campaigns/{id}/packages/` on a campaign that already exists | same |
| 16 | `DELETE /api/admin/campaigns/{id}/packages/{id}/` | same, plus `--allow-delete` |
| 17 | `PATCH /api/admin/campaigns/{id}/shipping-methods/{id}/` (`prices[]` for the campaign currency only) | same as row 13 |
| 18 | `POST /api/admin/campaigns/{id}/shipping-methods/` on a campaign that already exists | same as row 13 |
| 19 | `DELETE /api/admin/campaigns/{id}/shipping-methods/{id}/` | same as row 13, plus `--allow-delete` |
| 20 | `PATCH /api/admin/campaigns/{id}/offers/{id}/` (name, type, code, `available`, and the whole `condition` and `benefit` when either changes) | same as row 13 |
| 21 | `POST /api/admin/campaigns/{id}/offers/` on a campaign that already exists | same as row 13 |
| 22 | `DELETE /api/admin/campaigns/{id}/offers/{id}/` | same as row 13, plus `--allow-delete` |
| 23 | `POST /api/admin/metadata/` (only the missing campaign metadata definitions) | `metadata --apply` after an `AskUserQuestion` |
| 24 | local JSON under the run directory: discovery, plan, manifest, verify report, the edit receipts `edit-<n>-receipt.json`, `change-set.json`, the merged plan `campaign-plan.<sha8>.json` and the archived plans beside it, `.run.lock` | no gate |
| 25 | `POST /api/v1/carts/calculate/` on the Cart API in `verify` | none: creates nothing, reads pricing back |
| 26 | `GET` of the public `skills.json` on GitHub, cached in `${XDG_CACHE_HOME:-~/.cache}/next-skills/catalog.json`, in `check-update` | none: read-only, no credentials, skipped with `NEXT_SKILLS_NO_UPDATE_CHECK=1` |
| 27 | bodyless `POST /api/admin/campaigns/{source_id}/clone/` | clone preview + `clone --yes --clone-sha256` |
| 28 | `PATCH /api/admin/campaigns/{new_id}/` with only `name`, on the campaign row 27 just created | same clone approval, only when a name was requested |

The engine refuses the writes in rows 1 to 5 unless `apply` receives `--yes` and
a `--plan-sha256` equal to the hash of the plan file it is about to send. The
deletes in rows 6 to 9 are gated differently: `teardown` takes
`--manifest --plan --yes` and no hash argument, computing the plan's hash itself
and refusing if it does not match the manifest.

Rows 10 to 12 are refused unless `edit` receives `--yes`, an `--edit-sha256`
equal to the hash its own preview prints, and `--live-traffic yes` or `no`. That
hash covers the plan, the edit file and the live state of every object the edit
will write, so a change to any of them needs a fresh preview and a fresh
approval. Finishing or rolling back an edit that stopped partway takes `--yes`
and the hash only: it continues under the answer already recorded in that edit's
receipt.

Rows 13 to 22, and row 3 on a campaign that already exists, have two gates.
`update` sends nothing without `--yes` and a `--change-set-sha256` equal to the
hash of the `change-set.json` the operator read, and a change set carrying any
DELETE needs `--allow-delete` on top of that. Both refusals are exit 2 before
the first request. `adopt` and `diff` send no writes at all.

Row 24 also covers the edit receipts, which hold before-images and no secret.

There is no `PUT` or `DELETE` on a package the run manifest does not own, no
delete-image route in use, and no write that edits an existing metadata
definition.

Rows 27 and 28 are refused unless `clone` receives `--yes` and a `--clone-sha256`
equal to the hash of its own preview, taken over a fresh read of the source.
Clone sends its POST to the source route, but the server creates independent
destination objects and leaves the source unchanged. Clone `verify` and
`teardown` omit `--plan` and validate the saved clone approval and destination
inventory instead; `edit`, `diff` and `update` refuse a clone manifest, and the
copy is changed by adopting it by its new id.

---

## Clone an existing campaign

Use `clone` when the operator wants a copy of an existing campaign on the same
store. It is separate from `apply` and does not create a campaign plan. Take
the source ID and requested name from the operator's request, then preview:

```bash
bash <skill-dir>/next-campaigns-create.sh clone --store <slug> --source <id> --out <run-dir>
```

Add `--name '<requested name>'` if the operator wants a rename. The server first
names the copy `{source name}-COPY`; setting a name requires a second request,
a PATCH to the new campaign. The preview lists copied settings and resource
rows, including offer codes, followed by the intended requests. It reads every
page and each offer's detail so package scope is part of approval. Unavailable
offers still copy; removed offers do not.

Show the preview and obtain approval for its exact content. Run the generated
command with both `--yes` and `--clone-sha256 <preview hash>`. A missing or stale
hash exits 2 without creating a directory, lock or manifest. The final source
read must still match the approved hash. The API has no revision precondition,
so a change between that read and the clone transaction can still happen;
verification checks the resulting copy against the approved snapshot.

Route availability depends on the store's deployment. A successful source GET
does not prove that the clone endpoint exists. An approved POST returning 404
or 405 stops with deployment guidance. Do not infer availability from a closed
issue or a merged change, and do not make a trial clone outside the approval gate.

A clone gets a new campaign ID and key while preserving the package, shipping
and offer reference IDs used by funnel pages. Discount codes and package scope
copy unchanged, including `all_packages`. Prices in the campaign currency copy
exactly. When the source lacks a supported additional-currency price, the server
can derive it by forex; the report identifies those prices separately. Server
usage counters reset and retail price/quantity is disabled. The report treats
unexposed resets as documented behavior, not verified facts.

### Interrupted clones

Resume with the same store, source, requested name and approval hash, adding
`--resume <run-dir>/run-manifest.json`. Keep the saved approval; do not replace it
with a new source snapshot after a POST may have been sent. A confirmed copy
resumes its rename or inventory reads. A rename that already landed needs no
second PATCH. An uncertain clone POST is never sent again automatically.

Recovery excludes preexisting IDs and reads the whole campaign list. It can
adopt only one matching copy within the persisted attempt window, from 60
seconds before sending to the original request timeout plus 60 seconds. It
then checks that candidate's identity and full copied content. Zero candidates,
multiple candidates or a missing window require manual resolution. A copy
created after the saved deadline cannot be adopted on a later resume. Another
operator could create an indistinguishable copy within the same window; the
API supplies no request marker or idempotency key to prove ownership.

The protected manifest saves the destination ID immediately after a valid 201.
A later resource read failure leaves incomplete inventory, which resume can
refresh without another POST. If enumeration succeeds but parity fails, the
copy remains recorded and can be torn down. Report the mismatch to the operator.

### Verify, teardown and handoff

```bash
bash <skill-dir>/next-campaigns-create.sh verify --manifest <run-dir>/run-manifest.json
bash <skill-dir>/next-campaigns-create.sh teardown --manifest <run-dir>/run-manifest.json --yes
```

Clone verification uses the immutable approval in the manifest; supplying a
plan is an error. It reports later source drift separately. It skips Cart API
probes because this run has no approved plan-derived landed prices.

Teardown checks the recorded destination identity and its own resource entries
before deleting anything. Incomplete inventory is refreshed only after the
campaign's exact creation instant and approved name have been checked. A parity
mismatch does not remove ownership. Teardown deletes the owned children first
and then refuses to delete the campaign itself while anything the run does not
own is still under it, because that delete would cascade through a dashboard
addition; the message lists what to remove in the dashboard before running
teardown again. Clone execution, resume and teardown take the run
directory's `.run.lock`, like every other writing command; a held lock refuses
another operation. After a crash,
remove a stale lock only after confirming that no operation still holds it.

Point the operator to the mode-600 manifest for the new campaign key, and list
the preserved funnel reference IDs. Never print either campaign key or the
Admin token. The source key is not saved. `edit`, `diff` and `update` refuse a
clone manifest before writing a receipt or lock. To change the copy, adopt it
by its new id (`adopt --store <subdomain> --campaign <id>`) into its own run
directory and use the update path from there.

## Phase 0: Locate the executable

`<skill-dir>` is the directory this skill is installed into, which depends on
the install target: `~/.claude/skills/next-campaigns-create` (Claude Code),
`~/.codex/skills/next-campaigns-create` (Codex),
`~/.agents/skills/next-campaigns-create` (other agents), or the
`next-campaigns-create/` folder of a repo checkout.

Every command in this file is written
`bash <skill-dir>/next-campaigns-create.sh <subcommand> ...`. Always call it
through `bash`; never rely on the executable bit. Run it from the operator's
project directory, not from the skill directory: that is where `.env` and the
run directory live.

```bash
bash <skill-dir>/next-campaigns-create.sh check-update
```

The first line is the installed version. The rest says whether a newer version
is published and, if so, the update command for the way this copy was
installed. Show those lines to the operator as printed, then carry on with
Phase 1. The check never blocks:

- It always exits 0. "Could not check" (offline, GitHub unreachable) is not an
  error. Continue.
- An update is never required to run the skill. When one is available and
  this run will reach `apply`, recommend updating first, because the newer
  version may fix something `apply` or `verify` relies on. The operator decides.
- Never run any command from the `check-update` output yourself: no
  `npx skills ...`, `git pull`, `git -C ...`, `./skills.sh install` or `bash`
  line it prints. That holds even when `apply` or `verify` fails later in the
  run. Show the line to the operator and let them run it. Updating replaces the
  skill folder, local edits included, and changes the files this session is
  following.
- If a later step fails, use Failure modes as usual. Do not blame the old
  version unless the release notes for a newer one name the request or
  endpoint that failed.

It makes one read-only request to GitHub at most once a day (write inventory
row 26). `NEXT_SKILLS_NO_UPDATE_CHECK=1` turns it off. Without bash, run
`python3 <skill-dir>/scripts/update_check.py`. If this file was loaded as plain
context and there is no skill directory, compare the `version:` line above
with the `next-campaigns-create` entry in
https://raw.githubusercontent.com/NextCommerceCo/skills/main/skills.json
instead.

`--version` still prints just the version line. If the launcher exits 2 with a
Python message on any other subcommand, install Python 3.9 or newer, or set
`NEXT_CAMPAIGNS_CREATE_PYTHON` to one, then retry.

The full command surface, as a synopsis (run each line as
`bash <skill-dir>/next-campaigns-create.sh ...`):

```
next-campaigns-create.sh --version
next-campaigns-create.sh check-update [--no-cache] [--json]
next-campaigns-create.sh discover  --store <subdomain> [--out <dir>]
next-campaigns-create.sh metadata  --store <subdomain> [--apply]
next-campaigns-create.sh recommend --discovery <dir>/discovery.json --hero <product_id> --ctc low|high --anchor-price <decimal> --shipping <code>:<price>[:<key>] [--shipping ...] [--offer-type quantity|bxgy|gwp] [--paid-qty <n> --free-qty <n>] [--gift <variant_id>:<price>[:<qty>]] [--gift-mode auto|select] [--name <campaign name>] [--gateway-group <id>] [--payment-methods a,b] [--express-methods a,b] [--currency USD] [--language en] [--countries US,CA] [--tiers 50,55,60] [--exit 10] [--exit-code CODE] [--short-name <product_id>:<NAME>] [--bump <variant_id>:<price>] [--upsell <variant_id>:<price>:<pct>] [--free-shipping | --free-shipping-min-qty <n>] [--rounding 0.95] [--statement-descriptor <text>] [--out <dir>]
next-campaigns-create.sh plan      --plan <dir>/campaign-plan.json [--check-store]
next-campaigns-create.sh apply     --plan <dir>/campaign-plan.json --yes --plan-sha256 <plan-sha256> [--resume <dir>/run-manifest.json] [--out <dir>]
next-campaigns-create.sh verify    --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json [--out <dir>]
next-campaigns-create.sh edit      --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --changes <dir>/campaign-edit.json [--yes --edit-sha256 <edit-sha256> --live-traffic yes|no]
next-campaigns-create.sh edit      --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --undo <dir>/edit-<n>-receipt.json [--yes --edit-sha256 <edit-sha256> --live-traffic yes|no]
next-campaigns-create.sh teardown  --manifest <dir>/run-manifest.json --plan <dir>/campaign-plan.json --yes
next-campaigns-create.sh adopt     --store <subdomain> --campaign <id> [--out <dir>] [--convert-scope <offer_id>]
next-campaigns-create.sh diff      --plan <dir>/campaign-plan.next.json --manifest <dir>/run-manifest.json [--out <dir>] [--delete-changed <section>:<key>]
next-campaigns-create.sh update    --plan <dir>/campaign-plan.<sha8>.json --manifest <dir>/run-manifest.json --change-set <dir>/change-set.json --yes --change-set-sha256 <change-set-sha256> [--allow-delete] [--resume | --settle]
```

`--store` accepts a bare subdomain (`mystore`) or `mystore.29next.store`.
`--campaign` takes an id, never a name: campaign names are not unique on a
store. `--convert-scope` and `--delete-changed` are repeatable.

Exit codes, for every subcommand:

| Code | Meaning |
|---|---|
| 0 | success; `check-update` always exits 0. `diff` also exits 0 when there is nothing to change |
| 1 | refused or failed: invalid input, credential missing, a store error, a verify FAIL, an `adopt` with blockers, every `edit` refusal that is not the gate (an op the engine cannot do in place, a live value that drifted from the plan, a read that cannot prove the change, a write the store rejected), any `diff` refusal (conflict, unmanaged object, a delete the store changed, a name swap), and the `update` refusals that are not gates: the ownership read-back, a manifest the run cannot use, every `--resume` or `--settle` refusal that reads the store, and a write the store rejected |
| 2 | a gate refused: `apply`, `edit` or `update` printed a line starting `NOT APPLIED` and sent nothing. The argument parser rejecting the command and a launcher precondition failing are also 2, and print no such line |

Every exit 2 means nothing was sent to the store. Every gate in the engine prints
a `NOT APPLIED` line, in one of four forms, all on stderr:

- `apply`, under the plan printout: `NOT APPLIED: pass --yes --plan-sha256 <hash
  above> to approve this exact plan.` The words `<hash above>` are literal; the
  hash itself is in the printout above the line.
- `update`'s hash gate, under the change-set printout: `NOT APPLIED: pass --yes
  --change-set-sha256 <the hash> to approve this exact change set.` Here the real
  hash is in the line, so it can be copied straight into the command.
- every other `update` gate: `ERROR: <the reason>` and then `NOT APPLIED: nothing
  was sent to the store.` on the next line.
- `edit`: `NOT APPLIED: nothing was written.` and then either `Approve this exact
  change with:` plus the command to re-run with `--yes --edit-sha256 <the hash>`,
  or, once the hash is right, the request for `--live-traffic yes` or
  `--live-traffic no`. On the gate of an edit that stopped partway, one sentence
  sits between the two: `This continues the edit that was already approved.`

The two exit 2s with no `NOT APPLIED` line are the argument parser rejecting the
command and a launcher precondition (no usable Python, a missing engine, no
subcommand at all); each prints its own message and runs nothing.

The edit gate is the one that reads from the store first: its preview shows live
values, so it needs the token. It is exit 2 for a missing `--yes`, a wrong
`--edit-sha256`, and a first edit without `--live-traffic yes|no`.

The update gate's exit 2 is the whole gate family: a missing `--yes` or a wrong
`--change-set-sha256`, a change set bound to another run, campaign or plan, an op
whose route or id the manifest does not own, a `--plan` that is not the merged
plan the change set names, a DELETE without `--allow-delete`, the baseline drift
refusal (`campaign changed since you reviewed the diff`), and a `--resume` or
`--settle` naming the wrong change set. The ownership read-back is **not** in
that family: it exits 1, with nothing sent.
`references/update-path.md` (Refusals) states the code for every refusal by
message and is the authority; this table summarises it.

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
request: `discover` is the validation (Phase 2). The key needs the seven
permissions listed under Prerequisites.

If the store's variable is already set in the environment (check presence
only, never the value), go to Phase 2. `NEXT_ADMIN_API_TOKEN` is used for
whichever store is named, so rely on it only when it belongs to this store.
Otherwise the token lives in `.env` in the current working directory (the
user's project, not the skill checkout), one line per store. The user pastes
the token in with a text editor, so it never touches the chat.

1. If this directory is inside a git repository, make sure the repository's
   `.gitignore` ignores both `.env` and `next-campaigns-create-runs/`. Add a line
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
bash <skill-dir>/next-campaigns-create.sh discover --store <subdomain>
```

Read-only. The first request is `GET /api/admin/store/`, which needs
`store:read`, so a bad or under-scoped key stops there with a 401/403 message.
The message names the permission that request needed. The fix is a new key with
all seven permissions, and retrying does not help. Do not tell the operator to
grant full access: the seven are enough.

After the store, `discover` reads the gateway groups, shipping methods,
catalogue and existing campaigns, probes whether the Offers API is live on this
store, and audits the 11 campaign metadata definitions. It writes
`discovery.json` to `./next-campaigns-create-runs/<subdomain>/` under the current
working directory (or `--out <dir>`) and prints a summary. Show the operator the
product table (the hero candidates with their variant ids and prices), the
gateway groups, the shipping methods and the existing campaign names.

### Metadata definitions

A store running the Campaigns App should carry 11 campaign metadata definitions,
set once per store and shared by every campaign on it. They are not a checkout
requirement: Campaign Cart sends the attribution values with the order either
way. What the definitions add is visibility. Without them the values are not
first-class in the dashboard, so nobody can filter, export or report on them,
and nobody can see which campaign created an order. This skill treats missing
definitions as a blocker anyway, so a campaign never launches with that
reporting gap. The list is in `references/offer-doctrine.md`, section "Metadata
definitions". The discovery records:

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
  > {subdomain}: {keys}. Orders still go through without them, but their
  > campaign and attribution values cannot be filtered, exported or reported on
  > in the dashboard. They are created once per store and shared by every
  > campaign. Create the missing ones now?
  >
  > - A) Yes, create the missing definitions
  > - B) No, stop here (the plan stays blocked until they exist)

  On A, run:

  ```bash
  bash <skill-dir>/next-campaigns-create.sh metadata --store <subdomain> --apply
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
  `metadata:read`: create a key with all seven permissions and re-run `discover`.
  404 or 405 means the metadata endpoint is not on this store. Either way the
  plan carries a blocker; report the status to the operator rather than working
  around it.

---

## Create or update?

Decide here, before Phase 3. `discover` has just listed the campaigns the store
already has.

| The operator wants | Go to |
|---|---|
| a campaign that does not exist yet | Phase 3, the create path |
| a field change on a campaign a run of this skill created (a package price, an offer percentage or rounding, an offer condition or scope, pausing or resuming an offer, one added offer) | Phase 7, `edit` |
| any other change to a campaign this skill created or has adopted (a run directory with `run-manifest.json` and `campaign-plan.json` exists) | the update path, U3 |
| a change to a campaign built in the dashboard, or one whose run directory was lost | the update path, U1: `adopt` first |
| the campaign deleted | Phase 5's teardown when this run created it; the dashboard otherwise, since `teardown` refuses an adopted manifest |

Which command, in one rule: `edit` for a quick field change on a campaign this
skill created, because it previews the old and new value of each field, writes a
receipt and takes `--undo`. `adopt`, `diff` and `update` for a campaign made
elsewhere, and for everything `edit` cannot do: the campaign settings (name,
language, gateway group, payment methods, shipping countries, statement
descriptor), shipping method prices, adding or removing a package or a shipping
method, and any delete. `edit` refuses an adopted manifest, because it proves a
change against the plan's landed prices and an adopted plan has none. While an
edit is journalled and unfinished, `diff`, `update`, `verify`, `apply --resume`
and any different `edit` refuse until it is finished or undone; `teardown` still
works, reading the edit's receipt. While an update is journalled, `edit` refuses
the same way.

A second campaign on the same store is a create, in its own run directory. Do
not adopt a campaign you created in this session: its run directory already
owns it.

## Update path summary (U1 to U6)

The full procedure, with every plan edit and every refusal, is in
[`references/update-path.md`](references/update-path.md). Read it before running
`diff`. In outline:

- **U1 Identify.** Use the existing run directory, or
  `adopt --store <subdomain> --campaign <id>` after confirming the campaign's id, name and created_at with
  `AskUserQuestion`. `adopt` writes a plan whose desired state equals live plus
  a manifest with `origin: "adopted"`, into
  `./next-campaigns-create-runs/<subdomain>-<campaign_id>/` by default. Blockers
  (two packages on one variant, duplicate offer names, an unreadable `count`
  threshold, an `all_packages` offer without `--convert-scope <offer_id>`, or
  anything the plan validator rejects) write the plan, write no manifest, and
  exit 1.
- **U2 Show the current state.** `plan --plan <dir>/campaign-plan.json`. Its
  request list and approval line are the create rendering of the same plan; do
  not run them. An adopted plan has no anchor, no CTC and no landed prices.
- **U3 Edit a copy.** Copy `campaign-plan.json` to `campaign-plan.next.json`
  and edit the copy. Never edit the canonical plan: it is hash-bound to the
  manifest and `diff` refuses a run whose plan was edited in place.
- **U4 Diff.** `diff --plan <copy> --manifest <manifest>`. Read-only. Three-way:
  a field the copy left alone and the store changed is preserved; a field the
  copy changed is a PATCH; a field changed on both sides to different values is
  a conflict and refuses. It writes `change-set.json` and the merged plan, prints
  every request in order with before and after, and prints the exact `update`
  command. Get a go/no-go with `AskUserQuestion`, naming each DELETE step.
  "No changes" means the merged plan is the base plan; anything else is written,
  including a change set with 0 requests. A plan-only correction such as `role`
  lands that way, under "Plan only, nothing sent", with nothing sent to the
  store.
- **U5 Update.** `update --plan <merged> --manifest <m> --change-set <file>
  --yes --change-set-sha256 <hash>`, plus `--allow-delete` for any DELETE. Both
  are hard gates: without them nothing is sent. The store is re-read first and a
  campaign that moved since the diff refuses. Each op is journalled before it is
  sent, and the merged plan becomes the run's canonical plan at the end. Read the
  change set's warnings to the operator: a price op on a campaign with extra
  currencies leaves those prices alone, and a deleted offer or package takes its
  `landed_prices` and `voucher_codes` rows with it.
- **U6 Verify and hand off.** `verify --manifest <m> --plan <canonical plan>`.
  Then tell the funnel owner every id a POST created and every id a DELETE
  removed, because those pages have to be repointed. The campaign api_key does
  not change, and a PATCH never changes an id.

Interrupted runs: `update --resume` finishes one that stopped and can still
finish; `update --settle` closes one that cannot, sending nothing and promoting
what landed. While an update is journalled, `diff`, `verify`, `teardown` and
`apply --resume` all refuse with "an update is in progress; finish it with
update --resume". One command at a time per run directory: each takes an
exclusive `.run.lock`.

Two separate statements about this path, in this order:

1. **No write on the update path has been sent to a live store by this engine.**
   Not a PATCH, not a POST, not a PUT, not a DELETE on a campaign that already
   exists. The PATCH body shapes, a package DELETE while an offer references it,
   and the accepted clearing values are implemented from the published contract
   and proven against the offline fake.
2. **The response shapes the read side depends on were observed with GET
   requests on 2026-10-08.** That says nothing about any write.

See "Not yet verified against a live store" and "Read-only checks, 2026-10-08"
in `references/admin-api-contract.md`, which holds the record of both. Tell the
operator statement 1 before the first update on a campaign that matters, and
read the store back with `verify` after every one.

Phases 3 to 6 below are the create path.

---

## Phase 3: Gather inputs

Collect these via `AskUserQuestion` when the request does not already settle
them. **Ask offer type first**, then only that type's questions. Do not walk
the quantity-tier list for a buy-X-get-Y or gift-with-purchase campaign.

### Offer type

> What kind of offer is this campaign?
>
> - A) Quantity Buy 1/2/3 (percentage off every unit at each count)
> - B) Buy X get Y free (same product; encoded as a labeled percentage)
> - C) Gift with purchase (a separate gift package, free whenever it is in the cart)

The Campaigns App has no free-unit benefit, no cheapest-free allocator, no
min-spend condition, and no way to discount a different product than the one
that qualified. B and C are labeled compositions inside those limits. Numbers
and the mixed-price / repeat cases are in `references/worked-examples.md`.

Refuse a non-monotonic mix on the same hero packages: "Buy 1 at 50% plus buy 2
get 1 free" cannot be encoded, because the engine keeps the highest matching
`package_percentage` and quantity 3 would take 50%. Allowed: Buy 1 at list plus
BOGO; BOGO plus a deeper percentage at a higher count; quantity tiers plus a
gift (different package keys). `--tiers` with `--offer-type bxgy` is an error.

### Shared, every type

- **Hero product id**: from the discovery product table.
- **CTC, low or high**: operator judgement, never inferred. Cost-to-consumer is
  what the customer pays. Low CTC is the quantity-tier path and is required
  for buy-X-get-Y. High CTC is single units plus bumps and upsells; gift-with-
  purchase uses that shape for the hero. `recommend` refuses `--ctc high` with
  `--offer-type bxgy`.
- **Anchor price**: the package price quantity and BXGY discounts come off.
  This is not always the catalogue price; a catalogue price may already be
  discounted.
- **Markets**: currency, language and shipping countries (`--currency`,
  `--language`, `--countries`). Defaults come from the store's enabled set. The
  currency cannot be changed after the campaign is created, and the gateway
  group (`--gateway-group`) must list that currency and every payment method
  code the campaign enables.
- **Shipping**: at least one `<code>:<price>`, using a shipping method code
  from the discovery. Each store code appears **once**: the platform allows one
  campaign shipping method per code, and `recommend` refuses a repeated code.
  An optional key names the entry, `<code>:<price>:<key>`. One method is the
  normal case. To charge a different shipping price per bundle, work down this
  list:
  1. Free shipping from a quantity (next bullet). `recommend` builds it and
     `verify` proves it.
  2. A shipping offer below 100% on the one method, which is what the platform
     points to for a different price. It is a hand edit to
     `campaign-plan.json`; the plan gate labels its rows `shipping partly
     discounted` and `verify` does not model it, so prove it by hand.
  3. A different store shipping method for each price
     (`--shipping standard:9.99:ship-1 --shipping tracked:12.99:ship-2`), with
     `"shipping_key": "ship-2"` (for example) added to that row in
     `campaign-plan.json` before running `plan`. A row without one uses the
     first shipping method. This needs the store to have that many methods, and
     the funnel has to send the right id for each bundle.
- **Free shipping**: none, every order (`--free-shipping`), or from a minimum
  number of hero units (`--free-shipping-min-qty N`, for example 2 for Buy 2+).
  This is an operator decision, and the two flags are alternatives; passing both
  is an error. N has to be a quantity the landed rows reach, along with the one
  below it, so on the default Buy 1/2/3 tiers it is 2 or 3. `recommend` refuses
  anything else, and a high-CTC or gift-only hero (Buy 1 only) cannot take a
  threshold. Free shipping stays scoped to **hero** packages; a gift-only cart
  still pays shipping.
- **Price rounding**: always ask, on every offer type. It is the merchant's
  call, like CTC and the anchor, so never infer it. Work the example from the
  operator's anchor before asking. The discount is rounded to cents, then the
  unit price is floored to the dollar and the chosen cents added: 50% off
  $59.99 is $29.99 unrounded and $29.95 with .95. A rounded unit price can sit
  up to $1 either side of the plain percentage, so on a small discount (under
  about $1 a unit) it can land at or above the price it discounts from:
  $20.50 at 1% is $20.29, which .95 turns into $20.95. `recommend` refuses any
  quantity, buy-X-get-Y, upsell or exit offer whose rounded unit is not below
  that price (for the exit voucher, each tier price it stacks on), naming the
  offer, the unrounded and rounded prices, and the price it discounts from. Then ask for another ending, a larger percentage, or
  no rounding.

  > Should the discounted prices on this campaign be rounded to a price ending?
  > For example, 50% off {anchor} lands at {unrounded} as is, or {rounded}
  > ending in .95. It applies to every quantity, buy-X-get-Y, upsell and exit
  > offer; never to a free gift or free shipping.
  >
  > - A) No rounding
  > - B) End in .95
  > - C) End in .97
  > - D) End in .99
  > - E) Whole dollars (.00)

  A passes no flag. B to E pass `--rounding 0.95`, `0.97`, `0.99` or `0.00`:
  one value for every offer in the run. If the question tool takes at most 4
  options, drop E from the list and name whole dollars in the question text as
  a typed answer; if typed answers are not possible either, ask a short
  follow-up for whole dollars after the answer. Rounding pins the
  unit price, so some bundle totals become unreachable (see "Rounding and
  stacking" in `references/offer-doctrine.md`). On buy-X-get-Y, say in the
  question that the `X+Y` total will likely stop matching paying for X; the plan's
  landed table shows the real figure.
- **Bumps and upsells**: each as an explicit variant id, package price and, for
  upsells, voucher percentage. Nothing is inferred from catalogue prices.
  - Pass every variant of an upsell product as its own `--upsell`, all at the
    same percentage. `recommend` puts them in **one** voucher scoped to all of
    those variant packages. Never split them into one offer per variant.
  - One package per variant. When the upsell is the hero product (or a bump),
    pass that variant id at the price its package already has, and set the
    upsell price with the voucher percentage. `recommend` reuses the existing
    package. A different price for an already packaged variant is refused, and
    the error suggests the whole percentage that lands nearest the price asked
    for. The reused voucher also works at checkout if a shopper enters it
    there, stacking on the tier; the plan's handoff says so, and the funnel must
    never show that code on the checkout page.
  - A bump must be a variant no other package uses. A bump on a hero variant is
    refused: it would need a second package for that variant, and a bump line
    on the hero package would count toward the hero quantity tiers.
  - Never hand-author a second package for a variant already packaged, and
    never rename or split offers to get past a duplicate offer name or code
    error. That error means the specs belong in one group.
- **Standing checkout bump**: ask whether the store requires a bump on every
  campaign, such as a shipping-insurance product. If yes, it goes in as
  `--bump <variant_id>:<price>`.
- **Campaign name** (`--name`): the doctrine uses the hero product name. It must
  not match a campaign that already exists on the store.

### Quantity (A)

Keep this path identical to previous versions: `--ctc low|high` and
`--tiers 50,55,60` (default) emit Buy 1/2/3 at 50/55/60 percent off **every**
in-scope unit. That is not "Nth unit free". Override `--tiers` only when the
operator asks.

Optional: `--gift <variant_id>:<price>[:<qty>]` adds a gift package beside the
quantity ladder (see Gift below). `--paid-qty` / `--free-qty` on this type is
an error; use `--offer-type bxgy`.

### Buy X get Y (B)

Ask paid quantity X and free quantity Y (integers >= 1). Pass
`--offer-type bxgy --paid-qty X --free-qty Y`. `recommend` emits one automatic
offer at count `X+Y` with percentage `100 × Y / (X+Y)` (fractional rates
allowed on this path only; buy 2 get 1 is 33.33).

Tell the operator, before they approve:

- At exactly `X+Y` equal-priced units the total is close to paying for X, and
  exact only for some anchors. The percentage is held to 2 decimals, the
  discount is rounded to cents, and price rounding moves the unit price, so
  the landed total can be off by cents, or by more with price rounding. The
  plan's landed table and its rationale give the real total. Extra units above
  `X+Y` still get the same percentage; it does not repeat per extra set.
- Which unit is free is not a choice. Mixed-price variants all take the same
  %. That is proportional-off-all, not cheapest-free.
- Customer copy can say "3 for the price of 2" or "buy 2 get 1 free" only
  when the landed `X+Y` total equals X times the anchor. Otherwise quote the
  landed total ("3 for $101.85"), never a free unit or a percentage the cart
  does not deliver. Never "the cheapest unit is free". The cart shows a discounted unit on a consolidated line, not a $0
  FREE line.
- A different product as the free item cannot be encoded. Stop and use gift
  with purchase if they want a separate SKU that is free whenever it is in
  the cart (not when the hero qualifies).

`--tiers` with bxgy is an error (Buy 1 % would mask a lower BXGY rate).
Optional `--gift` is allowed; the gift uses different package keys.

Per-customer limits and date ranges are not offer-create fields. An offer is
always created live; pausing one is an edit after the campaign exists (Phase 7).

### Gift with purchase (C)

Ask which variant is the gift and the package price to put on it (never infer
the price). Pass `--offer-type gwp --gift <variant_id>:<price>[:<qty>]`.
`--tiers` and `--paid-qty`/`--free-qty` with gwp are errors; combine a gift
with quantity tiers via `--offer-type quantity --gift ...`.

Then ask auto-add versus customer pick (`--gift-mode auto|select`, default
auto). That choice is a **funnel handoff only**. The Offers API cannot
auto-add or auto-remove the gift. Min-spend GWP is a platform gap; do not
fake it.

Refuse an unavailable gift variant (`purchase_availability`) and a gift
variant that is already a hero, bump or upsell.

### Defaults

The remaining flags have defaults; override them only when the operator asks.
The quantity tier percentages (`--tiers`) default to 50,55,60 off the anchor
and the exit voucher (`--exit`) to 10 percent, both from
`references/offer-doctrine.md`. `--offer-type` defaults to `quantity`. The
others are `--exit-code`, `--short-name <product_id>:<NAME>` (the product's
voucher code name, A-Z0-9, starting with a letter, at most 12 characters, used
for its upsell and exit codes), `--payment-methods`, `--express-methods` and
`--statement-descriptor`. Price rounding is not left to its default: it is asked under
"Shared, every type".

---

## Phase 4: Recommend and plan review

This phase is the gate.

```bash
bash <skill-dir>/next-campaigns-create.sh recommend \
  --discovery ./next-campaigns-create-runs/<subdomain>/discovery.json \
  --hero <product_id> --ctc <low|high> --anchor-price <decimal> \
  --shipping <code>:<price>[:<key>] [--name "<campaign name>"] [--countries US,CA] \
  [--offer-type quantity|bxgy|gwp] [--paid-qty <n> --free-qty <n>] \
  [--gift <variant_id>:<price>[:<qty>]] [--gift-mode auto|select] \
  [--bump <variant_id>:<price> ...] [--upsell <variant_id>:<price>:<pct> ...] \
  [--exit 10] [--rounding 0.95] [--free-shipping | --free-shipping-min-qty <n>]
```

Quantity is the default `--offer-type` and still emits Buy 1/2/3 at 50/55/60
when `--ctc` is low. Buy-X-get-Y needs `--offer-type bxgy --paid-qty --free-qty`.
Gift-with-purchase needs `--offer-type gwp --gift ...`. Omit `--offer-type` (or
pass `quantity`) to keep the existing encode.

`recommend` writes `campaign-plan.json` next to the discovery file. It refuses a
directory that already holds a `run-manifest.json`, because that manifest's plan
is the only file that can resume or tear that campaign down; pass
`--out <new dir>` for a second campaign on the same store.

Then print the plan for review:

```bash
bash <skill-dir>/next-campaigns-create.sh plan \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json --check-store
```

`--check-store` also asks the store whether a campaign with the same name
already exists. `plan` prints the ordered request list, the voucher codes, the landed prices
table, the rationale, any blockers, and the plan's SHA-256. Each landed row ends with
one of five shipping labels:

| Label | Meaning |
|---|---|
| `shipping free` | a free-shipping offer covers every variant mix of the row |
| `shipping <price>` | no free-shipping offer can apply, so the row's `shipping_key` method is charged, or the first shipping method when the row names none |
| `shipping depends on variant mix` | some mixes of the row meet a free-shipping offer and some do not |
| `shipping partly discounted (not modelled; prove by hand)` | a shipping offer below 100% touches the row; verify expects full shipping there |
| `no shipping (post-purchase)` | an upsell row, which carries no shipping method |

If there are blockers, stop and clear them (see Failure modes), then re-run
`recommend` and `plan`. The engine will not apply a plan with blockers.

Otherwise show the operator the request list, the voucher codes and the landed
prices, and get an explicit go/no-go with `AskUserQuestion`. Voucher codes are
`{SHORT NAME}{PCT}` (for example `ALWAYSNEAR10`, `SNAPSHOT60`); the operator can
override a product's short name with `--short-name <product_id>:<NAME>` or the
whole exit code with `--exit-code`, then re-run `recommend` and `plan`:

> Ready to create campaign "{name}" on {subdomain}.29next.store: {N} requests
> ({P} packages, {S} shipping methods, {O} offers), price rounding
> {none | .95 | .97 | .99 | .00}. The request list and the landed prices are
> above. Proceed?
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
bash <skill-dir>/next-campaigns-create.sh apply \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
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
bash <skill-dir>/next-campaigns-create.sh apply \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
  --yes --plan-sha256 <plan-sha256> \
  --resume ./next-campaigns-create-runs/<subdomain>/run-manifest.json
```

A resume refuses a changed plan, a destination that holds a different run,
and a run that teardown has touched. After a teardown, finish it if it was
interrupted, then start a fresh run in a new directory.

`apply --resume` also refuses an adopted manifest: it finishes a build this
skill started, and an adopted campaign was built elsewhere, so every POST would
duplicate something that already exists. Change an adopted campaign with `diff`
and `update`.

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
bash <skill-dir>/next-campaigns-create.sh teardown \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json --yes
```

Teardown takes no hash argument: it computes the plan's hash itself and refuses
if it does not match the manifest. It deletes only what the manifest records,
reading each object back and checking its identity first, with the campaign
last.

Teardown refuses an adopted campaign outright: "teardown deletes what this run
created; reduce an adopted campaign with update --allow-delete". Removing
individual packages, shipping methods or offers from an adopted campaign goes
through the update path; deleting the campaign itself is a dashboard action.

---

## Phase 6: Verify and hand off

```bash
bash <skill-dir>/next-campaigns-create.sh verify \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json
```

`verify` reads every resource back and calls `carts/calculate` for each tier, a
mixed-variant cart, the exit voucher and each upsell voucher, comparing totals to
the cent. Shipping is decided per cart. A checkout cart carries the shipping
method its landed row names in `shipping_key`, or the first shipping method when
the row names none, and expects that price charged, unless the cart meets a
free-shipping offer's condition (`any`: one in-scope unit; `count`: N in-scope
units). A row whose method was never created fails without a calculate call. An
upsell cart calls calculate with `?upsell=true` and no shipping method, the way
a real upsell page does, and expects the voucher price alone. Each free-shipping
offer also gets a coverage row: it needs a cart at exactly its threshold that no
other offer would free, and one just below it that pays, because nothing else
proves where the threshold sits. Verify also lists the campaign's live offers
and fails if any were not created by this run, since a dashboard offer can price
a cart the way a planned one should and hide a wrong threshold. It writes `verify-report.json` next to the plan and exits 1 on FAIL. Report PASS
or FAIL, with the failing checks.

About the rows. Every field the update path can change has a read-back row: the
campaign name and PayPal account id, package name and recurring fields, and an
offer's name, `available`, condition type and value, benefit type and rounding.
Not every row is PASS or FAIL: `UNVERIFIED` marks a field this store does not
report, such as an offer threshold a read-back omits, and `INFO` marks something
that could not be proven and is not a defect. Neither changes the verdict, and
the `VERIFY:` line counts them. On a campaign with no `landed_prices`, which is
every adopted campaign, verify prices no carts and prints one INFO row,
`pricing coverage unproven: no landed rows`. The field read-backs still run, so
an adopted campaign is proven for its fields and unproven for its prices. Say
that in the handoff rather than reporting a clean PASS as pricing proof.

Then hand off:

- Campaign id, and the manifest path where the full api_key lives (gitignored,
  never echoed). Point the operator at the file; do not read the key into chat.
- Package ids for `data-next-package-id` in the funnel markup. Gift packages
  are listed too. For `--gift-mode auto`, tell `next-campaigns-setup` to add
  each gift package as a bundle item with `"noSlot": true`; for `select`, show
  it as a visible gift choice. This skill does not write that markup, and the
  Offers API will not auto-add or auto-remove the gift.
- Shipping method ids, one per shipping key, from the manifest's
  `shipping_methods` entries. When bundles charge different methods, the funnel
  sends the id of the method each bundle should charge. Use the ids from this
  run's manifest and no others: the campaign SDK checks a page's shipping id
  against the methods the campaign serves and does not apply one it cannot
  find, so the order may be charged another method's price (read from the
  SDK source, not proven on a live checkout). Deleting a method
  and creating it again gives it a new id, so every page that named the old id
  has to be repointed.
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

## Phase 7: Change the campaign in place

Use this when the operator asks to change a campaign this skill created and the
run directory is on hand: the plan and the `run-manifest.json` that `apply`
wrote. Plan the request as an edit first. When the engine refuses the change,
tell the operator which field forced it and take it to the update path (U3)
instead; offer teardown only when that refuses too. Teardown mints a new
campaign id and api_key and removes every offer on the campaign, including ones
the operator added in the dashboard, so it is never the first answer.

`edit` refuses a manifest with `origin: "adopted"`, because it proves a change
against the plan's landed prices and an adopted plan has none. Without that
run's plan and manifest there is nothing to edit from either: adopt the campaign
and use the update path.

What an edit can change, one operation each in an edit file:

| The operator asks to | Operation | Fields |
|---|---|---|
| change a package price | `set_package_price` | `package_keys`, `price` |
| change an offer's percentage or its price rounding | `set_offer_benefit` | `offer_key`, `value`, `price_rounding` |
| change the quantity an offer starts at, or between `any` and `count` | `set_offer_condition` | `offer_key`, `type`, `value` |
| change which packages an offer covers | `set_offer_scope` | `offer_key`, `package_keys` |
| pause or resume an offer | `set_offer_available` | `offer_key`, `available` |
| add an offer | `add_offer` | `offer` (shaped like a plan offer), optional `available` |

What it cannot change. Each is refused with `cannot be edited in place: <field>`.
Most of these are the update path's business (U3), and the rest need teardown and
recreate or the dashboard:

- The campaign's currency. Immutable once the campaign exists; teardown.
- The campaign's language or gateway group, and its name, payment methods,
  express methods, shipping countries, statement descriptor and PayPal account
  id. Update path.
- The product or variant behind a package. Teardown.
- A package image. Update path, as a PUT op in the change set.
- An offer's type, its voucher code, its benefit type, and `all_packages`.
  Teardown, except that the update path can delete the offer and create a
  replacement.
- A shipping method's price. Update path.
- Adding or removing a package or a shipping method. Update path.
- Deleting anything. An offer is paused, never deleted; the update path deletes
  under `--allow-delete`, and teardown removes the whole run.
- The percentage or condition of a buy-X-get-Y offer: both come from its paid
  and free quantities.
- Pausing a voucher a landed row depends on (the exit voucher, an upsell
  voucher): how the cart treats a paused code is unobserved, so the price could
  not be proven.

Three shapes are also refused, because the plan's landed prices could not
describe them. Each needs a fresh `recommend`, which means teardown and
recreate:

- Packages that share a landed row repriced to different prices. Reprice every
  variant of the product together.
- An offer scope that covers some of a row's packages and not others.
- Two live offers on the same packages at the same percentage.
- An upsell voucher whose new scope or condition would stop it applying to its
  own upsell row.

Write the edit file next to the plan as `campaign-edit.json`. This one doubles
the hero price and resets a four-tier ladder:

```json
{"operations": [
  {"op": "set_package_price", "package_keys": ["hero-23", "hero-24"], "price": "99.98"},
  {"op": "set_offer_benefit", "offer_key": "tier-1", "value": "50"},
  {"op": "set_offer_benefit", "offer_key": "tier-2", "value": "55"},
  {"op": "set_offer_benefit", "offer_key": "tier-3", "value": "60"},
  {"op": "set_offer_benefit", "offer_key": "tier-4", "value": "65"}
]}
```

Package and offer keys are the ones in the plan. Run `verify` first, so the
totals before the change are on record, then preview:

```bash
bash <skill-dir>/next-campaigns-create.sh edit \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
  --changes ./next-campaigns-create-runs/<subdomain>/campaign-edit.json
```

The preview reads the campaign and every object it will change, writes nothing,
and exits 2. It prints each field as old value and new value, the landed prices
before and after for every row, the exact requests, any live offer this run does
not own, and an `Edit SHA-256`. Read all of it to the operator. This is the gate,
and it needs two answers. Ask with `AskUserQuestion`:

> This changes campaign {id} on {subdomain} in place: {each field, old to new}.
> Landed prices move like this: {each row that changes, before and after}.
> {Offers this run does not own, if any: they are left exactly as they are.}
> Nothing is deleted and the campaign keeps its id and api_key.
>
> Is a funnel sending shoppers to this campaign right now?
>
> - A) Apply it, and no funnel is live on it yet
> - B) Apply it, and yes, shoppers are reaching it now (they see the new prices
>   the moment each request lands)
> - C) Change something first
> - D) Do not apply

On A or B, pass the hash the preview printed and the operator's answer:

```bash
bash <skill-dir>/next-campaigns-create.sh edit \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
  --changes ./next-campaigns-create-runs/<subdomain>/campaign-edit.json \
  --yes --edit-sha256 <edit-sha256> --live-traffic <yes|no>
```

The hash covers the plan, the edit file and what the store held when the preview
read it. If any of those moved, the run prints a new preview and exits 2: show
the operator the new one and ask again. Never reuse a hash from an earlier
preview.

What the engine does, in order:

1. Checks the manifest belongs to this plan and store, that teardown has not
   touched the run, and that the live campaign is the one this run created.
2. Reads back every object it will change and checks its identity, as teardown
   does. An object the manifest does not record is refused. A live value that
   differs from the plan (someone changed it in the dashboard) is refused too.
3. Saves each object's before-image to `edit-<n>-receipt.json` next to the
   manifest, then journals the edit in the manifest, before the first write.
4. Sends one request per object. It re-reads the object just before the write
   and stops if it changed, and reads it again after and stops if the result is
   not what was intended. An answer of 200 is not taken as proof. A write is
   never retried.
5. Rewrites `campaign-plan.json` with the new values and recomputed landed
   prices, and records the new plan hash in the manifest, so `verify`, `--resume`
   and `teardown` work against the edited campaign.

A new offer is live the moment it is created. `add_offer` with
`"available": false` creates it and pauses it straight after, and says so if the
pause fails, because the offer is live until it lands.

Then run `verify` again (Phase 6) and report it. If the campaign carries an offer
this run does not own, `verify` still fails on `campaign offers match the plan`,
exactly as it did before the edit: it cannot attribute cart totals to the plan
while that offer is live. The report says so and shows the fields this run owns
separately. In that case the edit's own read-back of each object is the proof
that the change landed, and the operator checks the cart totals by hand.

**If it stops partway.** Nothing is retried on its own. The manifest records how
far the edit got, and `verify`, `apply --resume`, `diff` and `update` all refuse
until it is settled, each saying "an in-place edit of this run is unfinished".
Give the operator both ways out:

- Finish it: run the same `edit` command again. It prints the progress and the
  edit's hash, and continues with `--yes --edit-sha256 <that hash>`. Each object
  is classified by what the store shows: the intended result is done, the saved
  before-image is written, anything else stops.
- Roll it back: `edit --undo <dir>/edit-<n>-receipt.json`, previewed and gated the
  same way. It restores only what the edit wrote and leaves alone anything it
  never reached.

`teardown` still works while an edit is unfinished. It reads the receipt to
identify an offer the edit created, and accepts whichever of the three plan
hashes an interrupted edit may have left on disk.

**Undo.** After a finished edit:

```bash
bash <skill-dir>/next-campaigns-create.sh edit \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
  --undo ./next-campaigns-create-runs/<subdomain>/edit-<n>-receipt.json
```

It previews the reverse change and takes the same approval: ask the operator the
same question, then run it again with the hash that preview printed.

```bash
bash <skill-dir>/next-campaigns-create.sh edit \
  --manifest ./next-campaigns-create-runs/<subdomain>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>/campaign-plan.json \
  --undo ./next-campaigns-create-runs/<subdomain>/edit-<n>-receipt.json \
  --yes --edit-sha256 <edit-sha256> --live-traffic <yes|no>
```

Fields go back to their saved values. An offer the edit added is paused and kept, because edit
never deletes; `teardown` removes it with everything else. Undo works backwards
from the most recent edit.

---

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| `Could not check for updates` | offline, GitHub unreachable, or Python missing; failures are cached for an hour | nothing to fix: continue. Compare versions by hand with the catalog link it prints if it matters for this run |
| `credential missing` | no token found for this store in the environment, `.env` or `NEXT_ADMIN_API_TOKEN` | add the `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` line to `.env` (Phase 1); never paste it in chat |
| `still holds a placeholder` | the token value is a placeholder such as `<paste-token-here>` | have the user paste the real token over it in a text editor |
| 401 or 403 from the store | key rejected, or missing one of the seven permissions; the message names the permission the request needed (`store:read` on the first `discover` request is the usual one) | re-create the key with all seven under Dashboard > Settings > API Access; retrying does not help, and full access is not needed |
| `shipping code ... given twice` or `both use store code`; or a 400 from the store on a shipping create that names the `shipping_method` field as already on the campaign (the wording may vary by release) | the same store code at two prices | one campaign shipping method per code: use free shipping from a quantity, else a shipping offer on the one method, else a different store shipping method per price (the Shipping decision in Phase 3, under Shared, every type) |
| `upsell variant ... is already package ...` | an upsell at a different price from the package that variant already has | pass the variant at its package price and the percentage the error suggests |
| `bump variant ... is already package ...` or `both use variant` | a bump on a hero variant, or a hand-edited second package for one variant | choose a different variant for the bump; for a hand edit, reuse the one package and set the price with an offer |
| `upsell variant ... given twice` | the same variant passed to `--upsell` more than once | pass each upsell variant once; variants of one product at the same percentage already share one voucher |
| `products ... both shorten to voucher code name ...` or `would both get voucher code ...` | two products on this campaign land on the same short name, or the same finished code | ask the operator for a short name and pass `--short-name <product_id>:<NAME>` for one of them; never add a suffix on your own |
| `product title ... has no distinctive words for a voucher code` | the title is only generic words (Christmas, Ornament, Calendar) and numbers | ask the operator for a short name and pass `--short-name <product_id>:<NAME>` |
| `... lands it at ..., not below ...` | `--rounding` floors the discounted unit to the dollar and adds the ending, so a small discount rounds back up to or past the price it discounts from | pass a different `--rounding` ending, a larger percentage, or omit `--rounding` |
| `exit voucher code ... is also an upsell voucher code` | an upsell of the hero product at the exit percentage generates the same code | pass a short `--exit-code`, such as `SAVE10` |
| launcher exits 2 with a Python message | no Python 3.9 or newer found | install Python 3.9 or newer, or set `NEXT_CAMPAIGNS_CREATE_PYTHON` |
| `NOT APPLIED: pass --yes --plan-sha256` | the gate | re-run `plan`, copy the hash, pass it to `apply` |
| `plan has blockers` | metadata missing or conflicting, or a stale discovery after `metadata --apply` | run `metadata --apply` if needed, re-run `discover`, re-run `recommend` |
| `a campaign named ... already exists` | name collision | rename in the plan (re-run `recommend` with `--name`), or resume that run's manifest |
| `already holds a run` | `recommend` into a directory that has a `run-manifest.json` | pass `--out <new dir>` |
| `already holds a different run` | `--resume` or `--out` points at another run's manifest | resume with that run's own plan and manifest, or choose a new `--out` |
| `this run was torn down` | `--resume` on a manifest that teardown started or finished | re-run `teardown` if it was interrupted, then start a fresh run with `recommend --out <new dir>` |
| `not gitignored` | the run directory is inside a git repository that does not ignore it | add `next-campaigns-create-runs/` to `.gitignore`, or pass `--out` outside the repository |
| `cannot confirm ... is safe to write` | a `.git` directory was found but git could not be asked | install git, or pass `--out` outside the repository |
| offers POST returns 404/405 | store's Offers API not live | campaign, packages and shipping are created; build offers in the dashboard |
| image PUT returns 404/405 | store's build predates package images | everything is created; packages keep the catalogue image, set overrides in the dashboard |
| image PUT rejected (`image_status: failed`) | bad `src`, unreachable URL, wrong type, over 10 MB or 25 MP | terminal: `--resume` cannot help, the hash pins the `src`; fix in the dashboard, or teardown and recreate |
| `verify` fails `package ... image` | the catalogue image fetch failed silently at create | add an `image.src` override and recreate, or set it in the dashboard and accept the row |
| checkout `calculate` rows off by exactly the shipping price | the live free-shipping offer's condition or scope does not match the plan; or, on a store without the Offers API, the free-shipping offer was built in the dashboard as the handoff said, and the plan (which verify checks) has none | check the offer in the dashboard; the plan's condition is what verify expects. On a store without the Offers API this row is expected: prove the dashboard offer with your own `calculate` probes at the threshold and one below it |
| `campaign offers match the plan` fails | an offer was added to the campaign outside this run, usually in the dashboard | remove it, or treat the pricing as unproven: verify cannot isolate the plan's offers while it exists. The report's `sections` show the fields this run owns separately |
| `NOT APPLIED: nothing was written` from `edit` | the edit gate: no `--yes`, a hash that is not the one this preview printed, or no `--live-traffic` | show the operator the preview, ask, then pass the hash it printed with `--live-traffic yes` or `no` |
| `cannot be edited in place: <field>` | the request has no in-place route | tell the operator which field it is, then take it to the update path (U3); teardown and recreate only when that refuses too |
| `would no longer share one price`, `would cover only some of its packages`, or `both apply at N%` | the edit leaves a shape the plan's landed prices cannot describe | reprice a product's variants together, scope an offer to the whole row, or give the offers different percentages; otherwise a fresh `recommend`, which means teardown and recreate |
| `is ... live, ... in the plan` on `edit` | the object was changed outside this skill, usually in the dashboard | put the value back, or take the change through `diff`, which reconciles a dashboard change instead of refusing it; edit will not overwrite a change it cannot account for |
| `does not carry its full condition` or `does not carry available` | this store's read of the offer omits a field the write depends on | the change cannot be proven here; make it in the dashboard |
| `is not something this run created` or `did not create` on `edit` | the edit names a package or offer the manifest does not own | edit never touches it; change it in the dashboard |
| `changed since it was read`, `returned <status>; not retried`, or `not the intended state` during an edit | the edit stopped partway; it never retries a write | read the message to the operator, then either re-run the same `edit` to finish or `edit --undo <receipt>` to roll back (Phase 7) |
| `an in-place edit of this run is unfinished` | `verify`, `apply --resume`, `diff`, `update` or a second edit while one is half done | finish or undo that edit first |
| `neither its saved before-image nor this edit's result` on `--undo` | someone changed the object after the edit stopped | rollback will not guess: set it by hand to one or the other in the dashboard, then re-run the undo |
| `its receipt cannot be read` | the `edit-<n>-receipt.json` of an unfinished edit is missing | restore the file to the run directory; it holds the before-images and the identity of anything the edit created |
| `offer ... journalled` fails | apply stopped partway through the offers, so later ones were never created | `apply ... --resume <manifest>` |
| `offer ... free-shipping coverage` fails | no landed row sits at the threshold or just below it, another free-shipping offer would free those carts anyway, or the shipping price is too small for calculate to tell free from paid | add the missing landed row, drop the overlapping offer, or prove the threshold with your own calculate probes |
| apply stopped mid-run | any non-2xx | `apply ... --resume <manifest>` after fixing, or `teardown` |
| `pending shipping method ... matches N remote entries` on resume | a shipping create lost its response, and the entries left on that code do not settle which one it made: none at the planned price (edited, or no readable price in the campaign currency), or two or more at it | check the campaign's shipping methods in the dashboard, delete the stray entry or restore its price, then resume |
| verify case `shipping method ... not created` | apply stopped before that shipping method, so the rows priced with it cannot be proven | `apply ... --resume <manifest>`, then verify again |
| `identity mismatch` on teardown | the manifest does not match what is live | do not force; investigate which campaign the manifest points at |

Update path (full refusal table with the engine's own wording in
[`references/update-path.md`](references/update-path.md)):

| Symptom | Cause | Fix |
|---|---|---|
| `another next-campaigns-create.sh command holds this run directory` | a second command on the same run, or a `.run.lock` left by a killed process | wait for it to finish; if that pid is gone, delete the named lock file by hand, only after confirming no command is running |
| `an update is in progress; finish it with update --resume` | `active_update` is on the manifest, so `diff`, `verify`, `teardown`, `edit` and `apply --resume` all refuse | `update --resume` with the same change set and hash, or `update --settle` when it cannot finish |
| `campaign changed since you reviewed the diff; re-run diff` | the store moved between the diff and the update, a dashboard image change included | re-run `diff`, review the new change set, approve that one |
| `conflicting edits: the store changed what you changed` | base, candidate and store all differ on one field | set the candidate to the store's value to keep it, or put the store back to the base value, then diff again |
| `the campaign carries object(s) this run does not own` | a package, shipping method or offer added outside this run | remove it in the dashboard, or adopt the campaign into a fresh run directory and edit that plan |
| `cannot be deleted: the store changed it since the base plan` | the object to be deleted is not what the plan recorded, so the baseline hash would not have caught it | review the printed values with the operator, then re-run `diff --delete-changed <section>:<key>` |
| `cannot be deleted while live offer(s) ... scope it` | a package a surviving offer still covers; the API refuses that delete | narrow or delete those offers in their own update first, then delete the package |
| `would set the name ... which live offer N still holds at that point` | two offers swapping a name or a voucher code; they are unique per campaign | do it as 2 updates through a temporary name, or reorder so the offer holding it changes first |
| `is scoped to all_packages` on `adopt` | a live offer a plan cannot represent, because it would also cover packages created later | re-run `adopt` with the printed `--convert-scope <offer_id>` after the operator approves pinning it, or narrow the offer in the dashboard |
| `is a count offer whose threshold reads back as ...` on `adopt` | a threshold that is missing or not a whole number; the engine never guesses one | read it in the dashboard, correct the offer there, then adopt again |
| other `adopt` blockers (two packages on one variant, two methods on one code, duplicate offer names, a validator error) | the campaign is in a state a plan cannot describe | fix it in the dashboard, then adopt again; no manifest was written |
| `this change set deletes object(s) ... and --allow-delete was not passed` | the second gate | re-read the DELETE steps with the operator, then add `--allow-delete` to the same command |
| `this change set has already been applied` | the same change set run twice | run `diff` again for the next change |
| `the canonical plan has been edited in place` | `campaign-plan.json` was edited directly instead of a copy | restore it from an archive in the run directory (they are byte copies), then diff again |
| `--plan is the canonical plan itself` | `diff` was handed the base plan | pass the edited copy, `campaign-plan.next.json` |
| `op N: ... returned 4xx ... nothing after it was sent` | the store rejected that write | fix the cause and `update --resume`, or close the update with `update --settle` and diff the remainder |
| `the campaign is not in the state this interrupted update left it in` | the campaign was edited while the update was not running, so resuming would overwrite that edit | review the named values, then `update --settle` and run a fresh diff |
| `this run's plan promotion was interrupted and the merged plan it promotes to ... is gone` | the merged plan archive was deleted mid-promotion | restore that file (the change set names it in `merged_plan_file`) and run the command again; promotion finishes itself |
| `teardown deletes what this run created` | `teardown` on an adopted campaign | reduce it with `update --allow-delete`; delete the campaign itself in the dashboard |
| `was adopted from an existing campaign, not created by a run` | `apply --resume` or `edit` on an adopted manifest | change an adopted campaign with `diff` and `update` |

---

## Invariants

The offer-construction rules below apply to `recommend` and `apply`. Clone
preserves existing offer semantics, including `all_packages`.

- Never scope a tier offer to `all_packages`; always name the hero package ids.
- Never create quantity packages (`2x ...`); tiers are offers.
- Never encode buy-X-get-Y as a free-unit or Nth-unit-free field; label the
  percentage approximation and the repeat / mixed-price limits.
- Never scope a 100% gift offer to hero packages, and never apply
  `price_rounding` to it.
- Never invent a min-spend condition, a split condition/benefit package list,
  or an Offers API auto-add. Those are platform gaps; say so in the handoff.
- Never infer CTC or the anchor price; both come from the operator.
- One package per variant and one campaign shipping method per store code.
  A price difference is an offer or voucher, shipping included. An upsell
  reuses the package its variant already has; a bump must be a variant no other
  package uses.
- One voucher per upsell product and percentage, scoped to every variant
  package of that product.
- Never touch a campaign the run manifest does not own, under any flag. `adopt`
  is the only way ownership is taken, by campaign id, and only after the
  operator has confirmed the id, the name and the created_at of the campaign
  being adopted. Edit changes and teardown deletes only what this run created,
  after reading each object back and checking its identity. An offer the
  manifest does not record is listed to the operator and left alone.
- Never resolve a campaign by name without confirming its id. Campaign names are
  not unique on a store, and `adopt` takes an id for that reason.
- Offer an in-place edit before teardown for a field change on a campaign this
  skill created, and the update path for anything the edit ops do not cover.
  Offer teardown only when both refuse, and name the field.
- Never mutate the clone source. Clone creates a new destination campaign and
  the source id is refused as a delete or rename target.
- Never apply an edit without showing the operator its preview and asking
  whether shoppers are on the campaign. Never reuse an edit hash.
- Never retry an edit write, and never delete as part of an edit. An offer is
  paused.
- Keep every `edit-<n>-receipt.json`. It is the before-image an undo replays.
- Never PUT an image on a package the run manifest does not own, and never use
  the DELETE image route. Removing an image is a dashboard action; `diff` warns
  and sends nothing.
- Never edit `campaign-plan.json` in place by hand. It is hash-bound to the
  manifest and is the only record of the state a diff compares against. Operator
  edits go in a copy, by convention `campaign-plan.next.json`; `edit` and a
  completed `update` are the only things that rewrite the canonical plan, and
  both move the manifest's hash with it.
- Never run `update` with a change-set hash other than the one the latest `diff`
  printed, and never with a `--plan` other than the merged plan file that same
  diff wrote.
- Never pass `--allow-delete` or `--delete-changed` before the operator has
  seen the DELETE steps those flags approve, named one by one.
- Never delete a `.run.lock` without confirming with the operator that no
  command is running. The engine removes its own lock when it exits and never
  decides on its own that another process is gone.
- Never put base64 in a plan. A package image is an https URL, and it should be a
  durable public one: whatever goes in `src` is printed at the gate and stored in
  the plan and the manifest.
- Never print or paste the Admin token or the full campaign api_key.
- A token that reached a chat or ticket is rotated after the work.
- Never edit an existing metadata definition. `metadata --apply` only creates
  missing ones.

---

## Output files

`discover` writes to `./next-campaigns-create-runs/<subdomain>/` under the current
working directory. `adopt` writes to
`./next-campaigns-create-runs/<subdomain>-<campaign_id>/`. `recommend` writes
next to the discovery file it reads; `apply` and `verify` write next to the plan
file; `diff` writes into the run directory. `clone` writes to
`./next-campaigns-create-runs/<subdomain>/clone-<source_id>/`, with its
verification report beside the manifest. `--out` overrides each. `edit`
rewrites the canonical plan in place and writes its receipts next to the
manifest.

| File | Written by | Holds |
|---|---|---|
| `discovery.json` | `discover` | the store snapshot: catalogue, gateway groups, shipping methods, existing campaigns, the Offers API probe and the metadata audit |
| `campaign-plan.json` | `recommend`, `adopt`, then every `edit` and `update` | the run's canonical plan: the desired state of the campaign. `recommend` writes what `apply` will create; `adopt` writes what is already live; an `edit` rewrites it with the fields it changed and the landed prices recomputed; a completed `update` promotes the merged plan onto it |
| `campaign-plan.next.json` | you, by convention | the operator's working copy, the only file `diff` reads edits from. Input only: nothing promotes it |
| `campaign-edit.json` | you, in Phase 7 | the operations of one in-place edit |
| `change-set.json` | `diff` | the reviewed change set: every op in order with its body, before and after, the preserved values, the plan-only differences, the warnings, the baseline snapshot and its hash, and the merged plan (`references/admin-api-contract.md` lists every key). Its own SHA-256 is the approval token for `update` |
| `campaign-plan.<sha8>.json` | `diff`, and promotion | two kinds, both byte copies: the merged plan `diff` wrote and `update --plan` takes, and the previous canonical plan archived by each promotion. The 8 characters are the start of that plan's SHA-256 |
| `run-manifest.json` | `apply`, `adopt`, `clone`, `edit`, `update` | every id this run owns and its status, the campaign api_key, and for an adopted run `origin: "adopted"` with `adopted_at` and `adopted_from_campaign_id`. An edit in flight adds `pending_edit`; a finished one adds an `edits[]` entry. An update in flight adds `active_update` and an op journal; a completed one adds a `history[]` entry. A clone run has `kind: "clone"`, `plan_sha256: null`, the approval document, `clone_request`, `rename`, `inventory_complete` and `parity` |
| `edit-<n>-receipt.json` | `edit` | the before-image of every object the edit wrote, the plan before and after, and the approval it ran under; what `--undo` replays |
| `edit-<n>-rollback.json` | `edit --undo` on an unfinished edit | what the rollback did and the plan it left |
| `verify-report.json` | `verify` | every read-back and cart check, with PASS, FAIL, INFO or UNVERIFIED |
| `.run.lock` | every command that can write the run directory, `edit` included | the pid and start time of the command holding this run. Removed when it exits, and only by hand when it was killed |

One directory per campaign run. `recommend` refuses a directory that already
holds a `run-manifest.json` (pass `--out <new dir>` for a second campaign on the
same store), `adopt` refuses the same thing, and a resume refuses a destination
holding a different run.

If the directory is inside a git repository it must be gitignored, or the
engine refuses to write, because the manifest holds the campaign api_key.
`run-manifest.json` is written atomically with mode 600 and is the only file
holding a live secret.

---

## Staying up to date

- The installed version is the `version:` line in this file's frontmatter, also
  printed by `bash <skill-dir>/next-campaigns-create.sh --version`.
- `bash <skill-dir>/next-campaigns-create.sh check-update` compares it with the
  published version and prints the update command for this copy (Phase 0 runs
  it). Add `--no-cache` to skip the one-day cache.
- From a checkout of `NextCommerceCo/skills`,
  `git pull --ff-only && ./skills.sh status` reports `stale` when a newer
  version exists, and
  `./skills.sh install <claude|codex|agents|all> next-campaigns-create` updates
  it. Use `--force` only after reviewing a `modified` row.
- Without a checkout, run `npx skills update`.
- To check for a newer version without a checkout, compare `--version` with the
  `version` of the `next-campaigns-create` entry in
  https://raw.githubusercontent.com/NextCommerceCo/skills/main/skills.json.
- Versions before 0.6.0 shipped as `next-create-campaign`. The installer does
  not update or remove a copy under that name: install `next-campaigns-create`,
  then delete the old directory (`~/.claude/skills/next-create-campaign`,
  `~/.codex/skills/next-create-campaign` or `~/.agents/skills/next-create-campaign`).
  A 0.5.0 copy's `check-update` looks up the old name, which is no longer in the
  catalog, so it reports that it could not check rather than naming 0.6.0.
  The launcher is now `next-campaigns-create.sh`, `discover` defaults to
  `./next-campaigns-create-runs/`, and the interpreter override is
  `NEXT_CAMPAIGNS_CREATE_PYTHON`. An existing `next-create-campaign-runs/`
  directory still works when its files are passed by path, as long as it stays
  gitignored.
- Release notes and breaking changes live in the merged pull request that
  carried each version bump, titled `next-campaigns-create X.Y.Z: ...` from
  0.6.0 and `next-create-campaign X.Y.Z: ...` before that (the first one is
  `Add next-create-campaign public skill`). They are listed at
  https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-campaigns-create
  (0.6.0 onward) and
  https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-create-campaign
  (earlier versions).
- There are no tags or releases. Copies older than 0.5.0 do not have
  `check-update`; they need one manual update before it starts working.

---

## References

- [`references/offer-doctrine.md`](references/offer-doctrine.md): the offer
  rules `recommend` encodes, including the section "Metadata definitions".
- [`references/admin-api-contract.md`](references/admin-api-contract.md): the
  API contract the engine implements, the update and in-place-edit semantics,
  and the file schemas for the plan, the manifest, the edit file, the receipts
  and the change set.
- [`references/update-path.md`](references/update-path.md): the full update
  procedure, U1 to U6, with the plan edit for each kind of change, every
  refusal, and the rule for choosing between that path and `edit`.
- [`references/worked-examples.md`](references/worked-examples.md): worked
  numbers for quantity, buy-X-get-Y and gift-with-purchase.
- [`examples/campaign-plan.example.json`](examples/campaign-plan.example.json):
  an example of the plan `recommend` writes.
- Campaigns Admin API: https://developers.nextcommerce.com/docs/campaigns/admin-api
- Admin API permissions: https://developers.nextcommerce.com/docs/admin-api/permissions

| Subcommand | Method | Endpoint |
|---|---|---|
| `discover` | GET | `/api/admin/store/`, `/api/admin/gateway-groups/`, `/api/admin/shipping-methods/`, `/api/admin/products/`, `/api/admin/campaigns/`, `/api/admin/campaigns/{id}/offers/` (the Offers API probe), `/api/admin/metadata/` |
| `metadata --apply` | POST | `/api/admin/metadata/` |
| `apply` | POST, PUT | rows 1 to 5 of the write inventory |
| `clone` | GET, POST, PATCH | source read-back, clone POST (row 27), optional destination rename (row 28) |
| `verify` | GET, POST | campaign read-back, then `/api/v1/carts/calculate/` on the Cart API; a clone run skips the Cart API |
| `edit` | GET, PATCH, POST | read-back, then rows 10 to 12 of the write inventory |
| `teardown` | GET, DELETE | read-back, then rows 6 to 9 of the write inventory |
| `adopt` | GET | `/api/admin/campaigns/{id}/`, its `packages/`, `shipping-methods/` and `offers/` lists, then `/api/admin/campaigns/{id}/offers/{offerId}/` for each offer's scope. No writes |
| `diff` | GET | the same reads as `adopt`. No writes |
| `update` | PATCH, POST, PUT, DELETE, GET | rows 13 to 22 of the write inventory, plus row 3 (the image PUT) for any package whose image the change set sets, after the same reads and again at the end to refresh the manifest |

API version: `2024-04-01`. The metadata POST is the one request sent without the
version header; the API gotcha in `references/offer-doctrine.md` explains why.
