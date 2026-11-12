# Check families

The report is organised by the QA checklist (`checklist.json`). Each checklist
item is decided by, or supported with evidence from, the automated rows below.
Each row has an id that starts with its family. Status is one
of `pass`, `fail`, `warn`, `manual_review` (a person must look; evidence is
attached) or `skipped`. Severity is `blocker`, `warn` or `info`. Rows with
severity `info` are advice and never change the result.

The result is `blocked` when any `fail` row has severity `blocker`;
`ready_with_exceptions` when there is any other failure, warning or
check-by-hand row; otherwise `ready`. While manual checklist items are open
the result shows `incomplete`.

## Source lane (plain HTTP reads, no page code runs)

| Family | What it checks | Severity | When it fails |
|---|---|---|---|
| `http` | Every crawled page answers 2xx; slow HTML and cross-site redirects are noted | blocker | Fix the route or the deploy; a 404 here usually comes from a route tag or a link |
| `funnel-chain` | On the checkout, `next-success-url` (legacy alias `next-next-url`) loads; on each upsell, `next-upsell-accept-url` and `next-upsell-decline-url` load and do not point back to the same page; the pages you named are in the order the tags lead; `next-funnel` is the same on every page | blocker for a target that does not load; warn for a missing tag | Point the tag at the real next page. A missing tag makes the SDK fall back to its default route |
| `sdk` | The Campaign Cart SDK script is on every checkout, upsell and receipt page, pinned to an exact version, the same version everywhere, loaded once; one campaign API key across pages (shown masked) | blocker for a missing SDK or two different keys | Pin the script URL to an exact version; use one config for every page |
| `tracking-consistency` | GTM, GA4, Meta and TikTok ids and the Everflow click script found in source, and the same on every page | warn | Add the missing snippet, or align the ids. A tag loaded only through GTM shows in `tags-observed` instead |
| `legal-links` | Terms, privacy, returns, shipping, contact and FAQ links exist and load; on checkout and upsell pages they open in a new tab | warn | Add or fix the link; add `target="_blank"` on checkout and upsell pages so the shopper does not leave |
| `links` | Links that go nowhere (`#`, empty, `javascript:void`, placeholder hosts), links to a preview deployment from a live page, same-site links that do not load, buttons with no visible wiring | warn; buttons are info | Wire or remove the link |
| `content-hygiene` | Visible lorem ipsum, TODO/FIXME/TBD, placeholder copy, unrendered template tags; empty headings and images without `alt` (info) | warn | Replace the copy |
| `seo-meta` | Title, description, `og:title`, `og:image`, `twitter:card`, favicon, `lang`; a missing viewport tag is a warn | info (viewport warn) | Add the tags; without a viewport tag phones render the desktop layout |
| `media-weight` | Images and video over 500 KB; files over 300 KB not on a known CDN host; files that do not load | warn | Compress, resize or move to a CDN |
| `storefront-domain` | Links, form actions, canonical and `og:url` that point at the platform's default `*.29next.store` address from a page on your own domain | warn | Use your own domain |
| `speed` | Mobile and desktop speed test per page (`speed:<page>:mobile`, `:desktop`): PageSpeed Insights with your key, else Lighthouse on your machine, else keyless PageSpeed. Targets: score ≥ 50, FCP ≤ 3 s, LCP ≤ 4 s, TBT ≤ 200 ms, CLS ≤ 0.1, Speed Index ≤ 4.4 s | warn | Use the PageSpeed link for the full audit; lab scores vary run to run |
| `pr-review` | Lines a PR adds: typed money amounts not bound to the SDK, direct cart or order API calls, secrets, debug leftovers, placeholders, route tag and SDK version changes (only with `--diff`) | blocker for secrets; warn otherwise | Bind prices with `data-next-display`; use SDK attributes or its JS API; remove and rotate secrets |

## Browser lane (`--browser`, Chromium)

Analytics and ad beacons are answered locally, order calls are blocked, and
the form check blocks every write request. See `safety.md`.

| Family | What it checks | Severity | When it fails |
|---|---|---|---|
| `browser-runtime` | Console and script errors; the SDK reaches ready (0.5: `data-next-ready` on `<html>`; 0.4: `next-display-ready` class) within 10 s; visible `NaN`, `undefined` or raw template text | blocker when the SDK is not ready on a checkout or upsell | Open the page with `?debug=1` (SDK 0.5) or `?debugger=true` (0.4) to see the cause |
| `tags-observed` | Tag libraries that actually load (GTM, gtag, Meta, TikTok, Everflow), beacons that would have been sent, dataLayer event names | warn | GTM in source but `gtm.js` never loading usually means a blocked or broken snippet |
| `responsive` | Content running off the right edge on a phone (`:mobile`, 390 px), at 125% (`:zoom-125`) and at 150% zoom (`:zoom-150`); a screenshot of each | warn | Find the elements listed in the evidence; usually fixed widths or long unbroken text |
| `param-propagation` | Test UTM parameters survive each click from the entry page to the checkout | warn | Carry the query string onto funnel links; attribution depends on it |
| `param-toggles` | One row per page and parameter (`param-toggles:<page>:timer`…). The check finds what each toggle should hide (countdowns, reviews and testimonials, promo banners, "As seen on" logos, fake stock or loading bars) by markup and wording, loads the page with `=n` and compares: hidden → pass; still visible → fail; nothing of that kind on the page → pass (nothing to hide). Exit-intent pop-ups only show when the shopper leaves, so their presence is reported for a person to try. Baseline and `=n` screenshots are attached | warn |
| `locale` | SDK-bound amounts agree between an en-US and a de-DE browser; amounts typed into the page (info) | warn | Custom code that parses formatted prices breaks in comma-decimal locales; read values from the SDK instead |
| `form-negative` | Submitting the empty checkout is stopped with visible errors; an invalid email is flagged; no order call leaves the browser | blocker when an empty form tries to create an order | Use the SDK's checkout fields and validation |
| `storefront-search`, `storefront-login`, `storefront-nav` | Storefront: a nonsense search shows a clean empty state; an empty login submit shows an error; header and footer links load | warn | — |
| `design-parity` | Text styles and colours from Figma CSS found on the rendered page; screenshots at each frame width next to the exported frames | check by hand | Compare side by side; differences can be intended |

## Orders (`order`, `record`)

`order` places typed test-card orders after the tester agrees; `record` watches an order
the tester places. Each order's rows carry its label: `1-standard-accept`, `2-3ds-decline`,
or `recorded`. `order --dry-run` adds `order:rehearsal:<label>` instead and sends nothing.

| Row | What it checks | Severity |
|---|---|---|
| `order:created` | An order call was made and answered 2xx | blocker |
| `order:attribution` | Parameters on the entry URL reached the order's attribution | warn |
| `order:upsells` | Each upsell call answered 2xx | blocker |
| `order:purchase-event` | Each purchase event name fired once (the SDK's `dl_purchase` and a GA4 `purchase` side by side is fine; the same name twice is a duplicate) | warn |
| `order:params-per-page` | The entry parameters stayed in the URL after checkout | info |
| `order:receipt` | The receipt page was reached | blocker |

## Scope

`scope:entry` (info) appears when the page you named does not look like the type you said, for example a checkout with no checkout markers. Checks follow what you said.

## Campaigns OS

When `campaigns-os qa` runs (or `--campaigns-os-verdict` is given), every verdict row is
imported as `cos:<family>:<id>`, with its page type taken from the Campaign Map page id.
Families: `funnel-flow`, `meta-tags`, `api-metadata`, `browser-runtime` (payment surface,
wallets, bundle selector, bumps, SDK debugger, request failures), `template_residue`,
`pricing`, `browser-test-order`, `browser-receipt-rendering`, `analytics-correctness`,
`analytics-parity`, `parity-capture`, `theme_gate`, `polish_gate`. They decide the
"Campaigns OS (spec-aware)" items and add evidence to others (prices, exit pop, CTAs,
test orders, purchase events). `campaigns-os:verdict` is its overall disposition. Our
`http` and `funnel-chain` rows defer to its `funnel-flow`, and `order` rows to its
`browser-test-order`, when it proved them.
