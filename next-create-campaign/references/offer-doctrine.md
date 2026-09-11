# Offer doctrine for Campaigns App campaigns

This is the reasoning the `recommend` subcommand encodes when it turns a store
snapshot into a campaign plan. The tool applies these rules; it does not make the
commercial calls behind them. Cost-to-consumer, the anchor price, any checkout
bumps and any post-purchase upsells are operator inputs, and the tool never infers
them from the catalogue or from a price.

## Cost-to-consumer decides the structure

Cost-to-consumer (CTC) is what the customer pays, not what the product costs to
make. A product that sells for $30 may cost a few dollars to manufacture; the
offer structure follows the $30.

Low CTC means the plan sells more units of the same product. Buying two or three
of a cheap item is normal behaviour, so the checkout offers Buy 1, Buy 2 and Buy 3
with escalating discounts. The plan creates one package per variant and adds one
automatic tier offer per quantity.

High CTC means single-unit packages and no quantity tiers. A customer who has just
committed to an expensive purchase is unlikely to add a second unit of the same
thing on the checkout page. Order value comes from low-friction checkout bumps and
post-purchase upsells instead.

Bundling is also how a low-ticket product pays for its traffic. A single unit
often cannot carry a typical acquisition cost: as an illustration, a product that
sells for $30 cannot fund a $50 acquisition cost on one unit. Putting two units in
the first order, for example buy one and get the second at half price, lifts the
order value toward the acquisition cost. That works better than pushing the
acquisition cost down,
because a lower payout reduces what affiliates earn per click and they move their
traffic elsewhere.

## Standard tiers

The default tiers take a percentage off the anchor price:

| Tier | Discount off the anchor |
|---|---|
| Buy 1 | 50 percent |
| Buy 2 | 55 percent |
| Buy 3 | 60 percent |

The falling unit price rewards the larger order, and the anchor makes the
multi-unit option read as the better deal. The anchor is supplied by the operator
and may differ from the catalogue price.

Launch for conversion rate first, then work on order value. If the offer does not
convert, it is very hard to make that up with upsells, and affiliates stop sending
traffic to an offer that earns them little per click. Launch lean with the simplest
offer that converts, then add bumps and upsells once traffic is flowing.

## Exit-pop voucher

Every campaign gets an exit voucher, shown when the customer signals they are
leaving. It offers an extra 5 to 10 percent, 10 by default, with added urgency.
It is the last chance to save the order.

The exit voucher stacks on the offer-adjusted price, so an extra 10 percent is 10
percent off the price the customer is already looking at, at every quantity tier.
See [Rounding and stacking](#rounding-and-stacking) for the arithmetic.

## Checkout bumps

A checkout bump is an add-to-order item on the checkout page. It has to be a quick
yes that does not interrupt the main purchase.

Good bumps:

- shipping insurance, especially on high-ticket products
- an extended warranty
- a small accessory that complements the hero product

Bad bumps:

- anything expensive enough to make the customer reconsider the whole order
- anything that needs its own decision

If you confuse, you lose. A bump that makes the customer think adds friction to the
original purchase and costs conversion rate.

Some stores require a standing bump on every campaign, such as a shipping-insurance
product. That is the operator's call and is passed to `recommend` explicitly. The
tool never infers a bump from the catalogue.

## Post-purchase upsells

Upsells run after the first order is placed.

- Keep them low-ticket. The customer is in a buying frame of mind, but only for
  small additions. This holds for high-CTC campaigns too: the high price sits on
  the hero product, not in the upsell flow.
- Discount them with vouchers. Site offers do not fire post-purchase.
- Put the upsell most likely to be taken first, while buying momentum is highest.
- Stop at 5 or 6. Past that, take rates drop and an extra upsell tends to lower
  total upsell revenue.

## Offer types and where they work

The Campaigns App has two offer types, and they work on different pages:

| Type | Where it works | Triggered by | Use for |
|---|---|---|---|
| Site `offer` | Checkout page only | Automatically, by quantity conditions | Tiered quantity pricing on the main checkout |
| `voucher` (coupon) | Every page: checkout, upsell, downsell, exit pop | A code | Upsell discounts, the exit pop, any discount off the checkout page |

Rules:

- Upsell page offers must be vouchers.
- Exit-pop offers should be vouchers. The exit pop applies the code itself.
- The checkout can use either type. Site offers are preferred for tiered quantity
  pricing because they apply on their own when the cart meets the condition.

Site offers silently do nothing on upsell pages. There is no error and no warning;
the discount just does not apply. This is the single most common offer setup
mistake.

Offers are applied campaign-wide by the offer engine. The engine evaluates every
offer against the cart by its type and condition, and an offer is not scoped to the
page it is attached to. Removing an automatic offer from a page therefore does not
stop it firing. To stop an automatic offer from overriding the tiers, convert it to
a voucher.

Voucher codes are case-insensitive in the SDK but stored as entered, so write them
in uppercase.

## Naming and scoping

Package names:

- A single-variant package is named with the bare product name, for example
  `Travel Mug`.
- A variant package is `{Product Name} - {Variant}`, for example
  `Travel Mug - Blue`. The API appends the variant title itself for variant
  packages, so the plan sends the bare product name and records the variant title
  separately.
- The old `2x Product` convention for quantity packages is deprecated. Tier pricing
  is done with offers, never with a separate package per quantity.

Offer names:

| Offer | Name | Code |
|---|---|---|
| Checkout tier (site offer) | `{Product} - Buy {n} - {pct}%` | none |
| Upsell voucher | `{Product} - {pct}%` | `{PRODUCT}{PCT}`, uppercase alphanumeric, for example `TRAVELMUG50` |
| Exit voucher | `{Product} - Exit - {pct}%` | `--exit-code` when given, else `{PRODUCT}{PCT}`; keep it short, for example `SAVE10` |

Scoping rules:

- Every site offer is scoped to the specific package ids it discounts, never to
  "all packages". On a checkout that carries more than one product, an offer on all
  packages discounts every package, accessories and bumps included. "All packages"
  also covers packages created later.
- Variants added to a campaign after an offer was configured are silently excluded
  from that offer. They get no discount and do not count toward its quantity
  condition, so a mixed cart can drop a tier. Add every variant to each offer's
  package list.
- A "Buy 1 at 50 percent" site offer with a minimum quantity of 1 makes the
  discounted price the effective base price. The package list price becomes the
  compare-at anchor, not the price anyone pays.
- Voucher display names are visible to the customer in the cart summary, and the
  Campaigns App requires them to be unique within a campaign. The names
  `recommend` generates are readable as they stand. If you rename one in the plan,
  keep it customer-facing and never use an internal code.
- Exit codes appear in a popup, so a short code such as `SAVE10` (passed with
  `--exit-code`) reads better than the generated product-based one.

## Rounding and stacking

The Cart API `calculate` call is the source of truth for what a cart costs. A
landed price computed locally is a forecast until `calculate` confirms it, which is
what `verify` does.

Unit-price rounding:

- Every qualifying unit rounds to the same unit price, and units of one package
  stay consolidated on one line. A bundle total is always quantity times unit.
  Splitting a quantity across variants costs the same as one line of that quantity.
- Charm totals that do not divide evenly are unreachable. $149.99 on 5 units is
  impossible, only $149.95 or $150.00. $99.99 on 3 units works, at $33.33 a unit.
  Check promised price points against this before the plan is approved.
- The per-line discount amounts `calculate` returns can show odd cents, because
  the aggregate absorbs the rounding remainder. The unit price and the grand total
  are still exact.

The offer's `price_rounding` setting pins the cents of the discounted unit price.
It changes the target, not the line structure. The API accepts these values:

- `null` (no rounding)
- `"0.00"`
- `"0.95"`
- `"0.97"`
- `"0.99"`

Stacking:

- The engine applies discounts in order: the offer first, then the voucher.
- A percentage voucher takes its percentage of the offer-adjusted price of the
  lines it targets. It does not use the retail base price, and it does not use the
  cart subtotal. Lines the voucher does not target, such as a shipping-insurance
  bump, add nothing to its base and are left alone by it.
- Stacking is multiplicative. A 50 percent offer plus a 50 percent voucher is 75
  percent off, not free.
- The voucher discount is computed per unit and rounded to cents, and the line
  total is quantity times that unit. A cart total can therefore differ from a
  literal percentage of the total by a few cents. That is expected.
- `verify` allows a tolerance of one cent per unit when it compares a forecast to
  `calculate`.

Engineering a voucher to a target price:

- Starting percentage:
  `voucher % = (offer-adjusted unit - target unit) ÷ offer-adjusted unit × 100`.
- This is a first guess, not a guarantee. The engine rounds the per-unit discount
  to cents, so the landed unit can sit a cent either side of the target.
- Worked example: an offer-adjusted unit of $45.53 and a target of $40.99 give
  `(45.53 - 40.99) ÷ 45.53 × 100 = 9.97…`, so 9.97 percent. Checked back through
  the rounding, 9.97 percent of $45.53 is $4.5393, which rounds to $4.54 and lands
  at $40.99. A plain 10 percent is $4.553, which rounds to $4.55 and lands at
  $40.98.
- `recommend` takes whole percentages only. A fractional voucher goes into the
  plan file by hand before the `plan` review, which gives it a new hash to
  approve.

On the Cart API `calculate` call the coupon field is `vouchers: [...]`. The fields
`coupon` and `coupons` are silently ignored: the call returns 200 with no voucher
applied, which is easy to misread as a code that does not work.

## Campaign settings

- Name: the hero product name.
- Currency: must match the target market, and it cannot be changed after the
  campaign is created. Pick it together with the payment gateway group. The group
  must list that currency and every payment method code the campaign enables; a
  group with no gateway for the currency leaves the campaign with no payment
  methods.
- Language: must match the audience.
- Packages: all packages for one hero product live under one campaign. Campaign
  analytics are tied to the campaign API key, so splitting a product's packages
  across campaigns splits its reporting.
- A different language is a different campaign. Additional currencies and shipping
  countries fit inside one campaign.

The API does not cover these, and they stay dashboard work:

- Allowed Domains, for both Development and Production
- PayPal account linking (the API takes a `paypal_account_id` only)
- Map Builder

## Metadata definitions

A store running the Campaigns App needs 11 metadata definitions before its
campaigns run. Two are `order` fields, `nc_campaign_id` and `nc_campaign_name`,
stamped onto each order by the flow from the Campaigns API to the Admin API. The
other nine are `attribution` fields attached by the Campaign Cart SDK. Without the
definitions, orders lose campaign attribution silently.

Definitions are set once per store, not per campaign; every campaign on the store
shares them. The `discover` subcommand audits them, and `metadata --apply` creates
the missing ones. Every definition is created with export enabled.

| Name | Object | Key | Type | Max length | Filter |
|---|---|---|---|---|---|
| Campaign ID | order | `nc_campaign_id` | text | 255 | yes |
| Campaign Name | order | `nc_campaign_name` | text | 255 | yes |
| Conversion Timestamp | attribution | `conversion_timestamp` | integer | none | no |
| Device | attribution | `device` | text | 500 | no |
| Device Type | attribution | `device_type` | text | 50 | yes |
| Domain | attribution | `domain` | text | 500 | no |
| Landing Page | attribution | `landing_page` | text | 1000 | no |
| Referrer | attribution | `referrer` | text | 2000 | no |
| SDK Version | attribution | `sdk_version` | text | 50 | no |
| Timestamp | attribution | `timestamp` | integer | none | no |
| User IP | attribution | `user_ip` | text | 100 | no |

API gotcha: creating a definition with the `X-29next-API-Version` header sent
returns 201, but the field is then invisible in the dashboard and in the versioned
GET. The engine sends the version header on GET and omits it on POST.

Metadata keys are unique per store, not per object. A key that is already defined
on a different object is a conflict: the tool reports it and never edits the
existing definition.

Permissions: `metadata:read` for the audit, `metadata:write` for creation.
