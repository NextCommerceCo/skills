# next-create-campaign: Admin API contract and file schemas

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
| `campaigns:read`, `campaigns:write`, `catalogue:read`, `gateways:read` | `discover`, `plan --check-store`, `apply`, `verify`, `teardown` |
| `metadata:read` | the metadata audit in `discover` |
| `metadata:write` | `metadata --apply` |

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
| GET/DELETE | `/api/admin/campaigns/{id}/` | retrieve (returns `api_key`) / delete |
| GET/POST | `/api/admin/campaigns/{id}/packages/` | list / create |
| DELETE | `/api/admin/campaigns/{id}/packages/{packageId}/` | delete |
| PUT | `/api/admin/campaigns/{id}/packages/{packageId}/image/` | replace the package image (optional override) |
| GET/POST | `/api/admin/campaigns/{id}/shipping-methods/` | list / create |
| DELETE | `/api/admin/campaigns/{id}/shipping-methods/{id}/` | delete |
| GET/POST | `/api/admin/campaigns/{id}/offers/` | list / create |
| GET/DELETE | `/api/admin/campaigns/{id}/offers/{offerId}/` | retrieve / delete |
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
- One store code can carry several campaign shipping methods at different prices.
  Each create returns its own id, the Cart API lists all of them under that code,
  and `carts/calculate` charges the price of the id the cart names. Store shipping
  methods are read-only over the Admin API, so this is how a store with a single
  shipping method offers a paid-shipping ladder, for example `default` at $9.99,
  $12.99, $14.99 and $16.99. In the plan:
  - Each `shipping_methods[]` entry may carry a `key`. It defaults to the code and
    is the entry's identity in the manifest. Keys must be unique, and a repeated
    code needs a key on each entry. The same code at the same price twice is
    rejected.
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
  `"0.97"`, `"0.99"`.
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
    probes, and fails on any this run did not create. It does not read the live
    `condition.value` back: the Admin API's offer retrieve body is undocumented
    for it. If a live threshold still looks wrong after that check, prove it with
    a hand `carts/calculate` at N and N-1 units rather than assuming the plan's
    number is what the campaign stored.
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
| High CTC: single unit, no tiers, bumps + upsell vouchers | [Cost-to-consumer decides the structure](offer-doctrine.md#cost-to-consumer-decides-the-structure) |
| Every campaign gets an exit-pop voucher (extra 5 to 10%) | [Exit-pop voucher](offer-doctrine.md#exit-pop-voucher) |
| Tier offers are `offer` type, scoped to hero package ids, never `all_packages` | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Upsell/exit offers are `voucher` type (site offers don't fire post-purchase) | [Offer types and where they work](offer-doctrine.md#offer-types-and-where-they-work) |
| Voucher code `{PRODUCT}{PCT}`, uppercase alphanumeric | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Package name `{Product}` or `{Product} - {Variant}`; never `2x Product` | [Naming and scoping](offer-doctrine.md#naming-and-scoping) |
| Campaign name = hero product; gateway group must carry the currency | [Campaign settings](offer-doctrine.md#campaign-settings) |
| Landed price is a forecast until confirmed against `carts/calculate` | [Rounding and stacking](offer-doctrine.md#rounding-and-stacking) |

CTC and the anchor price are operator inputs. The tool never classifies CTC from a
price or reads a package price from the catalogue for bumps or upsells.

## Output files

Files live in a run directory:

- `discover` defaults to `./next-create-campaign-runs/<slug>/` under the current
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
  request `path`.

### campaign-plan.json

```json
{"store_slug": "...", "store_origin": "https://<slug>.29next.store",
 "generated_at": "...", "ctc": "low|high",
 "campaign": {"name","currency","language","payment_gateway_group_id",
   "additional_currencies","available_payment_methods",
   "available_express_payment_methods","available_shipping_countries",
   "statement_descriptor"},
 "packages": [{"key","role":"hero|bump|upsell","name","variant_title",
   "product_id","product_variant_ids","price",
   "image":{"src","file_name"}}],
 "shipping_methods": [{"key","shipping_method","price"}],
 "offers": [{"key","name","offer_type","code",
   "condition":{"type","value","package_keys"},
   "benefit":{"type","value","price_rounding"}}],
 "landed_prices": [{"tier","kind":"tier|single|upsell","qty","offer_key","package_keys",
   "shipping_key","anchor","pct","unit_after","order_total"}],
 "rationale": ["..."], "blockers": ["..."], "waivers": ["..."], "handoff": ["..."]}
```

### run-manifest.json

```json
{"store_slug","store_origin","plan_sha256","run_id","started_at",
 "campaign": {"status":"pending|created|deleting|deleted","id","api_key","name","created_at"},
 "packages": [{"key","status","id","name","product_variant_id",
   "image_status":"pending|set|unsupported|failed","image","image_at_create","image_error"}],
 "shipping_methods": [{"key","status","id"}],
 "offers": [{"key","status","id","name"}]}
```

`package_keys` in the plan resolve to created ids at apply time. Each
`landed_prices` row carries its own `package_keys`, `offer_key` and optional
`shipping_key`, so `verify` builds its cart cases by lookup rather than parsing
the display label. Manifest shipping entries are keyed by the shipping key, which
is the store code for an entry without one, so manifests written before 0.4.0
still resume, verify and tear down. Resume matches `pending` entries by identity
(package: variant id + name; shipping: code + price; offer: name) before creating
anything, so a lost response never duplicates.
