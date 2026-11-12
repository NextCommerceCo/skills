# Campaign Launch Check

A **before-you-launch check** for a campaign: the story or landing page, the
checkout, the upsells and the thank-you page. You give your assistant the
web address of the first page. It follows the funnel to the end and tells
you what is broken, what looks risky, and what a person still has to look
at. It can also check a storefront, a pull request's preview, or a page
against its Figma design.

It runs on your computer and needs no account or key. It does not change
your campaign, does not place orders by itself, and does not upload
anything. You get a folder with a readable report, screenshots, and a short
summary you can paste into a ticket or a message.

## What It Checks

| Area | Examples |
|---|---|
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
plan. The report lists those as a checklist, and your assistant walks you
through them.

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
| "Run a launch check on https://offer.example.com/" | Checks the whole funnel and writes the report |
| "Launch check this PR's preview" | Reviews the changed code and checks the preview pages |
| "Test the storefront at https://shop.example.com" | Checks menus, footer, search and login |
| "Compare this page with the Figma design" | Puts screenshots next to the design and compares fonts and colours |
| "I want to place a test order" | Opens a browser window; you place one order with the test card and the assistant records what happened |

## About Test Orders

A test order is a real order record on your store, marked as a test. With
the platform's test card there is no real charge and nothing ships, but the
store records a customer and sends confirmation emails. The assistant asks
before every test order, and you place it yourself.

> [!IMPORTANT]
> Campaigns built with Campaigns OS have their own QA command that places
> test orders automatically. When your campaign has one, the assistant uses
> it for the order and adds its result to this report.

## What You Get

| File | What it is |
|---|---|
| `report.md` | The readable report: result, problems first, screenshots, your checklist |
| `results.json` | The same results for tools and spreadsheets |
| `screenshots/` | Desktop, phone and zoomed views of each page |

The result is one of: **Ready**, **Ready with exceptions** (no blockers, but
things to look at), **Blocked** (something must be fixed first) or
**Incomplete** (the checklist still has open items).
