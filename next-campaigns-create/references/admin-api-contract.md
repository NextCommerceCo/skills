# next-campaigns-create: Admin API contract and file schemas

Companion to `SKILL.md`. Field-level detail for `scripts/campaign_admin.py`. The
sources are the public developer docs: the provisioning flow at
<https://developers.nextcommerce.com/docs/campaigns/admin-api>, the per-endpoint
reference pages under
<https://developers.nextcommerce.com/docs/admin-api/reference/campaigns/>, and the
token permissions at
<https://developers.nextcommerce.com/docs/admin-api/permissions>. The reasoning
behind each recommendation is in [offer-doctrine.md](offer-doctrine.md).

## Auth and host

- Host: `https://<slug>.29next.store`, for example `https://mystore.29next.store`.
  Admin base: `/api/admin/`.
- Headers: `Authorization: Bearer <token>` and `X-29next-API-Version: 2024-04-01`.
- The version header is load-bearing. The campaign endpoints do not exist on the
  older `2023-02-10` API version, so a request without the header finds no
  campaign routes at all.

Token permissions:

| Permission | Used by |
|---|---|
| `store:read` | `discover` (`GET /store/`, its first request) |
| `campaigns:read`, `campaigns:write`, `catalogue:read`, `gateways:read` | `discover`, `plan --check-store`, `apply`, `verify`, `teardown` |
| `metadata:read` | the metadata audit in `discover` |
| `metadata:write` | `metadata --apply` |

`GET /shipping-methods/` carries no scope in the published spec, so any valid
token may call it. Source for every scope above: the 2024-04-01 OpenAPI file at
<https://developers.nextcommerce.com/api/admin/2024-04-01.yaml>. A 401 or 403
from the engine names the scope the failing request needed (`scope_for()` and
`auth_hint()` in the engine).

The Cart API used by `verify` is a different host, `https://campaigns.apps.29next.com`.
It takes the campaign `api_key` raw in `Authorization`, with no `Bearer` prefix and
no version header.

Three auth schemes are in play, and the wrong one fails quietly. The Admin API
wants `Bearer <token>`. The Cart API wants the raw campaign key. On the Admin API,
`Token <key>` and the bare key both return 401, which looks like a bad token rather
than a bad scheme.

Credential boundary: the Admin token is a full-access store credential. It belongs
in a gitignored `.env` file and never in funnel pages. The campaign `api_key`
returned by create is the only credential that is safe in browser code. Rotate any
token that has been pasted into a chat or a ticket once the work is done.

## Endpoints used

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/admin/store/` | enabled currencies and languages |
| GET | `/api/admin/gateway-groups/` | id, method codes, currencies per group |
| GET | `/api/admin/shipping-methods/` | store shipping codes |
| GET | `/api/admin/products/` | products, variants (id, sku, price, availability) |
| GET | `/api/admin/metadata/` | the 11 campaign/attribution definitions |
| POST | `/api/admin/metadata/` | create a missing metadata definition (`metadata --apply`; sent without the version header) |
| GET/POST | `/api/admin/campaigns/` | list (cursor paginated) / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/` | retrieve (returns `api_key`) / rename a clone destination / delete |
| POST | `/api/admin/campaigns/{id}/clone/` | bodyless clone request, availability depends on store deployment |
| GET/POST | `/api/admin/campaigns/{id}/packages/` | list / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/packages/{packageId}/` | read back / edit the price / delete |
| PUT | `/api/admin/campaigns/{id}/packages/{packageId}/image/` | replace the package image (optional override) |
| GET/POST | `/api/admin/campaigns/{id}/shipping-methods/` | list / create |
| DELETE | `/api/admin/campaigns/{id}/shipping-methods/{id}/` | delete |
| GET/POST | `/api/admin/campaigns/{id}/offers/` | list / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/offers/{offerId}/` | retrieve / edit / delete |
| POST | `campaigns.apps.29next.com/api/v1/carts/calculate/` | pricing truth (verify); `?upsell=true` for upsell carts |

## Field notes and live gotchas

- List responses: `/campaigns/` is cursor paginated (`next`, `page_size`). The
  package, shipping-method and offer lists may come back as a plain array or in a
  `{results, next}` envelope. The client reads both shapes.
- Campaign create requires `name` (at most 200 characters), `currency`,
  `language` and `payment_gateway_group_id`. Optional: `additional_currencies`,
  `available_payment_methods` and `available_express_payment_methods` (codes from
  the chosen gateway group), `available_shipping_countries` (alpha-2),
  `paypal_account_id`, `statement_descriptor` (at most 255). The response has `id`
  and `api_key`; `GET /campaigns/{id}/` returns `api_key` again, so a lost create
  response is recoverable.
- Currency is immutable. `currency` cannot be changed after the campaign is
  created, so pick it together with the gateway group: the group must list the
  currency and every payment method code the campaign enables.
- Package create returns a one-element list, not an object. Send
  `product_variant_ids` as a list; read back a single `product_variant_id`. The
  API appends `" - {variant title}"` to the package name for variant packages, so
  the plan sends the bare product name and records `variant_title` separately.
  `price` and `price_recurring` are write-only; read prices from `prices[]`.
- Package image (optional): a package may carry
  `image: {"src": "https://...", "file_name": "hero"}`. `apply` PUTs it to
  `/packages/{id}/image/` immediately after creating that package, and the request
  list printed at the gate shows it. Before hand-authoring one:
  - You usually do not need it. Package create already attaches the catalogue
    variant's image, or the parent product's. Set an override only when that image
    is wrong or absent.
  - Create can come back with `image: null` and a 201 when the catalogue image
    could not be fetched. Nothing in the response says so; check the field rather
    than trusting create.
  - `src` only, and it must be https. No base64: a plan has to stay reviewable,
    and the manifest already holds the campaign `api_key`. Validation forbids
    http, `data:`, relative URLs, credentials, whitespace and control characters.
  - Use a durable, public, unsigned URL. Query strings are allowed because CDNs
    need them for sizing, so nothing stops you pasting a signed link, but that
    credential would be printed at the gate and stored in the plan and manifest.
    This one is on the operator, not the validator.
  - `file_name` is cosmetic and loses its extension, which the server takes from
    the image bytes. `hero.jpg` is stored as `hero`.
  - Limits are 10 MB and 25 megapixels, applied independently. Accepted types are
    jpg, jpeg, png, ico, gif and webp.
  - One override per package, not per product. A twelve-variant hero with one
    shared image means twelve PUTs, twelve server-side fetches of the same file,
    and twelve lines for the operator to read at the gate.
  - The `image` you read back is never the URL you sent. It is a thumbnail the
    server builds, on a path derived from the image content, so comparing it to
    `src` always fails, and re-uploading the picture the package already had
    returns the identical URL.
  - Degradation, with no discover probe. The route has no read verb, and a new
    store has no existing package to probe against, so support is discovered at
    apply time. A 405, or a 404 before any image has landed, means the store does
    not have the endpoint: every override is marked `unsupported` and handed to
    the dashboard. A 404 after one has landed is read as that package being
    missing, not the route, so it is marked `failed` rather than `unsupported`
    (the endpoint plainly exists) and the rest still go. A 429, any 5xx, or a
    dropped connection stays `pending` and is retried by `--resume`. Anything
    else is `failed` and terminal: the `src` cannot change without changing the
    plan hash, so neither `--resume` nor a fresh run can correct it. Fix it in the
    dashboard, or teardown and recreate.
  - `recommend` never emits this field. It builds from the catalogue, which is
    the same source the image already comes from.
- Shipping create takes the store `shipping_method` code and a `price` in the
  campaign's default currency; other currencies are filled by forex.
- Uniqueness: a campaign takes one package per variant and one campaign shipping
  method per store code.
  - The skill enforces both itself. `validate_plan` applies them for
    `recommend`, `plan` and `apply`, and skips them for `verify`
    (`for_create=False`) so a run created earlier stays verifiable. Before the
    rule, one code could carry several campaign methods at different prices (a
    paid-shipping ladder); new plans cannot.
  - The dashboard's campaign form lists each store shipping method as one row
    with a campaign price, so a second row on a code cannot be added there.
  - Campaigns App releases that validate duplicates answer a shipping create on
    a code the campaign already uses with a 400:
    `{"shipping_method": ["Shipping method code '<code>' already exists on this
    campaign. Use an offer to charge a different price."]}`. The same 400 comes
    back from an update that sends `shipping_method` with a code another method
    on the campaign uses; an update that sends only prices is not checked. An
    older release may accept the duplicate create with a 201, and does not
    check the update. Unverified against a live store: the status and wording
    are as the platform documents them, so match on the 400 and the
    `shipping_method` field, not on the exact sentence.
  - A deleted method no longer counts, so its code can be used again. The new
    method gets a new id.
  - Older campaigns that still hold several methods on one code may have only
    one of them served to the storefront. A page that names one of the others
    cannot select it: the campaign SDK looks a page's shipping id up in the
    methods the campaign serves and does not apply one it cannot find, so the
    order may be charged another method's price. This is read from the SDK
    source and unverified on a live checkout. `carts/calculate`, which `verify`
    calls, takes the id directly and is not subject to that lookup.
- A different shipping price per bundle, in order of preference: free shipping
  from a quantity, then a `shipping_percentage` offer below 100 on the one
  method (see the partial shipping note below), then several campaign shipping
  methods on different store codes. When a plan uses several methods, each
  create returns its own id, and `carts/calculate` charges the price of the id the cart
  names. In the plan:
  - Each `shipping_methods[]` entry may carry a `key`. It defaults to the code and
    is the entry's identity in the manifest. Keys must be unique.
  - A `landed_prices` row may carry a `shipping_key`. `verify` prices that row's
    checkout carts with that method; a row without one uses the first method.
    Upsell rows cannot carry one.
  - `recommend --shipping <code>:<price>:<key>` sets the key. Assigning a
    `shipping_key` to a row is a plan edit.
  - The store returns no key, so resume identifies a lost entry by code and price
    among the entries on that code not already journalled, and stops unless
    exactly one has that price and every other price is readable. Teardown reads
    each entry back by its journalled id and requires the code, and the price
    whenever the store reports one in the campaign currency.
- Offer create requires `name` (unique in the campaign, at most 128), `condition`
  and `benefit`. `offer_type` is `offer` (automatic, checkout only) or `voucher`
  (customer-entered `code`, works post-purchase). `condition.type` is `any` or
  `count` (integer `value` of at least 1). Scope with `condition.package_ids`.
  `all_packages: true` also covers packages created later and is never what a
  tier offer wants; the plan never uses it. `benefit.type` is
  `package_percentage`, `shipping_percentage` or `order_percentage`; `value` is a
  decimal string; `price_rounding` is one of `null`, `"0.00"`, `"0.95"`,
  `"0.97"`, `"0.99"`. There is no free-quantity benefit, no fixed-amount
  benefit, no min-spend condition, and no separate benefit package list.
  `recommend --offer-type bxgy` therefore emits a labeled
  `package_percentage` of `100 × Y / (X + Y)` at `count = X + Y`.
  `recommend --gift` emits `package_percentage` `100.00` with `condition: any`
  scoped only to gift packages, and omits `price_rounding` so charm rounding
  cannot bring a 100% line back to 0.95.
- Offer scope read-back: the offers list omits `condition.packages`; only the
  per-offer retrieve carries the scope. `verify` retrieves each offer by id.
- Free shipping is an automatic `offer` with `benefit.type`
  `shipping_percentage` at `100.00`, scoped to the hero packages. It is operator
  input, not doctrine, so `recommend` only emits it on request:
  - `--free-shipping` gives `condition.type: any`, named `{Product} - Free
    Shipping`: every checkout order with a hero unit ships free.
  - `--free-shipping-min-qty N` gives `count` with `value: N`, named `{Product} -
    Free Shipping - Buy {N}+`, and adds a rationale line listing which tiers ship
    free. It replaces `--free-shipping`; passing both is an error. N must be at
    least 2 and no higher than the top tier, so the landed rows include a Buy N
    and a Buy N-1. On a store without the Offers API the rule goes into the
    handoff instead, with the calculate probes that prove it.
  - `verify` decides shipping per cart from the plan's offers. A cart meets an
    offer when its in-scope units reach the threshold (`any` counts as 1); a cart
    that meets none expects the price of its row's `shipping_key` method, or the
    first shipping method's price when the row names none. A free-shipping offer
    is assumed to apply whichever campaign shipping method the cart carries, so
    the carts at N and N-1 may sit on different methods: each is compared with
    its own price. Each offer gets a
    `free-shipping coverage` row that needs a checkout cart at exactly N in-scope
    units and, for N above 1, one at exactly N-1 that pays. A gap, or a second
    free-shipping offer that would free those carts anyway, fails the row,
    because calculate could not tell that threshold from its neighbour. A cart
    only counts when its own shipping price is above its rounding tolerance (0.01
    a unit), so free shipping on a 0.00 method can never pass. A row whose
    `shipping_key` method was never created fails without a calculate call.
    When an offer is scoped to a variant other than a row's first, verify adds a
    single-variant cart for it.
  - Partial shipping offers (`shipping_percentage` below 100) are not modelled.
    The plan gate labels the rows they touch `shipping partly discounted`, and
    verify expects full shipping there, so prove those by hand.
  - The masking analysis only knows the plan's offers, so verify also lists the
    campaign's live offers (`campaign offers match the plan`), after the pricing
    probes, and fails on any this run did not create. It reads the live
    `condition.type`, `condition.value` and `available` back when the offer
    retrieve body carries them, and skips the check when it does not: the public
    reference does not document those read fields, so an absent one is not a
    mismatch. If a live threshold still looks wrong after that check, prove it
    with a hand `carts/calculate` at N and N-1 units rather than assuming the
    plan's number is what the campaign stored.
  - A hand-edited condition is handled the same way. The case that forced this
    was a live campaign whose count-2 offer the engine priced correctly but
    verify failed on every 2+ row by the shipping price.
- Upsell carts in verify call `carts/calculate/?upsell=true` with no
  `shipping_method`. The Campaign Cart API documents the query as the switch that
  skips site-wide automatic offers, which do not apply post-purchase, and a
  post-purchase upsell adds lines to a placed order without a shipping choice of
  its own. The expected total is the voucher price alone.
- Timestamps: the create response and the retrieve echo the same instant in
  different timezone offsets (for example `+02:00` and `-07:00`). Identity checks
  compare instants, not strings (`same_instant()`).
- Offers "coming soon": the guide and the Admin API overview say offers are
  dashboard-only. The reference pages and the live endpoint disagree. `discover`
  probes per store; `apply` degrades to campaign, packages and shipping, and hands
  offers to the dashboard if the endpoint answers 404 or 405.
- Metadata create: `POST /api/admin/metadata/` sent with the
  `X-29next-API-Version` header returns 201, but the definition is then invisible
  in the dashboard and in the versioned GET. The client sends the version header
  on the GET and omits it on the POST.
- Rate limit: 4 requests per second, documented. The client paces at least 300 ms
  between calls and retries only GETs, honouring `Retry-After`. A POST or DELETE
  is never retried.

## In-place edits

`edit` changes objects the run manifest records and nothing else. The request
bodies, and the PATCH behaviour they are built around:

| Change | Request | Behaviour the engine relies on |
|---|---|---|
| package price | `PATCH .../packages/{id}/` `{"prices": [{"currency", "price"}, ...]}` | a package PATCH takes `prices[]` rows. The engine sends every row the package has and changes only the campaign-currency one, so other currencies keep their price |
| offer percentage, rounding | `PATCH .../offers/{id}/` `{"benefit": {"value", "price_rounding"}}` | the benefit is merged: a field that is not sent is kept. `price_rounding` is sent only when it changes, as a value or `null` |
| offer condition or scope | `PATCH .../offers/{id}/` `{"condition": {"type", "value", "all_packages": false, "package_ids"}}` | the condition is replaced whole: an `any` condition sent without `value` reads back with `value: null`. The engine therefore always sends all of it |
| pause or resume | `PATCH .../offers/{id}/` `{"available": false}` | a plain field |
| add an offer | `POST .../offers/` then, to pause, `PATCH {"available": false}` | a new offer is live on create whatever the create body says about `available` |

All changes to one offer go in one PATCH. A combined body (benefit, condition
and `available` together) has not been observed on a live store; the read-back
after the write is what catches a field that did not land.

Not edited: `PATCH` on the campaign (`currency`, `language` and
`payment_gateway_group_id` are fixed at create), `PATCH` on a shipping method (a
bare `price` there is answered 200 and ignored, and the method's price is part of
how teardown identifies it), an offer's `offer_type` or `code` (changing either
makes a different offer), `all_packages`, and any `DELETE`.

The checks around each write:

- **Before-image.** Each object is read by its journalled id and reduced to the
  fields an edit can change or relies on (a package: id, name, variant id and
  `prices[]`; an offer: id, name, type, code, `available`, the condition and the
  benefit). That reduced read is saved in the receipt and hashed.
- **The store must agree with the plan.** A live value that differs from the plan
  is refused: the plan's landed prices would be wrong either way. That covers the
  offer's type, benefit type and voucher code as well as the fields being edited;
  a read that omits one of those three is inconclusive, since no edit writes them.
- **One edit at a time.** `edit` holds an `edit.lock` file in the run directory
  while it writes and checks the manifest is unchanged since its preview.
- **The read must carry what the write depends on.** A condition write replaces
  the whole condition, so the read has to show `type`, `value` and the packages.
  A pause needs `available`. When the store's read omits one, the edit is refused
  as unprovable.
- **Re-read before, read back after.** There is no conditional write (no
  `If-Match`), so the engine re-reads each object immediately before its PATCH
  and stops if it is no longer the saved before-image, then reads it after and
  compares it with the intended state. A 2xx is not the test. A mismatch stops
  the run with both bodies printed. No write is retried.
- **Approval hash.** SHA-256 over the plan file's hash, the edit file and each
  object's before-image hash.

Landed prices after an edit are recomputed from the plan's packages and offers,
not carried over. For a checkout row, the live automatic `package_percentage`
offers whose scope covers the row's packages and whose condition the quantity
meets are compared and the highest percentage wins; `price_rounding` is applied
to the winner, never used to choose it. No such offer means the package price,
with `offer_key` null. An upsell row is priced by its own voucher. The engine
refuses shapes those rows cannot express: a row whose packages no longer share a
price, a scope covering part of a row, and two eligible offers at the same
percentage.

The residual in "Reconcile residual" applies to an added offer too: a lost POST
response is claimed by offer name. It is narrowed by the receipt, which lists
every offer id that was live before the edit; none of those is ever a
candidate, so an offer that already existed cannot be claimed, paused or later
deleted by this run.

## Reconcile residual

The API exposes no writable per-campaign marker and no idempotency key, so a
resumed run claims an interrupted campaign by exact name within the run's own time
window. The residual risk is a second operator creating a campaign with the same
name on the same store inside that window. A lost create response is recoverable,
because `GET /campaigns/{id}/` returns `api_key` again.

Shipping methods carry the same kind of residual one level down. A lost shipping
create is claimed by code and price among the entries this run has not
journalled. If a second operator adds a method with that code and price to this
new campaign between the lost response and the resume, the resume claims it, and
teardown later deletes it, as this run's.

## Recommendation rules

| Rule | Source |
|---|---|
| Low CTC: one package per variant + Buy 1/2/3 tier offers at 50/55/60 | [Cost-to-consumer decides the structure](offer-doctrine.md#cost-to-consumer-decides-the-structure), [Standard tiers](offer-doctrine.md#standard-tiers) |
| `--offer-type bxgy`: one count-(X+Y) offer at 100×Y/(X+Y)% off every hero unit | [Buy-X-get-Y approximation](offer-doctrine.md#buy-x-get-y-approximation), [worked-examples.md](worked-examples.md) |
| `--gift` / `--offer-type gwp`: gift package + 100% offer scoped only to it | [Gift with purchase](offer-doctrine.md#gift-with-purchase) |
| High CTC: single unit, no tiers, bumps + upsell vouchers | [Cost-to-consumer decides the structure](offer-doctrine.md#cost-to-consumer-decides-the-structure) |
| Every campaign gets an exit-pop voucher (extra 5 to 10%) | [Exit-pop voucher](offer-doctrine.md#exit-pop-voucher) |
| Tier offers are `offer` type, scoped to hero package ids, never `all_packages` | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Upsell/exit offers are `voucher` type (site offers don't fire post-purchase) | [Offer types and where they work](offer-doctrine.md#offer-types-and-where-they-work) |
| One upsell voucher per product and percentage, scoped to every variant package; key `upsell-{product_id}-{pct}` | [Post-purchase upsells](offer-doctrine.md#post-purchase-upsells) |
| One package per variant, one shipping method per store code; an upsell reuses its variant's package and the voucher sets its price; a bump must be an unpackaged variant | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Voucher code `{SHORT NAME}{PCT}`: distinctive words, at most 12 characters, discount rounded down; a clash stops for `--short-name` | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Package name `{Product}` or `{Product} - {Variant}`; never `2x Product` | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Campaign name = hero product; gateway group must carry the currency | [Campaign settings](offer-doctrine.md#campaign-settings) |
| Landed price is a forecast until confirmed against `carts/calculate` | [Rounding and stacking](offer-doctrine.md#rounding-and-stacking) |

CTC and the anchor price are operator inputs. The tool never classifies CTC from a
price or reads a package price from the catalogue for bumps or upsells.

## Output files

Files live in a run directory:

- `discover` defaults to `./next-campaigns-create-runs/<slug>/` under the current
  working directory.
- `recommend` writes next to the discovery file it reads.
- `apply` and `verify` write next to the plan file.
- `--out` overrides the directory for any of them.

One directory holds one campaign run. `recommend` refuses a directory that already
holds a `run-manifest.json`. A resume refuses a destination holding a different
run, and refuses a run that teardown has touched. When the directory is inside a
git repository it must be gitignored; the engine checks and refuses to write
otherwise.

- `discovery.json`: the store snapshot plus `offers_supported`,
  `metadata_checked`, `metadata_missing`, `metadata_conflicts` and
  `metadata_error`. The last is null when the audit ran, and when it failed it is
  `{"status": <int or null>, "reason": "<fixed reason>"}`, which never contains a
  response body.
- `campaign-plan.json`: see the schema below. Its SHA-256 is the approval token,
  passed to `apply` as `--plan-sha256 <plan-sha256>`.
- `run-manifest.json`: the journal of created ids and the campaign `api_key`.
  Mode 600 where the OS supports it, written atomically. The only file holding a
  live secret.
- `verify-report.json`: the admin read-back checks and the `calculate` cases.
  Each case records `shipping` (`paid`, `free` or `none`), `expected_shipping`,
  `shipping_key` (the method the cart carried, null for an upsell) and the
  request `path`. `sections` breaks the result down as `owned_fields`,
  `calculate` and `live_offer_set`; `calculate` reads `NOT ATTRIBUTABLE` when an
  offer this run does not own is live, listed in `unowned_live_offers`. The
  overall `result` is unchanged by the breakdown.
- `campaign-edit.json`: `{"operations": [...], "note": "..."}`, the input to
  `edit`. Operations are `set_package_price`, `set_offer_benefit`,
  `set_offer_condition`, `set_offer_scope`, `set_offer_available` and
  `add_offer`.
- `edit-<n>-receipt.json`: written before an edit's first request. Holds the edit
  file, the approval hash, the `live_traffic_acknowledged` answer, the plan text
  before and the plan after with both hashes, `preexisting_offer_ids`, and per
  object the `before` image, its hash, the `expected` read after the write, the
  `patch` body and the `restore` body. No secret. Mode 600 like the other run
  files.
- `edit-<n>-rollback.json`: written when an unfinished edit is rolled back. Holds
  the actions taken and the plan the rollback left, with its hash.

### campaign-plan.json

```json
{"store_slug": "...", "store_origin": "https://<slug>.29next.store",
 "generated_at": "...", "ctc": "low|high", "offer_kind": "quantity|bxgy|gwp",
 "campaign": {"name","currency","language","payment_gateway_group_id",
   "additional_currencies","available_payment_methods",
   "available_express_payment_methods","available_shipping_countries",
   "statement_descriptor"},
 "packages": [{"key","role":"hero|bump|upsell|gift","name","variant_title",
   "product_id","product_variant_ids","price",
   "image":{"src","file_name"}}],
 "shipping_methods": [{"key","shipping_method","price"}],
 "offers": [{"key","name","offer_type","code","available",
   "condition":{"type","value","package_keys"},
   "benefit":{"type","value","price_rounding"}}],
 "landed_prices": [{"tier","kind":"tier|single|upsell","qty","offer_key","package_keys",
   "shipping_key","anchor","pct","unit_after","order_total",
   "paid_qty","free_qty","total_qty","full_retail","payable","savings",
   "effective_pct","effective_unit","approximation","note"}],
 "voucher_codes": [{"offer_key","product_id","title","short_name",
   "generated_code","source":"generated|short-name|exit-code"}],
 "rationale": ["..."], "blockers": ["..."], "waivers": ["..."], "handoff": ["..."]}
```

`voucher_codes` is written by `recommend` and read only by `plan`'s preview; it
is never sent to the API and is optional for `validate_plan`. The code a voucher
is created with is always `offers[].code`. When that differs from
`generated_code` (a hand edit), the preview marks it `edited in plan`; a plan
without `voucher_codes` shows each code with source `unknown`. `short_name` is
null for an `--exit-code` exit voucher unless the hero has a `--short-name`.

Several `landed_prices` rows may share one `offer_key`: a grouped upsell voucher
has one `upsell` row per variant package, so `verify` probes each variant with
the code. An upsell row's `package_keys` may name a hero or bump package when
the upsell reused it.

`available` on an offer is optional. `edit` writes `false` when it pauses an offer
and removes the field when it resumes one; a hand-written `true` is accepted and
means the same as leaving it out. A paused offer is left out
of the landed-price and free-shipping calculations. A fresh `apply` refuses a
plan with a paused offer, because every offer is created live; `--resume` accepts
one whose manifest entry is already `created`. After an edit the plan file is
rewritten, so its hash changes and the manifest's `plan_sha256` follows it.

`offer_kind` is written by `recommend` (`quantity` when `--offer-type` is omitted)
and is optional for `validate_plan`. Package `role` is not schema-checked;
`recommend` emits `hero`, `bump`, `upsell` and `gift`. The extra landed fields
(`paid_qty` through `note`) appear only on buy-X-get-Y deal and over-qty rows;
the BXGY Buy 1 list-price row, quantity rows and gift rows keep the original
columns. Gift carts stay `kind: single` so verify's existing three kinds still
cover them. When a gift package is present, verify also probes a synthetic
hero+gift cart so the two `package_percentage` offers are proven together.
`pct` on quantity and list-price rows is an integer; on BXGY deal rows it is an
integer when the rate is whole and a decimal string (for example `"33.33"`) when
fractional. `print_plan` formats either.

### run-manifest.json

```json
{"store_slug","store_origin","plan_sha256","run_id","started_at",
 "campaign": {"status":"pending|created|deleting|deleted","id","api_key","name","created_at"},
 "packages": [{"key","status","id","name","product_variant_id",
   "image_status":"pending|set|unsupported|failed","image","image_at_create","image_error"}],
 "shipping_methods": [{"key","status","id"}],
 "offers": [{"key","status","id","name","pause_pending"}],
 "edits": [{"edit","kind":"edit|undo|rolled_back","receipt","edit_sha256",
   "old_plan_sha256","new_plan_sha256","applied_at"}],
 "pending_edit": {"edit","kind","receipt","old_plan_sha256","new_plan_sha256",
   "objects": {"<section>:<key>": "pending|sending|verified"},
   "adds": {"<offer key>": "pending|verified"},
   "rollback": {"approved_sha256","objects","adds","plan_sha256"}}}
```

`edits` and `pending_edit` exist only on a run that has been edited.
`pending_edit` is present while an edit is unfinished and is removed when it
completes or is rolled back. While it is present, `verify` and `--resume` refuse,
`edit` continues or rolls back, and `teardown` accepts any of the three plan
hashes it names (the plan the edit started from, the one it writes, and the one a
rollback writes) and takes the identity of an offer the edit created from the
receipt.

`package_keys` in the plan resolve to created ids at apply time. Each
`landed_prices` row carries its own `package_keys`, `offer_key` and optional
`shipping_key`, so `verify` builds its cart cases by lookup rather than parsing
the display label. Manifest shipping entries are keyed by the shipping key, which
is the store code for an entry without one, so manifests written before 0.4.0
still resume, verify and tear down. Resume matches `pending` entries by identity
(package: variant id + name; shipping: code + price; offer: name) before creating
anything, so a lost response never duplicates.

## Clone contract and journal

`POST /api/admin/campaigns/{source_id}/clone/` takes no request body and needs
`campaigns:write`. A body cannot set a name. The 201 response is a campaign
detail object, including a new `id` and `api_key`, with no nested resources.
The server names it `{source name}-COPY`. The optional rename uses
`PATCH /api/admin/campaigns/{new_id}/` with only `{"name": "<requested name>"}`.
This is the only campaign PATCH the clone command sends; currency is immutable.

Packages and shipping options get new database primary keys but preserve their
reference IDs, the IDs exposed in Admin API paths and used by funnel pages.
Offers preserve their reference IDs and discount codes. Only nonremoved offers
copy, including unavailable ones. Condition package references map to the new
packages; `all_packages` is preserved. Campaign settings copy, including PayPal
account, gateway group and payment methods. The new campaign key is distinct.
Campaign-currency prices copy exactly; missing supported additional-currency
prices can be derived by forex. Usage counters reset to zero and
`enable_retail_price_and_quantity` becomes false. These resets are documented
server behavior unless the response actually exposes them.

The server clones in one transaction, but supplies no idempotency key or revision
precondition. A lost response may mean the complete copy exists. Store deployment
determines route availability: 404/405 after a successful source read means
check deployment. Do not infer store availability from source-control status.
401/403 uses the normal token/scope guidance. No campaign response body is
printed in clone errors.

Clone decoding uses `Decimal` for JSON numbers. Canonical snapshots store prices
as decimal strings without rounding, sort resources by reference ID and normalize
set-valued settings. Each offer detail is read because lists omit package scope.
Snapshots explicitly select copied fields; they exclude keys, timestamps and
usage counters. Source creation identity is retained separately in UTC.

A clone manifest has `kind: "clone"`, `plan_sha256: null`, source identity fields,
`clone_sha256` and the secret-free `clone_approval` document. That document binds
the store, source settings and resource rows, requested name, server name and
request sequence. Creation manifests and `campaign-plan.json` remain unchanged.
Clone verification and teardown reject a supplied plan; `null` is never a wildcard
for a creation plan hash. Clone `edit` refuses before any lock or receipt write.

`clone_request.status` is `not_sent`, `sending`, `uncertain`, `rejected` or
`confirmed`. Before POST, the journal saves existing same-name candidate IDs,
`attempted_at` and `attempt_deadline` (attempt time plus request timeout plus 60
seconds). Recovery never widens this window: candidate creation must fall between
attempt time minus 60 seconds and the saved deadline. Missing window evidence,
zero candidates or multiple candidates stops for manual resolution. One candidate
must match identity and approved content before adoption. This is still not
server-backed proof: another operator can create an identical copy in that window.

A valid 201 immediately journals the destination ID, name, creation instant and
key. Only the protected mode-600 manifest holds that key; the source key is never
persisted. `rename` records `name` and `status: pending|done|skipped`. Resume first
reads the destination, so a rename already applied needs no second PATCH.

`inventory_complete` becomes true once destination enumeration succeeds. The
usual resource arrays hold destination read-back fields, deterministic keys and
`origin: "cloned"`. `parity: {status: match|mismatch|unchecked, details: [...]}`
records preservation differences; a mismatch does not remove ownership or block
completion. `completed_at` records completed enumeration. Teardown uses these
owned entries, not the expected source rows. With incomplete inventory, teardown
refreshes only after checking the journaled destination ID and exact creation
instant, with its server name or approved rename. Source/destination equality
always refuses mutation.

Clone execution, resume and teardown share `edit.lock`, created exclusively with
mode 600 beside the manifest. Resume and teardown load the manifest under that
lock. A new clone's preview and gate run before any directory or lock is created.
The existing run-directory gitignore and symlink guards apply.

Clone reports compare the destination with the saved approval, list current
source drift separately, and identify supported currencies missing from source
price rows as forex-derived. Cart probes are skipped with a reason because no
plan-derived landed prices were approved. Reports contain no campaign key.
