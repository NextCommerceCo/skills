# next-campaigns-create: Admin API contract and file schemas

Companion to `SKILL.md`. Field-level detail for `scripts/campaign_admin.py`. The
sources are the public developer docs: the provisioning flow at
<https://developers.nextcommerce.com/docs/campaigns/admin-api>, the per-endpoint
reference pages under
<https://developers.nextcommerce.com/docs/admin-api/reference/campaigns/>, and the
token permissions at
<https://developers.nextcommerce.com/docs/admin-api/permissions>. The reasoning
behind each recommendation is in [offer-doctrine.md](offer-doctrine.md), and the
procedure for changing a campaign that already exists is in
[update-path.md](update-path.md).

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
| `catalogue:read` | `discover` (`GET /products/`, the variant ids packages point at) |
| `gateways:read` | `discover` (`GET /gateway-groups/`) |
| `campaigns:read` | every GET under `/campaigns/`: `discover`, `plan --check-store`, `apply`, `verify`, `teardown`, `edit` (including `--undo` and finishing or rolling back an interrupted edit), `adopt`, `diff`, `update` |
| `campaigns:write` | every write under `/campaigns/`: `apply`, `edit` (including `--undo` and a rollback), `teardown`, `update` |
| `metadata:read` | the metadata audit in `discover` |
| `metadata:write` | `metadata --apply` |

`GET /shipping-methods/` carries no scope in the published spec, so any valid
token may call it. Source for every scope above: the 2024-04-01 OpenAPI file at
<https://developers.nextcommerce.com/api/admin/2024-04-01.yaml>. A 401 or 403
from the engine names the scope the failing request needed (`scope_for()` and
`auth_hint()` in the engine).

Every command that changes a campaign which already exists is narrower than the
create path, because every request it sends is on `/api/admin/campaigns/`:

| Command | Requests it sends | Permissions it needs |
|---|---|---|
| `edit` | GET the campaign, GET the offers list, GET each package or offer it will write, then PATCH packages, PATCH offers and POST an added offer | `campaigns:read`, `campaigns:write` |
| `edit --undo` | the same reads and the same two write methods: the inverse edit restores values and pauses an offer the edit added, and never deletes one | `campaigns:read`, `campaigns:write` |
| `adopt` | GET the campaign, its packages, its shipping methods, each offer by id | `campaigns:read` |
| `diff` | the same GETs, then writes files locally | `campaigns:read` |
| `update` | those GETs, then PATCH, POST, PUT and DELETE on the same prefix | `campaigns:read`, `campaigns:write` |

Finishing or rolling back an interrupted `edit` sends the same methods on the
same prefix and needs the same two permissions. None of these reads the store
settings, the catalogue, the gateway groups or the metadata definitions, so none
of them needs `store:read`, `catalogue:read`, `gateways:read` or either metadata
scope. This narrows nothing about the create path: `discover` still needs all
seven, and that is the key an operator makes. The same key works for `edit`,
`adopt`, `diff` and `update` with nothing added, so changing a campaign, or
taking one over, never means a second key.

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
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/` | retrieve (returns `api_key`) / update the campaign's settings (`update`), or rename a clone destination (`clone --name`) / delete |
| POST | `/api/admin/campaigns/{id}/clone/` | bodyless clone request, availability depends on store deployment |
| GET/POST | `/api/admin/campaigns/{id}/packages/` | list / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/packages/{packageId}/` | retrieve one (teardown's identity read-back, an edit's before-image, and a resumed update's full read-back) / update name, the recurring fields, and `prices[]` per currency (currencies the body omits are left unchanged) / delete |
| PUT | `/api/admin/campaigns/{id}/packages/{packageId}/image/` | replace the package image (optional override) |
| GET/POST | `/api/admin/campaigns/{id}/shipping-methods/` | list / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/shipping-methods/{id}/` | retrieve one (same two readers) / update `prices[]` per currency, same rule as a package / delete |
| GET/POST | `/api/admin/campaigns/{id}/offers/` | list / create |
| GET/PATCH/DELETE | `/api/admin/campaigns/{id}/offers/{offerId}/` | retrieve (the only read that carries the scope) / update name, type, code, `available`, `condition`, `benefit` / delete |
| POST | `campaigns.apps.29next.com/api/v1/carts/calculate/` | pricing truth (verify); `?upsell=true` for upsell carts |

The PATCH routes are the update path's. `adopt` and `diff` send no writes at
all: they use the campaign GET, the three collection GETs and the per-offer
retrieve.

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
  tier offer wants; the plan cannot express it, so it is never sent, and `adopt`
  blocks on a live offer that carries it unless the operator approves pinning it
  with `--convert-scope <offer_id>`. `benefit.type` is
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
  per-offer retrieve carries the scope. `verify`, `adopt` and `diff` all
  retrieve each offer by id for that reason.
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
    probes, and fails on any this run did not create. Since 1.0.0 it also reads
    `condition.type`, `condition.value`, `available`, `benefit.type`,
    `benefit.price_rounding` and the offer name back. `condition.value` was
    confirmed readable on 2026-10-08 (see "Read-only checks" below) and comes
    back as a decimal string, so it is compared as a number; a threshold a store
    does not report is an `UNVERIFIED` row rather than a pass. The offer read
    returned `available` in the read-only checks below. Should a store omit it,
    `verify` reads the offer as live, the same default a plan without the field
    has. If a live threshold still looks wrong after those
    checks, prove it with a hand `carts/calculate` at N and N-1 units rather
    than assuming the plan's number is what the campaign stored.
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

## Update semantics

What the update path (`adopt`, `diff`, `update`) relies on, field by field.

- **A PATCH carries only what changed.** Each body is built by comparing the
  desired value with the live one, in plan space, and sending the difference.
  Nothing else is sent, so a field nobody edited cannot be clobbered by a
  round-trip.
- **Currency is never in a PATCH body.** It is immutable, so `diff` refuses a
  currency edit instead, and refuses a manifest whose base plan currency does
  not match the live campaign.
- **Campaign clearing values are explicit.** `[]` empties
  `available_payment_methods`, `available_express_payment_methods` and
  `available_shipping_countries`; `null` clears `additional_currencies`,
  `statement_descriptor` and `paypal_account_id`. Read-back uses different
  empties for the same absence (`""` for the descriptor, `null` for the PayPal
  id, `[]` or `null` for the extra currencies), so the normaliser collapses
  `""` and `null` to one value before anything is compared.
- **A package PATCH sends `prices[]`, and prices merge per currency.**
  `prices: [{currency, price, price_recurring}]`; the create-only `price` field
  is not an update field. The published spec says of `prices` on both the
  package and the shipping-method PATCH: "Per-currency prices to update ...
  Currencies not included are left unchanged", with a separate
  `recalculate_prices` flag described as "Whether to recalculate prices for
  currencies not included in `prices`, via forex conversion". So a PATCH does
  **not** replace the price list. A changed `name`, `interval` or
  `interval_count` goes in the same body.
- **Only the campaign currency is priced, and the others are left as they are.**
  A plan carries one price per package and per shipping method, so a price op
  names the campaign currency only. On a campaign with `additional_currencies`
  that leaves the other currencies at the prices they already had: a campaign
  create fills them by forex, an update does not. The engine never sends
  `recalculate_prices`, because it would convert every other currency from the
  one being sent and overwrite a price someone set by hand. `diff` puts a line
  in `warnings[]` for every price op on such a campaign, naming the op and the
  currencies, and the operator sets those prices in the dashboard.
- **A shipping PATCH sends prices only.** The store code is that method's
  identity. Sending `shipping_method` with a code another method on the campaign
  uses answers 400 on releases that validate duplicates, so `diff` refuses a
  code change on an existing shipping key and tells the operator to add a method
  on the new code and delete the old one. Its `prices` merge per currency the way
  a package's do.
- **An offer PATCH replaces `condition` wholly and merges `benefit`.** A
  condition is sent complete, scope included, because a partial one would drop
  the fields it omits: an `any` condition sent without `value` reads back with
  `value: null`. A benefit merges field by field, so `edit` sends only what
  changes there and `update` sends the whole benefit, which comes to the same
  thing. `price_rounding` is sent as a value or an explicit `null`, never left
  out: a benefit body that omits it keeps whatever rounding the store has, so
  omitting it on a candidate that cleared the rounding would promote a plan
  saying the rounding was gone while every discounted price stayed rounded.
- **Offer names and voucher codes are unique per campaign, at every step.** A
  final state that is valid is not enough: `diff` walks the ops in the order
  `update` sends them against the names and codes still live at that point and
  refuses an intermediate collision. Swapping two offer names is the standard
  case, and it needs 2 updates through a temporary name. The walk follows what
  an op gives up as well as what it takes: an earlier op that renames an offer
  or drops its voucher code (by making it an automatic offer) frees that name or
  code for a later op in the same change set. Reverse the order and the same
  pair refuses, because at that step the first offer still holds it.
- **`available` retires an offer without deleting it.** It is a boolean,
  defaults to true, is modelled in the plan, and is sent on create and update.
  An offer set to false keeps its id and stops firing. Per-customer limits and
  date ranges are not fields at all.
- **A package an offer still scopes cannot be deleted.** The API answers 400
  rather than narrowing the offer, so `diff` refuses that delete before anything
  is sent and names the offers holding the package.
- **Ids are never reused.** A deleted package, shipping method or offer cannot
  come back under its old id, so a delete plus a create is a repoint for the
  funnel. A PATCH never changes an id, and the campaign `api_key` does not
  change on update.
- **Ownership is checked on immutable fields only.** The pre-flight read-back
  compares the campaign id and created_at, each package's id and
  `product_variant_id`, each shipping method's id and store code, and each
  offer's id. Names, prices and codes are mutable, so drift in them is the
  three-way merge's business, not an ownership failure.

## In-place edits

`edit` changes objects the run manifest records and nothing else. The request
bodies, and the PATCH behaviour they are built around:

| Change | Request | Behaviour the engine relies on |
|---|---|---|
| package price | `PATCH .../packages/{id}/` `{"prices": [{"currency", "price"}, ...]}` | a package PATCH takes `prices[]` rows. The engine sends every row the package has and changes only the campaign-currency one, so other currencies keep their price |
| offer percentage, rounding | `PATCH .../offers/{id}/` `{"benefit": {"value", "price_rounding"}}` | the benefit is merged: a field that is not sent is kept. `price_rounding` is sent only when it changes, as a value or `null` |
| offer condition or scope | `PATCH .../offers/{id}/` `{"condition": {"type", "value", "all_packages": false, "package_ids"}}` | the condition is replaced whole: an `any` condition sent without `value` reads back with `value: null`. The engine therefore always sends all of it |
| pause or resume | `PATCH .../offers/{id}/` `{"available": false}` | a plain field |
| add an offer | `POST .../offers/` then, to pause, `PATCH {"available": false}` | the create body never carries `available`, so whether a store honours it there does not matter here: the offer is created live and paused by its own PATCH, which is read back |

All changes to one offer go in one PATCH. A combined body (benefit, condition
and `available` together) has not been observed on a live store; the read-back
after the write is what catches a field that did not land.

Not edited in place: `PATCH` on the campaign, `PATCH` on a shipping method, an
offer's `offer_type` or `code` (changing either makes a different offer),
`all_packages`, and any `DELETE`. Those belong to the update path, which sends
them from a reviewed change set; `currency` belongs to neither, being fixed at
create. A bare `price` on a shipping method is answered 200 and ignored either
way, which is why both paths send `prices[]`.

`edit` refuses a manifest whose `origin` is `adopted`. It proves a change by
recomputing the plan's landed prices, and an adopted plan has none: that campaign
is changed through `adopt`, `diff` and `update`.

The checks around each write:

- **Before-image.** Each object is read by its journalled id and reduced to the
  fields an edit can change or relies on (a package: id, name, variant id and
  `prices[]`; an offer: id, name, type, code, `available`, the condition and the
  benefit). That reduced read is saved in the receipt and hashed.
- **The store must agree with the plan.** A live value that differs from the plan
  is refused: the plan's landed prices would be wrong either way. That covers the
  offer's type, benefit type and voucher code as well as the fields being edited;
  a read that omits one of those three is inconclusive, since no edit writes them.
- **One command at a time.** `edit` holds the run directory's `.run.lock` for
  the whole command, the same lock `apply`, `verify`, `teardown`, `diff` and
  `update` take, and checks the manifest is unchanged since its preview. It also
  opens the run the way they do, so a plan promotion an update left half-done is
  finished before the plan is read, and an `active_update` on the manifest
  refuses the edit.
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
- **The plan and the manifest move together.** A finished edit rewrites
  `campaign-plan.json` and then sets the manifest's `plan_sha256` to the new
  hash, so a later `diff` reads the edited plan as its baseline and `verify`,
  `apply --resume` and `teardown` keep working. While the edit is journalled and
  unfinished, `teardown` accepts any of the three hashes it may have left on
  disk, and `diff`, `update` and `verify` refuse until it is finished or undone.

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

## Read-only checks, 2026-10-08

A point-in-time observation, not a verification of the update path. This section
is the record: SKILL.md and `update-path.md` state the bare fact in a line and
point here, and everything about what it does and does not settle lives only
here. A dated read is a record, never an assurance about a write, so neither of
those files may lean on it as one.

On 2026-10-08 the response shapes below were read off 2 campaigns on live
stores. Every request was a GET; nothing was written, so nothing here says that
an update works, that a PATCH body is accepted, or that any write behaves as
assumed. What it settles is narrower: these fields were present, in these
shapes, on that date, on those 2 campaigns. A different store, a different
platform release or a later date can read back differently, and the open points
in "Not yet verified against a live store" below are all still open.

No store, campaign or key is named, because this repository is public. That also
means you cannot check the record against the stores it was read from. You do
not have to: the reads are `discover`, `adopt` and the GETs `diff` sends, so
anyone with a `campaigns:read` key can repeat every one of them against a
campaign on their own store and see the same fields or find that they differ.

| What was read | What came back on 2026-10-08 | Effect on the design |
|---|---|---|
| offer retrieve returns `condition.value` | yes: `"2.00"` for a `count` condition, `null` for `any`. The list carries it too, without `packages` | thresholds are readable, so `adopt` never has to ask for one. A `count` offer whose value is missing or not a whole number is a plain blocker |
| offer retrieve fields | `id, name, offer_type, code, available, condition{type, value, all_packages, packages[{id, ...}], description}, benefit{type, value, price_rounding, description}` | the `description` fields are ignored by the normaliser |
| package GET | carries `product_id`, `product_variant_id`, `product_variant_name`, `is_recurring`, `interval` (`""` when not recurring), `interval_count` (`null`), `prices[{currency, price, price_recurring}]` | `adopt` needs no catalogue read |
| campaign GET empty values | `statement_descriptor: ""`, `paypal_account_id: null`, `additional_currencies: []`, `payment_gateway_group_id` an int | the normaliser maps `""` and `null` to one value |
| list envelopes | packages, shipping methods and offers arrive as `{results: [...]}` | the client already reads both shapes |

## Not yet verified against a live store

The reads above were checked live on one date. **No write on the update path or
the edit path has been checked at all.** The points below are implemented from
the published contract and proven against the offline fake only, and each needs a
write-capable store to settle.

| Open point | The engine's current assumption |
|---|---|
| the PATCH body shapes | campaign: changed fields plus the explicit clearing values above. Package: `name`, `prices: [{currency, price, price_recurring}]`, `interval`, `interval_count`. Shipping: `prices` only. Offer: changed `name`, `offer_type`, `code`, `available`, the whole `condition` when it changes, and the changed benefit fields |
| that `prices` merges per currency | the spec's wording ("currencies not included are left unchanged") is what the engine and the fake both implement, so a price op is assumed to leave the additional currencies alone rather than delete them. A store that replaced the whole list instead would drop them, which is why `diff` warns on every price op on a multi-currency campaign |
| whether a package PATCH re-appends `" - {variant}"` to `name` | the fake re-appends it, as create does. The normaliser strips the suffix before comparing: the live name equals the plan's, the plan's `name - variant_title`, the live `product_variant_name` suffix, and only last the name this run journalled. A diff is 0 ops whichever the store does. The journalled name comes last because a rename that landed refreshes it to the new live name while the plan still holds the old one, and matching on it first made a resume read its own applied rename back as drift |
| the accepted clearing values | `[]` for the three campaign code lists, `null` for `additional_currencies`, `statement_descriptor` and `paypal_account_id`. A store that rejects one of them fails that op with the store's own message, and `update --settle` closes the run |
| whether an offer create honours `available: false` | the create path refuses a plan that pauses an offer it would have to create, and `edit` creates an added offer live and pauses it with a separate PATCH that is read back. Only `update` can POST an offer the plan marks paused, and `verify`'s `available` row is what would catch a store that ignored it |
| a combined offer PATCH | benefit, condition and `available` in one body has not been observed on a live store. The read-back after the write is what catches a field that did not land |
| whether `price_rounding: null` clears rounding | the fake clears it, and both write paths send the null rather than omit the key: `edit` when the field changes, `update` on every benefit PATCH. A store that ignored the null would fail `edit`'s read-back and stop that edit; on the update path it fails `verify`'s `benefit.price_rounding` row and the next `diff` says it kept the store's rounding, so it surfaces rather than passing silently |
| a package DELETE while an offer still references it | a 400, so `diff` refuses the delete before sending it and names the offers to deal with first. If the platform instead cascades, the refusal is merely conservative: the operator removes or narrows those offers in their own update |

Until a live write check passes, treat an update or an edit as reviewed and
gated rather than proven, and read the store back with `verify` after every one.
An edit also reads every object it wrote back as it goes, and stops on the first
field that did not land.

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
- `adopt` defaults to `./next-campaigns-create-runs/<slug>-<campaign_id>/`.
- `recommend` writes next to the discovery file it reads.
- `apply` and `verify` write next to the plan file.
- `diff` writes into the run directory of the manifest it is given.
- `--out` overrides the directory for any of them.

One directory holds one campaign run. `recommend` and `adopt` both refuse a
directory that already holds a `run-manifest.json`. A resume refuses a
destination holding a different run, and refuses a run that teardown has
touched. When the directory is inside a git repository it must be gitignored;
the engine checks and refuses to write otherwise. Every command that can write
the directory holds a `.run.lock` in it for its whole life (the pid and start
time of the holder, created with `O_CREAT | O_EXCL`, removed on exit and only by
hand after a kill).

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
  request `path`. A check's `result` is `PASS`, `FAIL`, `INFO` or `UNVERIFIED`,
  and the report's `result` is FAIL only when some row is FAIL. `sections` breaks
  it down as `owned_fields`, `calculate` and `live_offer_set`; `calculate` reads
  `NOT ATTRIBUTABLE` when an offer this run does not own is live, listed in
  `unowned_live_offers`. The overall `result` is unchanged by the breakdown.
- `campaign-plan.next.json`: the operator's edited copy, by convention. Input to
  `diff` only. Nothing writes it and nothing promotes it.
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
- `change-set.json`: the reviewed change set, written by `diff`. Its own SHA-256
  is the approval token, passed to `update` as
  `--change-set-sha256 <change-set-sha256>`. Schema below.
- `campaign-plan.<sha8>.json`: byte copies of plans, never re-serialised. `diff`
  writes the merged plan under the first 8 characters of its SHA-256, and each
  promotion archives the plan it replaces the same way. `update --plan` takes
  the merged one.
- `.run.lock`: `{"pid": <int>, "started_at": "..."}` while a command holds the
  run directory. Every command that can write it takes the lock, `edit`
  included.

### campaign-plan.json

```json
{"store_slug": "...", "store_origin": "https://<slug>.29next.store",
 "generated_at": "...", "ctc": "low|high", "offer_kind": "quantity|bxgy|gwp",
 "origin": "created|adopted", "adopted_from_campaign_id": 0,
 "campaign": {"name","currency","language","payment_gateway_group_id",
   "additional_currencies","available_payment_methods",
   "available_express_payment_methods","available_shipping_countries",
   "statement_descriptor","paypal_account_id"},
 "packages": [{"key","role":"hero|bump|upsell|gift","name","variant_title",
   "product_id","product_variant_ids","price",
   "price_recurring","interval","interval_count",
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

Both lists are advice, not desired state, which is what makes an update able to
prune them: when a change deletes the offer or package a row names, `diff` drops
that row from the merged plan with a line in `warnings[]`, and `update --settle`
does the same for a DELETE that landed inside an update that could not finish.
Rows that still resolve are untouched, and a row naming a key that never existed
stays a `validate_plan` error, because that is a typo rather than a deletion.

`available` on an offer is optional. `edit` writes `false` when it pauses an
offer and removes the field when it resumes one; `adopt` writes it for a live
offer the dashboard switched off; a hand-written `true` is accepted and means the
same as leaving it out. A paused offer is left out of the landed-price and
free-shipping calculations and still owned, so `verify` reads it back. A fresh
`apply` refuses a plan with a paused offer, because every offer it creates is
live; `--resume` accepts one whose manifest entry is already `created`. After an
edit, and after a completed update, the plan file is rewritten, so its hash
changes and the manifest's `plan_sha256` follows it.

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

`origin` says where the desired state came from: `created` by a run of this
skill, `adopted` from a campaign that already existed. Only `adopt` writes it,
so a plan from `recommend` carries none, and a missing value reads as `created`.
`adopt` also records `adopted_from_campaign_id`, writes `landed_prices: []`
(nothing on the store says what anchor the discounts came off), and writes
neither `ctc` nor `offer_kind` nor an `image` field. `paypal_account_id` is
written by `adopt` and can be set by hand; `recommend` does not emit it.
`offers[].available` is a boolean that defaults to true and is sent on create and
update; `false` retires the offer without deleting it. The recurring trio on a
package is set or absent as one thing.

### run-manifest.json

```json
{"store_slug","store_origin","origin":"created|adopted","plan_sha256","run_id","started_at",
 "adopted_at","adopted_from_campaign_id","completed_at","promoted_at",
 "campaign": {"status":"pending|created|deleting|deleted","id","api_key","name","created_at"},
 "packages": [{"key","status","id","name","product_variant_id",
   "image_status":"pending|set|unsupported|failed","image","image_at_create",
   "image_src","image_error"}],
 "shipping_methods": [{"key","status","id","shipping_method","price"}],
 "offers": [{"key","status","id","name","code","pause_pending",
   "scope_conversion_pending"}],
 "edits": [{"edit","kind":"edit|undo|rolled_back","receipt","edit_sha256",
   "old_plan_sha256","new_plan_sha256","applied_at"}],
 "pending_edit": {"edit","kind","receipt","old_plan_sha256","new_plan_sha256",
   "objects": {"<section>:<key>": "pending|sending|verified"},
   "adds": {"<offer key>": "pending|verified"},
   "rollback": {"approved_sha256","objects","adds","plan_sha256"}},
 "active_update": {"change_set_sha256","merged_plan_sha256","started_at"},
 "ops": [{"n","method","section","key","status":"queued|in_flight|done","id"}],
 "removed": [{"key","section","status":"deleted","id","op","removed_at","..."}],
 "history": [{"at","change_set_sha256","from_plan_sha256","to_plan_sha256",
   "archived","settled","ops":[{"n","method","section","key","outcome"}]}],
 "pending_promotion": {"archived","sha256"}}
```

The update path's fields:

| Field | Written by | Meaning |
|---|---|---|
| `origin` | `apply` (`created`), `adopt` (`adopted`) | a missing value reads as `created`. `teardown`, `apply --resume` and `edit` refuse an adopted manifest |
| `adopted_at`, `adopted_from_campaign_id` | `adopt` | when ownership was taken, and of which campaign |
| `shipping_method` on a shipping entry | `apply`, `adopt`, `reconcile`, and a completed update | the store code, that method's immutable identity. Manifests from 0.7.5 and earlier do not carry it, so the ownership check falls back to the hash-verified base plan for those |
| `price` on a shipping entry | `adopt`, refreshed by a completed update | present only on runs that recorded one; a refresh never adds a field the run did not have |
| `image_src` on a package | `apply` and `update` when an image lands | the `src` this run sent. Images are diffed by intent against this value, never against the server-built thumbnail in `image`. Manifests from 0.8.0 and earlier do not carry it, so for those the diff falls back to the hash-verified base plan's `image.src` for that package, and a completed update records the field |
| `scope_conversion_pending` on an offer | `adopt --convert-scope` | an approved `all_packages` conversion the first `diff` will send as a PATCH. Cleared once it lands |
| `pause_pending` on an offer | `edit`, on an offer it added paused | set while the offer is created and not yet paused, cleared when the pause is read back |
| `edits` | each finished `edit` or `--undo` | one entry per edit: its number, kind, receipt file, approval hash and the plan hashes either side |
| `pending_edit` | `edit`, before the first write | the journal of an unfinished edit: the receipt, the plan hashes, and per object and per added offer how far it got. It exists only on a run that has been edited, and is removed when the edit completes or is rolled back. While it is present, `verify`, `apply --resume`, `diff` and `update` refuse; `edit` continues or rolls it back; and `teardown` accepts any of the three plan hashes it names (the plan the edit started from, the one it writes, and the one a rollback writes) and takes the identity of an offer the edit created from the receipt |
| `active_update` | `update`, before the first write | the change set and merged plan this update is applying. While it is present, `diff`, `verify`, `teardown`, `edit` and `apply --resume` refuse, and `update` needs `--resume` or `--settle` |
| `ops` | `update` | the journal. Every op is `queued`, then durably `in_flight` immediately before its request goes out, then `done`. Only an `in_flight` op can have landed with its response lost |
| `removed` | a completed DELETE | the receipt for an object that is gone: its old entry plus `section`, `op` and `removed_at`. The active section then holds only what is on the campaign |
| `history` | each promotion | one entry per completed or settled update: the change set, the plan hashes either side, the archive name, each op's outcome, and `settled: true` when `--settle` wrote it |
| `pending_promotion` | step 2 of a promotion | the bytes still to be copied onto `campaign-plan.json`. Any later command finishes the promotion before reading the plan, which is what makes an interrupted one recoverable |
| `promoted_at` | step 4 of a promotion | when the canonical plan last changed |

### change-set.json

```json
{"schema": 1, "run_id": "", "store_slug": "", "campaign_id": 0, "created_at": "",
 "base_plan_sha256": "<plan-sha256>", "merged_plan_sha256": "<plan-sha256>",
 "merged_plan_file": "campaign-plan.<sha8>.json",
 "baseline_sha256": "<baseline-sha256>", "baseline": {},
 "has_deletes": false, "warnings": [], "preserved": [], "plan_only": [],
 "ops": [{"n": 1, "method": "PATCH|POST|PUT|DELETE", "section": "", "key": "",
          "id": 0, "path": "", "body": {}, "body_template": {}, "package_keys": [],
          "depends_on": 0, "before": {}, "after": {}}],
 "merged_plan": {}}
```

Those 16 keys are all of them, and `diff` writes every one of them every time: a
list with nothing in it is written as `[]` rather than left out. On an op, `n`,
`method`, `section`, `key`, `path`, `body`, `before` and `after` are always there
(`body` is `null` on a DELETE and on an op whose body is deferred), while `id`,
`body_template`, `package_keys` and `depends_on` appear only in the cases the rules
below give.

- `schema` is 1. A change set written by another schema is refused rather than
  guessed at.
- `run_id`, `store_slug` and `campaign_id` bind it to one run and one campaign.
  `base_plan_sha256` has to equal both the manifest's `plan_sha256` and the hash
  of `campaign-plan.json` on disk, so a change set from before another update is
  refused.
- `merged_plan_file` names the bytes `diff` wrote and `update --plan` must pass;
  `merged_plan_sha256` is the hash of those bytes, and `merged_plan` is the same
  plan parsed, which `update` compares with the file it was given. `diff` is the
  only writer of that file, and promotion copies its bytes rather than
  re-serialising, so a merged plan re-indented by hand keeps its own formatting
  as long as the change set's hash is recomputed with it.
- `baseline` is the normalised live snapshot the operator reviewed and
  `baseline_sha256` its hash. `update` re-reads the campaign and recomputes that
  hash; a different answer is the "campaign changed since you reviewed the diff"
  refusal. `update --resume` compares the whole store against this baseline plus
  the `after` of every completed op.
- `preserved` and `warnings` are operator-facing lines: a value kept from the
  store, a dropped `image.src`, or a `--delete-changed` authorisation.
  `has_deletes` drives the `--allow-delete` gate.
- `plan_only` is every way the merged plan differs from the base plan that no
  request carries, one `what: before -> after` line each: a plan-only field such
  as a package `role`, a value the candidate and the dashboard reached
  independently, an advisory row dropped with the object it priced. It is what
  makes a change set with 0 ops worth writing, and a value `preserved` already
  prints is not repeated here.
- `created_at` is when `diff` wrote the change set. Nothing is bound to it; it is
  there for the operator reading two change sets in one run directory.
- An op's `path` is the exact route, rebuilt and re-checked by `update` from the
  campaign id, the section and the resolved id. `id` is absent on a POST and on
  an image PUT that waits for one. `body_template` plus `package_keys` stand in
  for a body that cannot exist yet, because the offer's scope names a package
  this change set creates: the real body is built once that POST has answered.
  `depends_on` names that POST's `n`. `before` and `after` are the field values
  either side of the op, in plan space, and they are what a resume compares
  against.

### The normalised snapshot

`baseline`, and every comparison the diff makes, is the live campaign in plan
space:

```json
{"campaign": {"id": 0, "name": "", "currency": "USD", "language": "en",
   "payment_gateway_group_id": 1, "paypal_account_id": null,
   "statement_descriptor": null, "additional_currencies": [],
   "available_payment_methods": [], "available_express_payment_methods": [],
   "available_shipping_countries": []},
 "packages": [{"id": 0, "product_id": 0, "product_variant_id": 0, "name": "",
   "price": "0.00", "price_recurring": null, "interval": null,
   "interval_count": null, "image": null}],
 "shipping_methods": [{"id": 0, "shipping_method": "", "price": "0.00"}],
 "offers": [{"id": 0, "name": "", "offer_type": "offer", "code": null,
   "available": true,
   "condition": {"type": "count", "value": 2, "all_packages": false, "package_ids": []},
   "benefit": {"type": "package_percentage", "value": "0.00", "price_rounding": null}}]}
```

Rules, because the hash moves if any of them change:

- Prices become plan-shaped decimal strings. A price the store does not report,
  or reports as something that is not a finite number, becomes `null`, which
  never equals a plan price, so it reads as a difference rather than a match.
- `condition.value` becomes an int (`"2.00"` to `2`) for a `count` condition and
  `null` for `any`. A value that is not a whole number becomes `null`: a blocker
  on adopt, a difference in a diff.
- `""` and `null` collapse for `statement_descriptor`, `interval` and
  `price_rounding`; `additional_currencies` `null` becomes `[]`.
- Code lists, `package_ids` and the sections themselves are sorted, so the hash
  does not move with the order the store happens to list things in.
- A package `name` is stripped to its bare form before comparison, by the name
  this run journalled, then the plan's `name - variant_title`, then the live
  `product_variant_name` suffix.
- Excluded entirely: `api_key`, timestamps, `product_purchase_availability`,
  `product_inventory_availability`, product name and sku, and the `description`
  fields on a condition or benefit.
- `image` is a receipt, not desired state. It is the server-built thumbnail, so
  it is hashed into the baseline (an image changed in the dashboard between the
  diff and the update is caught as drift) and never compared with a plan.

The baseline hash is the SHA-256 of that object serialised with sorted keys and
no whitespace. It covers every field above, `image` included, and nothing else.

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
for a creation plan hash. `edit`, `diff` and `update` refuse a clone manifest before any lock or receipt
write; the copy is changed by adopting it by its new id.

`clone_request.status` is `not_sent`, `sending`, `uncertain`, `rejected` or
`confirmed`. Before POST, the journal saves the id of every campaign that already
existed on the store, `attempted_at` and `attempt_deadline` (attempt time plus
request timeout plus 60 seconds). A `rejected` request keeps those fields as the
record of the one attempt that was made. Recovery never widens this window: candidate creation must fall between
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
owned entries, not the expected source rows, and only reference ids from the
approved snapshot are ever owned. It refuses to delete the campaign while any
unowned package, shipping method or offer remains under it. That check runs
immediately before the campaign DELETE, but the API offers no precondition, so
an object someone adds in the dashboard in the moment between the check and the
delete is removed with the campaign. The run lock serializes this skill's own
commands, not other writers. Before the clone
POST, the journal saves every campaign id that already existed, so a later
rename cannot make an unrelated campaign a recovery candidate. With incomplete inventory, teardown
refreshes only after checking the journaled destination ID and exact creation
instant, with its server name or approved rename. Source/destination equality
always refuses mutation.

Clone execution, resume and teardown take the run directory's `.run.lock`, the
same exclusive mode-600 lock every writing command holds. Resume and teardown load the manifest under that
lock. A new clone's preview and gate run before any directory or lock is created.
The existing run-directory gitignore and symlink guards apply.

Clone reports compare the destination with the saved approval, list current
source drift separately, and identify supported currencies missing from source
price rows as forex-derived. Cart probes are skipped with a reason because no
plan-derived landed prices were approved. Reports contain no campaign key.
