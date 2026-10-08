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

## This path or `edit`?

`edit` (SKILL.md Phase 7) is the other way to change a campaign, and it is the
first thing to reach for when both would work. It takes a short list of field
operations on a campaign a run of this skill **created**, previews each value old
to new, writes a receipt of the before-images and takes `--undo`. Its scope is
narrow: a package price, an offer's percentage or rounding, an offer's condition
or scope, pausing or resuming an offer, and adding one offer.

This path is the one to use when:

- the campaign was built elsewhere, or its run directory was lost (`edit` refuses
  a manifest whose `origin` is `adopted`, because it proves a change by
  recomputing the plan's landed prices and an adopted plan has none);
- the change is to the campaign's own settings, a shipping method's price, or
  adding or removing a package or a shipping method;
- the change is a delete;
- `edit` refuses the change for any other reason, naming the field.

The two never run at once. While an edit is journalled and unfinished, `diff` and
`update` refuse with "an in-place edit of this run is unfinished"; while an update
is journalled, `edit` refuses with "an update is in progress". Both move the
canonical plan and the manifest's `plan_sha256` together, so either can follow the
other: a `diff` straight after an edit reads the edited plan as its baseline, and
an `edit` straight after an update works from the promoted plan.

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

For an adopted campaign the request list is headed "What this campaign holds,
as the requests that would create it" and the last line says nothing is sent.
It is a description, not a to-do list. For a campaign this skill created, the
same command still ends with the `apply` approval line from the create path;
do not run it again, because `apply` refuses a directory that already holds a
manifest.

`adopt` also prints a role table:

```
Roles (a heuristic from the offers; correct one in your edited copy and diff it. A role correction lands on its own, with nothing sent to the store):
  pkg-801        variant 801 (v801)  hero
  pkg-802        variant 802 (v802)  bump
```

`role` is plan-only: it is never sent to the API, and changing it is never an
op. It drives `verify`'s hero and free-shipping logic, so a wrong role is worth
correcting. Correct it in the copy (U3), not in `campaign-plan.json`: editing
the canonical plan in place breaks its hash and `diff` refuses the run. A
role-only edit lands on its own. `diff` decides whether there is anything to do
by comparing the merged plan with the base plan, not by counting requests, so a
role correction gives a change set with 0 ops and one `plan_only` line, and
`update` promotes the merged plan without sending anything. It can travel with a
real change too; neither is a precondition for the other.

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
| a price in any currency but the campaign's | the plan carries one price per package, in the campaign currency | set the other currencies in the dashboard; `diff` warns when a price op leaves them behind |

`landed_prices` and `voucher_codes` need no editing when an offer or a package
goes. They are advice, not desired state: nothing is ever sent from either, and
`verify` is their only reader. A row that prices an object the change deletes is
dropped from the merged plan by `diff` itself, with a warning naming the row, and
`update --settle` does the same when a DELETE landed but the update could not
finish. A row naming a key that never existed is still an invalid plan, so a
typo in one is reported rather than quietly removed.

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

A package `image` is the one field not diffed against the store. The store reads
back a thumbnail it built itself, so the comparison is between the candidate's
`image.src` and the src this run recorded, and a PUT goes out only when those two
differ. The recorded src is `image_src` on the manifest entry, written when an
image lands. A run written before that field existed (0.7.5, 0.8.0) carries only
`image_status` and the thumbnail, and for one of those the **base plan's**
`image.src` is the recorded intent: the base plan is hash-verified against the
manifest, which makes it that run's own record of what it asked for. So a diff of
an unchanged copy of such a plan sends nothing: re-sending the PUT would overwrite
an image changed in the dashboard since with the original source. An adopted plan
carries no `image` at all, so the first src an operator adds to one is a change
like any other, and the completed update records it as `image_src`.

The same rule has one other effect. A package whose image PUT never landed under
an older run (`image_status` `pending` or `failed`, so no `image_src` was
recorded) is not re-sent by a diff of the unchanged plan either. A `pending` one
is retried by `apply --resume`; `failed` is terminal by design, because the src
cannot change without changing the plan.

What `diff` prints, and how to read it:

- The three hashes: the base plan, the merged plan file it wrote
  (`campaign-plan.<sha8>.json`), and the baseline (the store as reviewed).
- `Kept from the store (the candidate did not touch these)`: every preserved
  value. Read these out: they are changes someone made in the dashboard that
  this update is deliberately keeping.
- `Plan only, nothing sent (...)`: every way the merged plan differs from the
  base plan that no request carries, one line each as `what: before -> after`.
  A `role` correction, a value the candidate and the dashboard converged on, an
  advisory row dropped with the object it priced. Also in `change-set.json` as
  `plan_only`.
- `Warnings`: a dropped `image.src`; a DELETE of an object the store changed that
  `--delete-changed` authorised; a `landed_prices` or `voucher_codes` row dropped
  because the offer or package it names is being deleted; and, on a campaign that
  prices in more than one currency, every price op that leaves the other
  currencies alone. Read these out: the last two are the ones an operator is
  surprised by afterwards.
- `Requests update will send, in order (N)`, with `, including DELETEs` when any
  op is a DELETE. Each line is the method, the section, the key, the exact path
  and the exact body, with `before` and `after` underneath.
- `wrote ...` for the merged plan and `change-set.json`.
- `Approve with: ...`, the exact `update` command including the change set's
  hash and `--allow-delete` when it is needed.

`diff` exits 0 with ops, 0 with "no changes: the candidate plan matches both the
base plan and the store", and 1 on any refusal. "No changes" means the merged
plan equals the base plan, value for value once both are parsed, and nothing is
written. Anything else is written, including a change set with 0 ops: values
preserved from the store, a plan-only field such as `role`, a value the
candidate and the dashboard reached independently, an advisory row dropped with
the object it priced. `update` on one of those sends nothing and promotes the
merged plan, so the canonical plan is the current state again. A diff with
warnings and no change prints the warnings anyway, because a warning is about
the candidate rather than about the change.

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
checked; the store is read; the ownership read-back runs on the immutable
fields; and last the baseline hash is recomputed, a mismatch refusing with
"campaign changed since you reviewed the diff; re-run diff". Nothing is sent if
any of them refuses. The ownership check comes before the baseline hash on
purpose: a campaign whose ids have moved is not drift, it is the wrong
campaign, and it says so instead. Which code each one exits with is in Refusals
below; that table is the only place this skill states it.

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
While it is there, `diff`, `verify`, `teardown`, `edit` and `apply --resume` all refuse
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
than one candidate refuses. A POST that had already landed and been journalled is
left alone: its object is this run's from that moment, and the op is not re-sent.
An image PUT on a package that already existed is re-sent only when the live
thumbnail still equals the one the diff recorded, so an image changed in the
dashboard during the interruption is kept. On a package this same change set
created, the thumbnail proves nothing (the create attaches the catalogue image by
itself), so the PUT is simply sent again.

Then it checks the **whole** campaign against the state this update left: the
change set's reviewed baseline with the `after` of every completed op applied.
Any other difference, on any object or field, refuses and names it. That is what
catches an edit made in the dashboard while the update was not running, before
the resume could overwrite it. An offer's `all_packages` is compared there too,
although no plan can hold it: switching an offer to the whole campaign leaves
its package list untouched, so no other field would show the change, and the
discount would go on to cover packages nobody reviewed. No plan field holds it, so
no op field can either: for a completed op the value is derived from the op rather
than carried over from the baseline. An offer op that sends a `condition` (a POST,
or a PATCH of the scope) pins the offer to package ids, so `all_packages` is false
after it. That is what lets a resume accept a scope conversion it landed itself,
and still refuse an offer widened in the dashboard after a POST created it.

### `update --settle`

For a run that **cannot** finish: a 400 on a PATCH the store will keep
rejecting, a rejected image `src`, or a resume that refuses on drift. Without it
a rejected write would leave the run behind `active_update` forever.

`--settle` sends nothing. It reads back every op left in flight, writes the plan
that describes what actually landed (the base plan plus the `after` of every
applied op, created keys added and deleted keys dropped, and any `landed_prices`
or `voucher_codes` row that priced a dropped key dropped with it), validates it,
promotes it the same way a completed update does, records each op's outcome in
the manifest's `history[]`, and clears `active_update`. It prints how many of the
ops had landed, one line per op, and a `note:` line for every advisory row it
dropped. The operator then runs a fresh `diff` for
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

This is the one place that says which exit code each refusal uses; `SKILL.md`'s
exit-code table is a summary of it. Every refusal here sends nothing, with one
exception named below: a write the store rejected mid-run, where the ops before
it had already gone out.

Every exit 2 prints a line starting `NOT APPLIED`, and there are two forms of it
on the update path. The hash gate prints the change set and then:

```
NOT APPLIED: pass --yes --change-set-sha256 <the hash> to approve this exact change set.
```

Every other gate prints the reason and then one fixed closing line:

```
ERROR: <the reason>
NOT APPLIED: nothing was sent to the store.
```

An exit 1 prints the `ERROR:` line on its own, with no `NOT APPLIED` after it, so
the closing line is what tells a gate apart from a refusal that read the store.
`edit` has its own pair of forms, both opening `NOT APPLIED: nothing was
written.`; `SKILL.md`'s exit-code section lists all four.

`update` exits 2 for the gate family: everything it checks about the paperwork
before it looks at the store.

- A missing `--yes`, or a `--change-set-sha256` that is not the hash of the
  change set file.
- A DELETE in the change set without `--allow-delete`.
- Any binding check: the change set was written for another run, store or
  campaign; it was written against a different canonical plan (including "this
  change set has already been applied"); `campaign-plan.json` on disk no longer
  hashes to what it was written against; `--plan` is not the merged plan the
  change set names, byte for byte; an op is out of order, carries a method this
  engine does not send, names a route or an id the manifest does not own, or is
  a POST on a key this run already owns.
- An `active_update` on the manifest without `--resume` or `--settle`, a
  `--resume` or `--settle` naming a different change set, one with no update in
  progress at all, and both flags together.
- Baseline drift: "campaign changed since you reviewed the diff; re-run diff".
  The store is re-read and nothing has been written at that point, so it is a
  gate like the others.

`update` exits 1 for everything else:

- The ownership read-back: "ownership check failed; refusing to update". It
  compares the immutable fields (ids, `created_at`, `product_variant_id`, store
  codes) against the store, so a failure means the manifest does not describe
  that campaign any more. Nothing is sent, and it is still exit 1: it is a
  statement about the campaign, not about the operator's paperwork.
- A manifest this run cannot use: a campaign entry that is not `created`, an
  entry left `pending` by an unfinished `apply`, a torn-down run, a tampered
  `store_slug` or `store_origin`, a journal that does not describe this change
  set's ops, or a promotion whose merged archive is gone.
- Every `--resume` and `--settle` refusal that reads the store: an op left in
  flight that is at neither its `before` nor its `after`, a campaign that is not
  in the state the interrupted update left it in, an object this run cannot
  prove it created, a settled plan that does not validate.
- Any store rejection once sending has begun (`op N: ... returned 4xx`) and an
  image override that did not land. This is the one case where requests before
  the refusal did go out; the journal says which, and `--resume` or `--settle`
  takes it from there.

`diff` and `adopt` exit 1 on every refusal: neither has a gate of its own and
neither writes to the store at all.

| Message (abbreviated) | Code | Cause | Fix |
|---|---|---|---|
| `another next-campaigns-create.sh command holds this run directory` | 1 | a second command on the same run, or a lock left by a killed process | wait for it; if the pid is gone, delete `.run.lock` by hand after confirming nothing is running |
| `an update is in progress; finish it with update --resume` | 1, or 2 from `update` | `active_update` on the manifest | `update --resume`, or `update --settle` if it cannot finish |
| `campaign changed since you reviewed the diff; re-run diff` | 2 | the store moved between the diff and the update | re-run `diff`, review the new change set, approve that one |
| `conflicting edits: the store changed what you changed` | 1 | base, candidate and store all differ on one field | set the candidate to the store's value to keep it, or put the store back to the base value, then diff again |
| `the campaign carries object(s) this run does not own` | 1 | a package, shipping method or offer added outside this run | remove it in the dashboard, or adopt the campaign into a fresh run directory and edit that plan |
| `cannot be deleted: the store changed it since the base plan` | 1 | the object to be deleted is not what the plan recorded | review the printed values, then re-run `diff --delete-changed <section>:<key>` |
| `cannot be deleted while live offer(s) ... scope it` | 1 | a package a surviving offer still covers; the API refuses that delete | narrow or delete those offers in their own update first, then delete the package |
| `would set the name ... which live offer N still holds at that point` | 1 | two offers swapping a name or a voucher code | do it as 2 updates through a temporary name, or reorder so the offer holding it changes first |
| `is scoped to all_packages` (adopt) | 1 | a live offer a plan cannot represent | re-run `adopt` with the printed `--convert-scope <offer_id>`, or narrow the offer in the dashboard |
| `is a count offer whose threshold reads back as ...` (adopt) | 1 | a threshold that is missing or not a whole number | read it in the dashboard and correct the offer there, then adopt again |
| `this change set deletes object(s) ... and --allow-delete was not passed` | 2 | the DELETE approval | re-read the DELETE steps, then add `--allow-delete` to the same command |
| `this change set has already been applied` | 2 | the same change set run twice | run `diff` again for the next change |
| `was written against plan ... and this run is on plan ...` | 2 | a stale change set from before another update | re-run `diff` |
| `the canonical plan has been edited in place` | 1 from `diff`, 2 from `update` | `campaign-plan.json` was edited directly | restore it (the archives in the run directory are byte copies), then diff again; edits belong in the copy |
| `--plan is the canonical plan itself` | 1 | `diff` was given the base plan | pass the edited copy |
| `campaign currency cannot change once the campaign exists` | 1 | a currency edit | a different currency needs a new campaign |
| `product_id and product_variant_ids cannot change on a package that exists` | 1 | a variant edit on an existing key | new key for the new variant, delete the old package |
| `the store code cannot change on a campaign shipping method that exists` | 1 | a code edit on an existing key | add a method on the new code, delete this one |
| `the merged plan (your edits plus the values preserved from the store) is invalid` | 1 | a preserved dashboard change collides with an edit (two offers now share a name, say) | reconcile them in the candidate and diff again |
| `the campaign is not in the state this interrupted update left it in` | 1 | the campaign was edited while the update was not running | review the named values, then `update --settle` and run a fresh diff |
| `op N: ... returned 4xx` | 1 | the store rejected that write; nothing after it was sent | fix the cause and `update --resume`, or close it with `update --settle` |
| `this run's plan promotion was interrupted and the merged plan it promotes to ... is gone` | 1 | the merged archive was deleted mid-promotion | restore that file (the change set names it in `merged_plan_file`) and run the command again |
| `teardown deletes what this run created` | 1 | `teardown` on an adopted campaign | reduce an adopted campaign with `update --allow-delete`; the campaign itself is deleted in the dashboard |
| `was adopted from an existing campaign, not created by a run; apply --resume only finishes a run it started` | 1 | `apply --resume` on an adopted manifest. A plain `apply` does not reach it: the check is in the resume branch | change an adopted campaign with `diff` and `update` |
| `was adopted from an existing campaign, not created by a run; edit changes a campaign this skill built and proves it against the plan's landed prices, which an adopted plan does not have` | 1 | any `edit` on an adopted manifest, `--changes`, `--undo` and a resumed edit alike: it refuses after the plan is read and before the token is used | change an adopted campaign with `diff` and `update` |

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
- **Only the campaign currency is priced.** A plan holds one price per package
  and per shipping method. On a campaign with `additional_currencies`, a price
  PATCH names the campaign currency and the endpoint leaves the others
  unchanged, so they keep whatever they were (a campaign create fills them by
  forex; an update does not). `diff` warns for every price op on such a
  campaign, naming the op and the currencies, and the operator sets those in the
  dashboard. The engine does not send `recalculate_prices`: it would convert
  every other currency from the one being sent and overwrite a price someone set
  by hand.
- **No write on the update path has been sent to a live store by this engine.**
  Not a PATCH, not a POST, not a PUT, not a DELETE on a campaign that already
  exists. The write shapes (the PATCH bodies, a package DELETE while an offer
  references it, the accepted clearing values) are implemented from the
  published contract and proven against the offline fake only.
  `admin-api-contract.md` lists exactly what is still unverified.
- **The read side's response shapes were observed with GET requests on
  2026-10-08.** That is a separate, narrower statement, and it says nothing
  about any write. The record of what was read, what it does and does not
  settle, and how to repeat it on your own store is in
  `admin-api-contract.md`, "Read-only checks, 2026-10-08".
