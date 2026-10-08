# next-campaigns-create: the update path (U1 to U6)

Companion to `SKILL.md`, which carries the create path (Phases 1 to 6) and a
short summary of this one. This file is the full agent procedure for changing a
campaign that already exists. The engine (`scripts/campaign_admin.py`) owns the
behaviour: where this file and the engine disagree, the engine wins, and
`admin-api-contract.md` wins for the API contract and the file schemas.

Three commands, in this order: `adopt` (once per campaign, only for a campaign
this skill did not create), `diff` (read-only, writes the change set), `update`
(the only one that writes to the store). `adopt` and `diff` send no writes.

The update path never touches a campaign the run manifest does not own, and it
never takes ownership on its own: `adopt` is how an operator hands a campaign
over, after confirming its id.

```
adopt   --store <subdomain> --campaign <id> [--out <dir>] [--convert-scope <offer_id>]
diff    --plan <dir>/campaign-plan.next.json --manifest <dir>/run-manifest.json
        [--out <dir>] [--delete-changed <section>:<key>]
update  --plan <dir>/campaign-plan.<sha8>.json --manifest <dir>/run-manifest.json
        --change-set <dir>/change-set.json --yes --change-set-sha256 <change-set-sha256>
        [--allow-delete] [--resume | --settle]
```

---

## U1: Identify the campaign

Two cases, and they are not interchangeable.

**The run directory exists.** A campaign this skill created, or one already
adopted, has a `run-manifest.json` and a `campaign-plan.json`. That pair is the
only thing that can update it. Use it and skip to U2. Never adopt a campaign
twice: `adopt` refuses an out directory that already holds a manifest, and two
manifests for one campaign would each think they own it.

**No run directory.** The campaign was built in the dashboard, or its run
directory was lost. It has to be adopted. Resolve the campaign by **id**, never
by name: campaign names are not unique on a store. Adopting one campaign into
two run directories is not something the engine can detect, so do not create a
second one.

```bash
bash <skill-dir>/next-campaigns-create.sh discover --store <subdomain>
```

`discover` prints every campaign on the store as `[id] name currency/language`,
and `discovery.json` records each one's `created_at` as well. Confirm the right
one with `AskUserQuestion` before adopting, quoting all three identity fields
(id, name, created_at):

> Take ownership of campaign {id} "{name}", created {created_at}, on
> {subdomain}.29next.store? Adopting writes a plan and a run manifest that
> describe this campaign exactly as it is now. It sends nothing to the store and
> changes nothing on it. Every later change goes through that plan.
>
> - A) Yes, adopt campaign {id}
> - B) No, that is the wrong campaign (I will list them again)

On A:

```bash
bash <skill-dir>/next-campaigns-create.sh adopt --store <subdomain> --campaign <id>
```

The default output directory is
`./next-campaigns-create-runs/<subdomain>-<campaign_id>/` under the current
working directory; `--out <dir>` overrides it. `adopt` reads the campaign, its
packages, its shipping methods and each offer by id, then writes
`campaign-plan.json` whose desired state is exactly what is live, plus
`run-manifest.json` with `origin: "adopted"`, `adopted_at`,
`adopted_from_campaign_id` and the campaign api_key (mode 600, never printed).

It prints the campaign's id, name and created_at, the object counts, and the
role it guessed for each package. Show those lines to the operator as printed.

### Blockers

`adopt` never guesses. With any blocker it writes the plan with a `blockers[]`
list, writes **no** manifest, prints the re-run command and exits 1. The
campaign is not adopted and nothing else can run against it.

| Blocker | What the operator does |
|---|---|
| two packages on one product variant | remove one in the dashboard, then adopt again |
| two campaign shipping methods on one store code | remove one in the dashboard, then adopt again |
| two offers with the same name | rename one in the dashboard, then adopt again |
| a `count` offer whose threshold does not read back as a whole number | read the threshold in the dashboard, correct the offer there, then adopt again |
| an offer scoped to `all_packages` | see `--convert-scope` below |
| anything the plan validator rejects (a `2x` package name, no shipping method, an unsupported benefit type, an invalid voucher code) | fix it in the dashboard, then adopt again |

`--convert-scope <offer_id>` is the only blocker with an in-engine answer. A
plan carries package keys, so it cannot express "every package, including ones
created later". Passing the flag pins that offer to the campaign's packages as
they are now, records the decision in the plan's `waivers[]`, and sets
`scope_conversion_pending: true` on that manifest offer so the first `diff`
shows the conversion as a PATCH. Ask the operator first: it narrows a live offer
and the narrowing is a real change to the campaign's behaviour for any package
added later. The blocker message names the exact re-run command, including every
`--convert-scope` the campaign needs.

---

## U2: Show the current state

The canonical plan is the current state. Print it:

```bash
bash <skill-dir>/next-campaigns-create.sh plan \
  --plan ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.json
```

`plan` prints the campaign settings, the packages with their roles and prices,
the shipping methods and the offers with their conditions and benefits. Read it
with the operator before asking what they want changed.

Two things in that output do not apply here. The request list is headed
"Requests apply will send, in order" and the last line is an `apply` approval
command: both are the create-path rendering of the same plan. Do not run them.
`apply` refuses a directory that already holds a manifest, and `apply --resume`
refuses an adopted manifest outright.

`adopt` also prints a role table:

```
Roles (a heuristic from the offers; correct them in the plan before the first diff):
  pkg-801        variant 801 (v801)  hero
  pkg-802        variant 802 (v802)  bump
```

`role` is plan-only: it is never sent to the API, and changing it is never an
op. It drives `verify`'s hero and free-shipping logic, so a wrong role is worth
correcting. Correct it in the copy (U3), not in `campaign-plan.json`: editing
the canonical plan in place breaks its hash and `diff` refuses the run. A
role-only edit produces no ops and no preserved values, so `diff` reports no
changes and writes nothing. A role correction therefore lands when it travels
with a real change, which the merged plan then carries.

What an adopted plan does not know, because nothing on the store says it:

| Missing | Effect |
|---|---|
| the anchor price the discounts came off | no landed row records it, so the landed-price table cannot be rebuilt |
| cost-to-consumer | no `ctc`, so the doctrine's structure checks do not apply |
| landed prices | `landed_prices` is `[]`, so `verify` proves read-back only and prints one INFO row saying pricing is unproven |

An adopted campaign is therefore verifiable for its fields and unproven for its
prices. Say that plainly in any handoff.

---

## U3: Gather the change and edit a copy

Ask the operator what they want changed, in their words, then make the edit
yourself. Never ask them to edit JSON.

Edit a **copy**. The canonical `campaign-plan.json` is hash-bound to the
manifest and is the only record of the state the diff compares against:

```bash
cp ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.json \
   ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.next.json
```

`campaign-plan.next.json` is the conventional name: it is what `adopt` tells
you to create and what `diff`'s own help and refusals name. The copy is input
only, and `diff` never promotes it. `diff` refuses a `--plan` that is the canonical plan itself.

| What the operator asks for | The edit in `campaign-plan.next.json` |
|---|---|
| a different price on a package | that `packages[]` entry's `price` (decimal string, 2 places) |
| a different subscription price or interval | `price_recurring`, `interval`, `interval_count` on that entry, all three together or none |
| rename the campaign | `campaign.name` |
| rename a package | that entry's `name`, bare: the API appends `" - {variant}"` itself |
| rename an offer | that `offers[]` entry's `name` (unique per campaign) |
| a different shipping price | that `shipping_methods[]` entry's `price` |
| different shipping countries | `campaign.available_shipping_countries` (alpha-2 codes; `[]` clears it) |
| different payment methods | `campaign.available_payment_methods` or `available_express_payment_methods` (codes the gateway group lists; `[]` clears) |
| a different gateway group | `campaign.payment_gateway_group_id` |
| a different statement descriptor, or none | `campaign.statement_descriptor` (`null` clears it) |
| link or unlink a PayPal account | `campaign.paypal_account_id` (`null` clears it) |
| add or drop extra currencies | `campaign.additional_currencies` (`[]` clears it; the PATCH sends `null`) |
| a deeper or shallower discount | that offer's `benefit.value` |
| a different price ending | that offer's `benefit.price_rounding` (`null`, `"0.00"`, `"0.95"`, `"0.97"`, `"0.99"`) |
| a different quantity threshold | that offer's `condition.value`, with `condition.type: "count"` |
| the offer to cover different packages | that offer's `condition.package_keys` |
| retire an offer without deleting it | `"available": false` on that offer |
| a different voucher code | that offer's `code` (unique per campaign, `A-Z0-9`) |
| add a bump package | a new `packages[]` entry: a fresh `key`, `role: "bump"`, `name`, `variant_title`, `product_id`, `product_variant_ids` (a variant no other package uses), `price` |
| add an offer | a new `offers[]` entry: a fresh `key`, `name`, `offer_type`, `condition`, `benefit`, and `code` for a voucher |
| set or replace a package image | `"image": {"src": "https://...", "file_name": "hero"}` on that package |
| remove a package | delete its `packages[]` entry (a DELETE: see U4 and `--allow-delete`) |
| remove an offer | delete its `offers[]` entry (a DELETE) |
| remove a shipping method | delete its `shipping_methods[]` entry (a DELETE) |

What cannot be changed, and why:

| Not changeable | Reason | What to do instead |
|---|---|---|
| `campaign.currency` | immutable on the API once the campaign exists | a different currency needs a new campaign |
| `product_id` or `product_variant_ids` on an existing package key | the key is that package's identity in the manifest | give the new variant its own package key and delete the old one, in that order |
| `shipping_method` on an existing shipping key | the store code is the method's identity, and sending another method's code is a 400 | add a method on the new code and delete this one |
| `all_packages` scope | a plan holds package keys, so it cannot express it | name the packages, or keep the offer out of the plan's reach |
| removing an `image` | the API has no delete-image route | `diff` emits a warning and no op; change the image in the dashboard |

A new package's key must be one the canonical plan does not use and the manifest
does not own, or `update` refuses the POST as a duplicate. Keep the engine's own
shapes for readability: `pkg-{variant_id}` for a package, `offer-{something}`
for an offer.

---

## U4: Diff

Read-only. It builds the change set and writes two files; it sends nothing.

```bash
bash <skill-dir>/next-campaigns-create.sh diff \
  --plan ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.next.json \
  --manifest ./next-campaigns-create-runs/<subdomain>-<id>/run-manifest.json
```

The diff is three-way: the canonical plan (base), the edited copy (candidate)
and the store (live). Per field:

| Base, candidate, store | Result |
|---|---|
| the candidate left it alone, the store changed it | preserved: the store's value wins, no request, and it is carried into the merged plan |
| the candidate changed it, the store did not | a PATCH |
| the store is already at the candidate's value | nothing is sent |
| all three differ | a conflict: the whole diff refuses and prints the 3 values |

Per key: only in the candidate is a POST, only in the base is a DELETE, and a
live object in neither plan is unmanaged and refuses the whole diff.

What `diff` prints, and how to read it:

- The three hashes: the base plan, the merged plan file it wrote
  (`campaign-plan.<sha8>.json`), and the baseline (the store as reviewed).
- `Kept from the store (the candidate did not touch these)`: every preserved
  value. Read these out: they are changes someone made in the dashboard that
  this update is deliberately keeping.
- `Warnings`: a dropped `image.src`, or a DELETE of an object the store changed
  that `--delete-changed` authorised.
- `Requests update will send, in order (N)`, with `, including DELETEs` when any
  op is a DELETE. Each line is the method, the section, the key, the exact path
  and the exact body, with `before` and `after` underneath.
- `wrote ...` for the merged plan and `change-set.json`.
- `Approve with: ...`, the exact `update` command including the change set's
  hash and `--allow-delete` when it is needed.

`diff` exits 0 with ops, 0 with "no changes: the candidate plan matches both the
base plan and the store", and 1 on any refusal. A change set with 0 ops and some
preserved values is still worth applying: `update` sends nothing and promotes
the merged plan so the plan matches the store again.

Then get an explicit go/no-go with `AskUserQuestion`. Call the DELETE steps out
by name; never fold them into a count:

> Ready to update campaign {id} on {subdomain}.29next.store: {N} requests. It
> will delete {offer "X" / package Y / shipping method Z} permanently, and
> change {short list of the rest}. It keeps {the preserved values}, which were
> changed in the dashboard since this plan was written. The request list is
> above. Proceed?
>
> - A) Yes, send exactly these requests
> - B) Change something first (I edit the copy and re-run the diff)
> - C) No, stop here (nothing has been sent to the store)

Halt on C. Nothing remote has happened at that point.

`--delete-changed <section>:<key>` exists for one case: the operator wants to
delete an object that the store changed since the base plan. The diff refuses
that delete by default, printing every changed field with its base and store
values, because the baseline hash only protects edits made after the diff.
Review those values with the operator, then re-run with the flag. It is recorded
in the change set's `warnings[]`. Sections are `packages`, `shipping_methods`
and `offers`; the key is the plan key, for example `offers:tier-2`.

---

## U5: Update

Only after an A at the gate. Two gates, both required:

1. `--yes` plus `--change-set-sha256 <hash of change-set.json>`. Without them
   `update` prints the change set and exits 2, having built no client and sent
   nothing.
2. `--allow-delete` on top, for any change set that carries a DELETE. Without it
   the run exits 2 before the first request.

```bash
bash <skill-dir>/next-campaigns-create.sh update \
  --plan ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.<sha8>.json \
  --manifest ./next-campaigns-create-runs/<subdomain>-<id>/run-manifest.json \
  --change-set ./next-campaigns-create-runs/<subdomain>-<id>/change-set.json \
  --yes --change-set-sha256 <change-set-sha256> [--allow-delete]
```

Use the command the latest `diff` printed and nothing else. Never pass a hash
from an earlier diff, and never hand-assemble the flags: `--plan` has to be the
merged plan file that diff wrote, byte for byte.

Before the first request, in this order: the change set is bound to this run
(run id, store, campaign id, the canonical plan's hash, every op's route and
owned id, the embedded merged plan against `--plan`); the DELETE approval is
checked; the store is re-read and the baseline hash recomputed, and a mismatch
refuses with "campaign changed since you reviewed the diff; re-run diff"; the
ownership read-back runs. All of those are exit 2 or exit 1 with nothing sent.

Then it sends, in this order: the campaign PATCH, offer DELETEs, package DELETEs,
package PATCHes with any image PUT behind them, package POSTs each followed by
its image PUT, shipping DELETEs then PATCHes then POSTs, offer PATCHes, offer
POSTs. Each op is journalled `in_flight` and saved to the manifest before it is
sent and `done` once the response is in. A clean run prints one line per op
(`updated packages pkg-801`, `created packages pkg-903 id 118`,
`deleted offers tier-3`) and then:

```
DONE: campaign <id> '<name>' on https://<subdomain>.29next.store
  campaign-plan.json is the merged plan now (<sha8>)
  package pkg-<variant>: id <package id>  (data-next-package-id="<package id>")
  ...
  offer tier-2: id <offer id>
  removed offers tier-3 (id <offer id>)
  the campaign api_key does not change; it stays in <dir>/run-manifest.json
```

The merged plan becomes the run's canonical `campaign-plan.json` at the end, in
4 recoverable steps, and the old one is kept beside it as
`campaign-plan.<oldsha8>.json`. Every archive in the run directory is a byte
copy of a plan this run has held.

---

## U6: Verify and hand off

```bash
bash <skill-dir>/next-campaigns-create.sh verify \
  --manifest ./next-campaigns-create-runs/<subdomain>-<id>/run-manifest.json \
  --plan ./next-campaigns-create-runs/<subdomain>-<id>/campaign-plan.json
```

Pass the canonical plan, which is the merged plan now. `verify` reads every
object back field by field and exits 1 on FAIL. On an adopted campaign it proves
read-back only: `landed_prices` is empty, so there are no carts to price and one
INFO row says so. INFO and UNVERIFIED rows do not fail the report, and the
verdict line counts them.

Then hand off what the funnel has to act on. This is the part an update gets
wrong most easily:

- **Every id a new POST created.** A new package id has to be added to the
  funnel markup as `data-next-package-id`, and a new shipping method id has to
  be sent by the pages that should charge it.
- **Every id a DELETE removed.** Any page naming a deleted package or shipping
  id has to be repointed. Deleting an object and creating it again gives a new
  id: ids are never reused.
- **Changed voucher codes.** A renamed or re-coded voucher breaks any page that
  hard-codes the old code.
- **What did not change.** The campaign api_key is the same before and after an
  update, and a PATCH never changes an id. A package whose price changed keeps
  its id, so the funnel needs no change for a price edit.

An offer the update retired with `available: false` still exists and keeps its
id; it simply stops firing. Say which it was.

---

## Interrupted runs

An interrupted update leaves `active_update` and an op journal on the manifest.
While it is there, `diff`, `verify`, `teardown` and `apply --resume` all refuse
with "an update is in progress; finish it with update --resume", and `update`
itself refuses without `--resume` or `--settle`. That is deliberate: the plan and
the store do not agree yet, so nothing else can reason about the run.

Both recovery flags take the **same** change set and the same hash as the
original command, plus `--plan` and `--yes` as usual.

### `update --resume`

For a run that stopped and can still finish: a 5xx, a dropped connection, a
killed process, a rate limit.

It resolves every op left `in_flight` by reading the store. A PATCH is at
`before` (so it is re-sent) or at `after` (so it is marked done); anything else
refuses by name. A DELETE whose object is gone is done. A POST is matched by
identity, read back in full, and only claimed when every field equals the op's
`after`: a same-named object with different contents is never claimed, and more
than one candidate refuses. An image PUT is re-sent only when the live thumbnail
still equals the one the diff recorded.

Then it checks the **whole** campaign against the state this update left: the
change set's reviewed baseline with the `after` of every completed op applied.
Any other difference, on any object or field, refuses and names it. That is what
catches an edit made in the dashboard while the update was not running, before
the resume could overwrite it.

### `update --settle`

For a run that **cannot** finish: a 400 on a PATCH the store will keep
rejecting, a rejected image `src`, or a resume that refuses on drift. Without it
a rejected write would leave the run behind `active_update` forever.

`--settle` sends nothing. It reads back every op left in flight, writes the plan
that describes what actually landed (the base plan plus the `after` of every
applied op, created keys added and deleted keys dropped), validates it, promotes
it the same way a completed update does, records each op's outcome in the
manifest's `history[]`, and clears `active_update`. It prints how many of the
ops had landed and one line per op. The operator then runs a fresh `diff` for
the work that is left.

An op the engine cannot resolve to either state refuses: the operator has to put
that object back to one of the two values in the dashboard first. `--settle` and
`--resume` cannot be passed together.

### The run lock

Every command that can write the run directory (`adopt`, `diff`, `update`,
`apply`, `verify`, `teardown`) holds an exclusive `.run.lock` in that directory
for its whole life, naming the holder's pid and start time. A second command
refuses at once. A lock left behind by a killed process is removed by the
operator, never automatically, and never without confirming that no command is
still running.

---

## Refusals

Every refusal below sends nothing. Exit 2 is the gate family (`update`'s own
checks); everything else exits 1.

| Message (abbreviated) | Cause | Fix |
|---|---|---|
| `another next-campaigns-create.sh command holds this run directory` | a second command on the same run, or a lock left by a killed process | wait for it; if the pid is gone, delete `.run.lock` by hand after confirming nothing is running |
| `an update is in progress; finish it with update --resume` | `active_update` on the manifest | `update --resume`, or `update --settle` if it cannot finish |
| `campaign changed since you reviewed the diff; re-run diff` | the store moved between the diff and the update | re-run `diff`, review the new change set, approve that one |
| `conflicting edits: the store changed what you changed` | base, candidate and store all differ on one field | set the candidate to the store's value to keep it, or put the store back to the base value, then diff again |
| `the campaign carries object(s) this run does not own` | a package, shipping method or offer added outside this run | remove it in the dashboard, or adopt the campaign into a fresh run directory and edit that plan |
| `cannot be deleted: the store changed it since the base plan` | the object to be deleted is not what the plan recorded | review the printed values, then re-run `diff --delete-changed <section>:<key>` |
| `cannot be deleted while live offer(s) ... scope it` | a package a surviving offer still covers; the API refuses that delete | narrow or delete those offers in their own update first, then delete the package |
| `would set the name ... which live offer N still holds at that point` | two offers swapping a name or a voucher code | do it as 2 updates through a temporary name, or reorder so the offer holding it changes first |
| `is scoped to all_packages` (adopt) | a live offer a plan cannot represent | re-run `adopt` with the printed `--convert-scope <offer_id>`, or narrow the offer in the dashboard |
| `is a count offer whose threshold reads back as ...` (adopt) | a threshold that is missing or not a whole number | read it in the dashboard and correct the offer there, then adopt again |
| `this change set deletes object(s) ... and --allow-delete was not passed` | the DELETE approval | re-read the DELETE steps, then add `--allow-delete` to the same command |
| `this change set has already been applied` | the same change set run twice | run `diff` again for the next change |
| `was written against plan ... and this run is on plan ...` | a stale change set from before another update | re-run `diff` |
| `the canonical plan has been edited in place` | `campaign-plan.json` was edited directly | restore it (the archives in the run directory are byte copies), then diff again; edits belong in the copy |
| `--plan is the canonical plan itself` | `diff` was given the base plan | pass the edited copy |
| `campaign currency cannot change once the campaign exists` | a currency edit | a different currency needs a new campaign |
| `product_id and product_variant_ids cannot change on a package that exists` | a variant edit on an existing key | new key for the new variant, delete the old package |
| `the store code cannot change on a campaign shipping method that exists` | a code edit on an existing key | add a method on the new code, delete this one |
| `the merged plan (your edits plus the values preserved from the store) is invalid` | a preserved dashboard change collides with an edit (two offers now share a name, say) | reconcile them in the candidate and diff again |
| `the campaign is not in the state this interrupted update left it in` | the campaign was edited while the update was not running | review the named values, then `update --settle` and run a fresh diff |
| `op N: ... returned 4xx` | the store rejected that write; nothing after it was sent | fix the cause and `update --resume`, or close it with `update --settle` |
| `this run's plan promotion was interrupted and the merged plan it promotes to ... is gone` | the merged archive was deleted mid-promotion | restore that file (the change set names it in `merged_plan_file`) and run the command again |
| `teardown deletes what this run created` | `teardown` on an adopted campaign | reduce an adopted campaign with `update --allow-delete`; the campaign itself is deleted in the dashboard |
| `was adopted from an existing campaign, not created by a run` | `apply --resume` on an adopted manifest | change an adopted campaign with `diff` and `update` |

---

## Limits

- **No pricing proof for an adopted campaign.** Nothing on the store says what
  anchor the discounts came off, so `adopt` writes `landed_prices: []` and
  `verify` cannot price a cart. It prints one INFO row and proves field
  read-back only. To get pricing proof, add landed rows by hand or probe
  `carts/calculate` yourself at the thresholds that matter.
- **An image cannot be removed over the API.** There is no delete-image route,
  so dropping `image` from the candidate produces a warning and no op. Change
  the image in the dashboard.
- **The offers endpoint has to exist on the store.** `adopt` and `diff` both
  read the campaign's offers, so on a store whose build predates the Offers API
  they fail on that read and the update path cannot be used at all. `discover`
  probes for the endpoint. Where it is live, offers built in the dashboard are
  read and adopted like any other.
- **`available: false` is the retirement mechanism.** Per-customer limits and
  date ranges are not offer fields at all.
- **The update path has not been exercised against a live store.** The reads
  behind it were confirmed read-only against live stores on 2026-10-08. The
  write shapes (the PATCH bodies, package DELETE while an offer references it,
  the accepted clearing values) are implemented from the published contract and
  proven against the offline fake only. `admin-api-contract.md` lists exactly
  what is still unverified.
