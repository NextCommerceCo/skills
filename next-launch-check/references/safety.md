# Safety

The launch check is meant to be safe to run against a live funnel.

| Part | What it does to the funnel |
|---|---|
| Source lane | GET and HEAD requests only. Page code does not run, so no tag fires. |
| Browser lane | Pages run in Chromium. Requests to analytics and ad endpoints (GA4 collect, Google Ads conversion, Meta, TikTok, Bing, Pinterest, Snap, Reddit, Everflow click and conversion) are answered locally with an empty 204 and listed in the report. Tag libraries still load so the report can show what would fire. `--live-beacons` turns this off. Order and upsell calls are blocked. |
| Form check | Every write request (POST, PUT, PATCH, DELETE) is blocked while it submits the empty and invalid-email checkout. If the page tries to create an order anyway, that is reported as a blocker; the call never leaves the browser. |
| Storefront checks | Every write request is blocked, so no account, cart or order is created. |
| Test parameters | Browser runs add `utm_source=next-launch-check`, `utm_medium=qa`, `utm_campaign=launch-check`. Nothing else is added. |
| Order recorder | Watches only; the tester places the order. Refuses URLs with affiliate parameters unless confirmed or allowed by the rules file. Emails, names, addresses, phone numbers and payment tokens are replaced with `•••` before the record is written. |
| Speed test | Lighthouse loads each tested page in a local Chromium, like a visit. With a PageSpeed key, or when falling back to keyless PageSpeed, Google fetches the page URL. Beacons are not held back on a speed visit, so affiliate and click-id parameters (`affid`, `sub1`–`sub10`, `oid`, transaction and click ids) are removed from the URL first. |
| Output | A local folder. Nothing is uploaded; there is no telemetry. The campaign API key is always masked; tracking ids are masked with `--mask-ids`. |

What it cannot protect against: a page that sends data from its own server,
or a tag that reports through an endpoint not in the list above. Use your
tag manager's preview mode when that matters.
