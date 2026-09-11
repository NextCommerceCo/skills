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
| POST | `campaigns.apps.29next.com/api/v1/carts/calculate/` | pricing truth (verify) |

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
 "shipping_methods": [{"shipping_method","price"}],
 "offers": [{"key","name","offer_type","code",
   "condition":{"type","value","package_keys"},
   "benefit":{"type","value","price_rounding"}}],
 "landed_prices": [{"tier","kind":"tier|single|upsell","qty","offer_key","package_keys",
   "anchor","pct","unit_after","order_total"}],
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
`landed_prices` row carries its own `package_keys` and `offer_key`, so `verify`
builds its cart cases by lookup rather than parsing the display label. Resume matches
`pending` entries by identity (package: variant id + name; shipping: code;
offer: name) before creating anything, so a lost response never duplicates.
