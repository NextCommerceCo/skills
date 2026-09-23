# Worked examples

Numbers use the Photo Bracelet fixture: anchor **49.95**, no `price_rounding`.
`landed_unit` subtracts a per-unit discount rounded to cents (half-up). These
are the forecasts `recommend` prints; `verify` confirms them against
`carts/calculate`.

Quantity Buy 1/2/3 is unchanged. Buy-X-get-Y and gift-with-purchase are
labeled approximations inside the existing percentage fields. See
[offer-doctrine.md](offer-doctrine.md).

## 1. Quantity Buy 1/2/3 (50 / 55 / 60)

`--offer-type quantity --ctc low --anchor-price 49.95 --tiers 50,55,60`

One automatic `package_percentage` per quantity, scoped to every hero variant.
Every in-scope unit gets that tier's percentage. This is not Nth-unit-free.

| Offer | Qty | % | Unit | Total |
|---|---|---|---|---|
| Buy 1 | 1 | 50 | 24.97 | 24.97 |
| Buy 2 | 2 | 55 | 22.48 | 44.96 |
| Buy 3 | 3 | 60 | 19.98 | 59.94 |

Buy 1: 50% of 49.95 is 24.975, rounds to 24.98 off, unit **24.97**.
Buy 2: 55% of 49.95 is 27.4725 → 27.47 off, unit **22.48**, total 44.96.
Buy 3: 60% of 49.95 is 29.97 off, unit **19.98**, total 59.94.

Locked by `Recommendation.test_low_ctc_multi_variant`.

## 2. Buy 2 get 1 free (~33.33% at count 3)

`--offer-type bxgy --paid-qty 2 --free-qty 1 --ctc low --anchor-price 49.95`

Encoded as one automatic offer: `count = 3`, `package_percentage = 33.33`.
At exactly three equal-priced units the payable matches paying for two.

| Row | Qty | % | Unit | Full retail | Payable | vs true repeating BOGO |
|---|---|---|---|---|---|---|
| Buy 1 at list | 1 | 0 | 49.95 | 49.95 | 49.95 | n/a (count 3 not met) |
| Buy 2 get 1 free | 3 | 33.33 | 33.30 | 149.85 | 99.90 | 99.90 (one deal) |
| Qty 4 (engine; not true repeat) | 4 | 33.33 | 33.30 | 199.80 | 133.20 | 149.85 (one deal + one leftover at list) |

33.33% of 49.95 is 16.648335 → 16.65 off, unit **33.30**. Three units 99.90.
Four units still get 33.33% off all four (133.20), not one free-unit deal plus
a leftover at 49.95 (149.85). The engine cannot encode true repeating BOGO.

Buy 1 get 1 (50% at count 2) on the same anchor: unit 24.97, two-unit total
**49.94** versus paying for one at 49.95. That one-cent gap is the same
rounding as quantity Buy 1, not a second encoding.

## 3. Buy 3 get 2 free (40% at count 5)

`--offer-type bxgy --paid-qty 3 --free-qty 2 --ctc low --anchor-price 49.95`

| Row | Qty | % | Unit | Full retail | Payable |
|---|---|---|---|---|---|
| Buy 1 at list | 1 | 0 | 49.95 | 49.95 | 49.95 |
| Buy 3 get 2 free | 5 | 40 | 29.97 | 249.75 | 149.85 |
| Qty 6 (engine; not true repeat) | 6 | 40 | 29.97 | 299.70 | 179.82 |

40% of 49.95 is 19.98 off, unit **29.97**. Five units 149.85, which matches
paying for three. Qty 6 still gets 40% off all six (179.82), not one deal of
five plus one leftover at list (199.80).

### Mixed-price divergence (not cheapest-free)

`recommend` prices every hero variant at one anchor, so mixed catalogue
prices do not appear in the landed table. The engine still applies the same
percentage to every matching unit.

If a count-3 cart held two units at 49.95 and one at 24.95, buy 2 get 1 at
33.33% would charge:

| Unit list | 33.33% off (this encode) | Cheapest-free (not available) |
|---|---|---|
| 49.95 | 33.30 | 49.95 (paid) |
| 49.95 | 33.30 | 49.95 (paid) |
| 24.95 | 16.63 | 0.00 (free) |
| **Payable** | **83.23** | **99.90** |

Proportional-off-all saves 41.62. Cheapest-free would save 24.95. The
Campaigns App has no cheapest/most-expensive selector, so the skill cannot
promise the right-hand column.

## 4. Gift with purchase

`--offer-type gwp --ctc high --anchor-price 49.95 --gift 7:24.95`

Hero packages stay at 49.95 with no quantity-tier percentages. Gift variant 7
is a separate package at 24.95. One automatic offer, `gift-free`, is 100%
`package_percentage` with `condition: any`, scoped **only** to that gift
package, and with `price_rounding` omitted.

| Row | Package | Qty | % | Unit | Total |
|---|---|---|---|---|---|
| Buy 1 | hero | 1 | 0 | 49.95 | 49.95 |
| Gift Memorial Ornament | gift-7 | 1 | 100 | 0.00 | 0.00 |

The gift is free whenever it is in the cart, including a gift-only cart.
Free shipping, if requested, stays on the hero keys, so a gift-only cart
still pays shipping.

The Offers API does not auto-add the gift, does not remove it when the hero
is absent, and does not wait for a min spend or hero quantity. Funnel setup
(`noSlot: true` on a bundle item, or a visible gift choice) is a
`next-campaigns-setup` handoff.

`--offer-type quantity --gift 7:24.95` keeps the Buy 1/2/3 ladder on the
hero and adds the same gift composition. The 100% gift offer does not compete
with the hero percentages because they name different package keys.

## 5. Upsells: one voucher per product, package reuse

`--hero 10 --ctc high --anchor-price 189.95 --upsell 16:39.95:50 --upsell 17:39.95:50`

Both Music Photo Magnet variants land under one voucher, `Music Photo Magnet -
50%`, code `MUSICPHOTOMAGNET50`, scoped to `upsell-16` and `upsell-17`. There is
one landed row per variant, both pointing at that voucher, so `verify` prices
each variant with the code in upsell mode.

| Row | Package | % | Unit |
|---|---|---|---|
| Upsell Music Photo Magnet - 4 in - 50% (upsell-16) | upsell-16 | 50 | 19.97 |
| Upsell Music Photo Magnet - 6 in - 50% (upsell-17) | upsell-17 | 50 | 19.97 |

50% of 39.95 is 19.975, which rounds to 19.98 off, so the unit is **19.97**.

`--hero 22 --ctc low --anchor-price 49.95 --upsell 23:49.95:50` is the hero
product as its own upsell. Variant 23 is already `hero-23` at 49.95, so no
second package is created: voucher `PHOTOBRACELET50` is scoped to `hero-23`. In
upsell mode the cart is 24.97. Entered at checkout the code stacks on the Buy 1
tier, 24.97 then 50% again, landing at 12.48. The plan's handoff says so, and the
funnel never shows that code on the checkout page. An upsell price other than
49.95 is refused, because there is one package per variant. For example
`--upsell 23:39.95:50` asks for 19.97, and the error suggests
`--upsell 23:49.95:60`, which lands at 19.98 on the existing package.
