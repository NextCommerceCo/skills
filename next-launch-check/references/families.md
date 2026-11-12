# Check families

Each row in the report has an id that starts with its family. Status is one
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
| `pagespeed` | Google PageSpeed Insights mobile score and LCP, FCP, TBT, CLS against common targets (only with `--pagespeed`) | info | Treat as advice; lab scores vary |
| `pr-review` | Lines a PR adds: typed money amounts not bound to the SDK, direct cart or order API calls, secrets, debug leftovers, placeholders, route tag and SDK version changes (only with `--diff`) | blocker for secrets; warn otherwise | Bind prices with `data-next-display`; use SDK attributes or its JS API; remove and rotate secrets |

## Browser lane (`--browser`, Chromium)

Analytics and ad beacons are answered locally, order calls are blocked, and
the form check blocks every write request. See `safety.md`.

| Family | What it checks | Severity | When it fails |
|---|---|---|---|
| `browser-runtime` | Console and script errors; the SDK reaches ready (0.5: `data-next-ready` on `<html>`; 0.4: `next-display-ready` class) within 10 s; visible `NaN`, `undefined` or raw template text | blocker when the SDK is not ready on a checkout or upsell | Open the page with `?debug=1` (SDK 0.5) or `?debugger=true` (0.4) to see the cause |
| `tags-observed` | Tag libraries that actually load (GTM, gtag, Meta, TikTok, Everflow), beacons that would have been sent, dataLayer event names | warn | GTM in source but `gtm.js` never loading usually means a blocked or broken snippet |
| `responsive` | Content running off the right edge on a phone (390 px) and at 125% and 150% zoom; screenshots of each | warn | Find the elements listed in the evidence; usually fixed widths or long unbroken text |
| `param-propagation` | Test UTM parameters survive each click from the entry page to the checkout | warn | Carry the query string onto funnel links; attribution depends on it |
| `param-toggles` | Each of `exit=n`, `timer=n`, `reviews=n`, `loading=n`, `banner=n`, `seen=n` on the entry page and the checkout, with screenshots of what disappears | check by hand | These are campaign-side conventions, not SDK features; wire the ones your campaign promises |
| `locale` | SDK-bound amounts agree between an en-US and a de-DE browser; amounts typed into the page (info) | warn | Custom code that parses formatted prices breaks in comma-decimal locales; read values from the SDK instead |
| `form-negative` | Submitting the empty checkout is stopped with visible errors; an invalid email is flagged; no order call leaves the browser | blocker when an empty form tries to create an order | Use the SDK's checkout fields and validation |
| `storefront-search`, `storefront-login`, `storefront-nav` | Storefront: a nonsense search shows a clean empty state; an empty login submit shows an error; header and footer links load | warn | — |
| `design-parity` | Text styles and colours from Figma CSS found on the rendered page; screenshots at each frame width next to the exported frames | check by hand | Compare side by side; differences can be intended |

## Order (`record`)

| Row | What it checks | Severity |
|---|---|---|
| `order:created` | An order call was made and answered 2xx | blocker |
| `order:attribution` | Parameters on the entry URL reached the order's attribution | warn |
| `order:upsells` | Each upsell call answered 2xx | blocker |
| `order:purchase-event` | Exactly one purchase event reached the dataLayer | warn |
| `order:params-per-page` | The entry parameters stayed in the URL after checkout | info |
| `order:receipt` | The receipt page was reached | blocker |

## Campaigns OS

`campaigns-os:verdict` appears when you pass `--campaigns-os-verdict`. Rows
in `http`, `funnel-chain`, `legal-links`, `param-propagation`,
`param-toggles`, `browser-runtime` and `order` are then marked deferred:
that verdict is the authority for them on the same build.
