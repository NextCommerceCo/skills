# Campaign Launch Check

A **QA check for a campaign before it goes live**. Your assistant asks what you
are testing — a presell page, a landing page, a checkout, an upsell, the full
funnel, or a storefront — and runs the full QA checklist for that kind of
page. It checks everything a computer can check by itself, including a speed
test on mobile and desktop. It shows you screenshots for the things you need
to confirm with your own eyes, and asks you about the rest one section at a
time. It can also check a pull request's preview or compare a page with its
Figma design.

It runs on your computer and needs no account or key. It does not change
your campaign, does not place orders by itself, and does not upload
anything. You get a folder with a readable report, screenshots, and a short
summary you can paste into a ticket or a message.

## What It Checks

| Area | Examples |
|---|---|
| Speed | A speed test on mobile and desktop, with scores and load times |
| Pages and routes | Every page loads; the checkout and upsells lead to the right next page |
| Checkout setup | The checkout script is present, pinned and the same on every page |
| Tracking | The same tag manager, analytics and pixel ids on every page; which tags actually load |
| Links | Terms, privacy, returns, shipping and contact links work and open in a new tab; no dead or placeholder links |
| Copy and images | No "lorem ipsum" or TODOs; images that are too heavy for phones |
| Phones | Nothing sticks out sideways on a phone or when zoomed to 125% or 150% |
| Ad parameters | Tracking parameters survive from the first page to the checkout |
| Checkout form | An empty form or a bad email is stopped with a clear error, without sending an order |
| Your rules | Your own checklist, added to the report |

Some things only a person can judge, such as whether prices match the offer
plan. Those stay on the checklist, and your assistant walks you through them.
Only the items that fit what you are testing are included: a checkout gets the
card and form checks, a presell page gets the content and parameter checks.

## What You Need

- **The web address** of the first page of the funnel. A preview link is fine.
- **Node.js** on your computer (version 18 or newer).
- For the page-by-page browser checks, the free **Playwright** browser
  tool. Your assistant will offer to install it.
- Optionally, **your own QA checklist** as a file, and for design checks the
  CSS copied from Figma and exported frame images.

> [!IMPORTANT]
> If your link contains affiliate values (for example `affid` or `sub1`), the
> check still runs safely, but a **test order** through a live affiliate value
> can count as a real conversion. Use only the test values your affiliate
> partner gave you.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your assistant in plain words:

| You say | What happens |
|---|---|
| "QA this checkout: https://offer.example.com/checkout/" | Runs the checkout checklist and the speed test on that page |
| "Run a launch check on the full funnel at https://offer.example.com/" | Checks every page from the first one to the thank-you page |
| "Launch check this PR's preview" | Reviews the changed code and checks the preview pages |
| "Test the storefront at https://shop.example.com" | Checks menus, footer, search and login |
| "Just run a speed test on https://offer.example.com/" | Mobile and desktop speed scores and load times, nothing else |
| "Compare this page with the Figma design" | Puts screenshots next to the design and compares fonts and colours |
| "Place test orders" | After you agree, places test-card orders itself (standard and 3-D Secure, each package you choose, accepting or declining upsells) and checks every page through the thank-you page |
| "I want to place the test order myself" | Opens a browser window; you place one order with the test card and the assistant records what happened |

## Campaigns OS Projects

If your campaign was built with Campaigns OS, the check also runs Campaigns OS's own QA
and puts its results on the same checklist: the checkout's payment fields and buttons,
leftover template styles and images, prices against your campaign settings, analytics
events, and (if you ask) automatic test orders. Your assistant tells you what it needs
from your project.

## About Test Orders

A test order is a real order record on your store, marked as a test. With
the platform's test card there is no real charge and nothing ships, but the
store records a customer and sends confirmation emails. The assistant asks
before every test order and tells you exactly which orders it will place. It
can also do a rehearsal that fills the checkout and stops before paying.

> [!IMPORTANT]
> Campaigns built with Campaigns OS have their own QA command that places
> test orders automatically. When your campaign has one, the assistant uses
> it for the order and adds its result to this report.

## What You Get

| File | What it is |
|---|---|
| `report.md` | The readable report: result, speed table, the checklist with every item marked pass, fail or to do, and screenshots |
| `results.json` | The same results for tools and spreadsheets |
| `screenshots/` | Desktop, phone and zoomed views of each page |

The result is one of: **Ready**, **Ready with exceptions** (no blockers, but
things to look at), **Blocked** (something must be fixed first) or
**Incomplete** (the checklist still has open items).
