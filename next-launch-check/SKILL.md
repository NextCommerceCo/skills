---
name: next-launch-check
version: 0.1.0
description: |
  QA a Next Commerce campaign before launch: asks what is being tested
  (presell, landing, checkout, upsell, full funnel or storefront), then
  fills the full QA checklist for those page types. Automated checks decide
  what a script can judge (routes, SDK, tracking on every page, legal and
  dead links, copy, content toggles, phone and zoom layout, locale, form
  errors, a speed test on mobile and desktop) and attach screenshots; the
  rest is walked through with the tester. Places typed test-card orders
  (standard and 3-D Secure, per package, accepting or declining upsells)
  after the tester agrees, runs Campaigns OS QA on Campaigns OS projects,
  reviews a pull request and compares a page with Figma.
  Writes a local report; uploads nothing.

  Use when: "test this funnel", "launch check", "QA this campaign", "check
  the checkout before launch", "check this deploy preview", "review this PR's
  funnel", "test the storefront", "compare the page with the Figma", or when
  an agency or merchant built a campaign and wants it checked before traffic.
allowed-tools:
  - Bash
  - Read
  - Write
  - AskUserQuestion
  - TodoWrite
---

# /next-launch-check: Campaign Launch Check

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-launch-check` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Runs the full QA checklist on a campaign before it gets traffic, from its URL
alone. It asks what is being tested, automates every checklist item a script
can judge, attaches screenshots for the ones a person must confirm, and walks
the tester through the rest. It runs on the tester's machine, needs no
account, key or Campaign Map, and writes a local report folder. It is built
for agencies and merchants who build their own campaigns.

It **never** edits campaign code, never places an order by itself, never
sends a purchase event, and never uploads anything. Analytics and ad beacons
are answered locally during browser runs, so test visits do not reach
vendors.

**Where it sits.** Campaigns built with Campaigns OS have a spec-aware QA
command, `campaigns-os qa run` (the `next-campaigns-qa` skill). That command
is the authority for what it proves: routes against the Campaign Map, policy
links, parameters, runtime and typed-card test orders. This skill covers
what it leaves out (links and calls to action, copy, tracking consistency
across pages, media weight, meta tags, phone and zoom layout, locale, form
errors, storefronts, Figma, pull requests) and works when there is no Map at
all. When both run on the same build, fold the Campaigns OS verdict in with
`--campaigns-os-verdict` (Phase 3).

## Phase 0: Set Up

**The report always comes from the script.** Never write your own checklist, never mark
an item as checked, and never tell the tester something "needs a manual pass" because
you did not run a lane. If a lane cannot run (Playwright, Lighthouse or Campaigns OS
missing), install what is missing after asking, or say plainly which lane did not run
and why, as the report's "Ran:" line shows.

Find the script next to this file and check the machine:

```bash
S="<directory containing this SKILL.md>/scripts/launch-check.js"
node "$S" env
```

- **Node 18 or newer** is required. The script has no npm dependencies.
- **Playwright** with Chromium runs the browser lane (on by default), design
  mode and the order recorder; the speed test uses the same Chromium. `env`
  prints where it was found, or `null`. Without it about a third of the
  checklist falls back to manual, so recommend installing it, and ask before
  installing anything. Then either:
  - in a Campaigns OS campaign folder: `npx campaigns-os qa install-browser`
    (the script finds that copy when run from the folder), or
  - anywhere else: `npm install -g playwright && npx playwright install chromium`.
- **`campaigns_os`** in the `env` output tells you whether Phase 4 can
  delegate the test order.
- Run the commands from a working folder that is **not** inside a git
  repository you will commit, or add `next-launch-check-output/` to its
  `.gitignore`. Reports hold page URLs and screenshots.

## Phase 1: Ask What Is Being Tested

Always ask first, with AskUserQuestion when it is available:

**"What are you testing?"**

| Answer | `--scope` | What runs |
|---|---|---|
| Presell page | `presell` | That page, plus a click-through to the landing page and checkout for parameter carry-through |
| Landing page | `landing` | That page, plus a click-through to the checkout |
| Checkout | `checkout` | That page and where its route tag leads |
| Upsell | `upsell` | That page and where accept and decline lead |
| Full funnel | `funnel` | Every page from the entry URL: presell, landing, checkout, upsells, receipt |
| Storefront | `storefront` | The store's home page and its header and footer pages |
| Speed test only | `speed` command | Mobile and desktop speed for the URL (and any `--page`), nothing else |

AskUserQuestion takes 4 options: offer **Full funnel**, **Checkout**, **Upsell** and
**Presell or landing page**, then ask which of the two if they pick the last one. A
storefront, a speed test only, a design comparison or a pull request come through
"Other".

**Campaigns OS projects.** If the campaign was built with Campaigns OS, the launch
check also runs `campaigns-os qa` (the engine behind `next-campaigns-qa`) and adds its
results to the same checklist: template commerce structure (payment fields, wallets,
bundle selector, bumps), template residue, price parity with the Campaigns App, page
binding to the Campaign Map, theme and polish gates, analytics correctness and, when
asked, typed-card test orders. It needs one of these, so ask if `env` found none:

| They have | Pass |
|---|---|
| A Build Packet (`campaign-runtime.build.json`) | nothing: run from the campaign folder and it is found, or `--packet <file>` |
| A built Page Kit folder | `--site <folder> --family <template family>` (e.g. `olympus`) `[--slug <route>]` |
| A saved Map | `--map-id <id> --spec <exported CampaignSpec.json>` |

For `--site` and `--map-id`, also pass `--campaigns-os-base-url <deployed campaign root>`
when the URL being tested is not the root (for example the root is `…/olympus/` and you
test `…/olympus/landing/`). It runs locally and never publishes its verdict to the QA
portal (`--no-post-verdict`). It uses the project's own `campaigns-os` when installed,
otherwise `npx` downloads the pinned version once. It needs its own Playwright Chromium:
`npx campaigns-os qa install-browser`. `--no-campaigns-os` skips it.

Then ask only for what is still missing:

| Input | Notes |
|---|---|
| URL | The page being tested (for a full funnel, the first page). A deploy preview is fine. |
| Other page URLs | Only if the crawl cannot reach them; pass `--page` in funnel order. |
| Their QA rules | Optional: a JSON rules file or their own Markdown checklist (`references/rules-file.md`). Its items join the checklist. |

If the URL carries affiliate parameters (`affid`, `sub1`…, `oid`, click ids), say so
now: the checks are safe with them, but Phase 4 will refuse them unless the tester
confirms they are agreed test values.

## Phase 2: Run the Check

```bash
node "$S" check "<url>" --scope <presell|landing|checkout|upsell|funnel|storefront> [--page "<url>"]... [--rules <file>] [--mask-ids] [Campaigns OS flags]
node "$S" speed "<url>" [--page "<url>"]...
node "$S" design "<page-url>" --figma-css figma.css [--frame desktop.png] [--frame mobile.png]
```

- The browser lane and the speed test run by default. A single page takes about 2
  minutes; a full funnel about 6. Tell the tester before you start.
- **Speed test**: mobile and desktop for the page (in a full funnel, the entry page,
  the checkout and the first upsell). It uses the tester's `PAGESPEED_API_KEY` if set,
  otherwise Lighthouse on this machine (npx downloads it once), otherwise keyless
  PageSpeed. If none answers, the report links to pagespeed.web.dev to run it by hand.
  Never ask for a key in the chat. `--no-speed` skips it.
- `--no-browser` skips the browser lane; many checklist items then fall to manual.
- `--mask-ids` masks tracking ids, for reports pasted into tickets.
- `--param key=value` adds a parameter to the carry-through test.
- Exit codes: `0` done, `4` blocked, `2` usage error, `3` refused for safety.

## The Report

Every run writes one folder with:

| File | For | What it holds |
|---|---|---|
| `report.html` | People: the tester, the merchant, the client | Result, what to fix, what is left to check, speed, pages, the full checklist with screenshots. Opens in any browser; prints cleanly |
| `report.pdf` | Sharing | The same report as a PDF (written when Playwright is installed; `--no-pdf` skips it) |
| `report.md` | Developers and AI agents | The fix list with item names, page URLs, evidence, screenshots and the likely fix; the open checks; the re-run command |
| `results.json` | Tools | Everything, machine-readable |

Point the tester to `report.html` (or the PDF) and a developer or a fixing agent to
`report.md`. Checklist items have readable names (`toggle-timer`,
`standard-card-order`, `legal-privacy`…); people see the title, numbered within its
section; use the names in `answers.json`.

## Phase 3: Walk the Checklist

The report is built around the QA checklist (`references/checklist.json`): every item
from the funnel, presell and storefront checklists, applied only to the page types
being tested. Each item is one of:

| Mark | Meaning | What you do |
|---|---|---|
| **PASS** | Decided by an automated check | Mention briefly |
| **FAIL** / **WARN** | Decided by an automated check, with the evidence | Explain what is wrong, on which page, and the likely fix (`references/families.md`) |
| **TODO** (look at the evidence) | The check ran and attached evidence, such as before and after screenshots for `?timer=n` | Open the screenshots, describe what you see, ask the tester to confirm |
| **TODO** (check by hand) | No script can judge it, such as prices against the offer plan or a real device | Ask the tester |

Report the result first (`blocked`, `ready_with_exceptions`, `ready`, or
`incomplete` while items are open), then the speed table, then the FAILs and WARNs.
Then go through the open items **one section at a time**, in report order. Ask about
each section's items together: "Design & branding, 9 items: …". Never answer for
the tester. Accept pass, fail (with a note), warn, or n/a. Write their answers and
close the run:

```bash
cat > answers.json <<'JSON'
{ "product-name-logo": "pass", "prices-match-plan": { "answer": "fail", "note": "3-pack shows $89, plan says $79" }, "address-autocomplete": "na" }
JSON
node "$S" finalize "<report-folder>" --answers answers.json
```

You can call `finalize` after each section; answers accumulate. Finish with the
**Summary to paste** block from the report.

If a Campaigns OS verdict already exists for the same build (a `qa-output/…/<run>.json`
file), `--campaigns-os-verdict <file>` folds it in without running it again. Either way,
our route and order rows defer to the families Campaigns OS proved, and its items
appear in a "Campaigns OS (spec-aware)" section.

Do not edit their code unless they ask. Do not edit `results.json` or the report files by
hand; re-run or `finalize`.

## Phase 4: Test Orders (Checkout, Upsell or Full Funnel)

The card, upsell and order-data items (`standard-card-order`, `3ds-card-order`, `upsell-accept-decline`, `utm-carry-through`, `purchase-event-once`, `order-*`) need
real test orders. Never skip this phase silently and never decide on the tester's
behalf that a host is "not a test host": ask. Explain once, then ask with
AskUserQuestion:

> A test order is a real order on the store, marked as a test: the platform's test
> card makes no charge and nothing ships, but the store records a customer and sends
> confirmation emails (to `qa+launchcheck@example.com` unless you give another).
> Which orders should I place?

Offer: **Standard card, accept upsells** (recommended minimum); **Standard + 3-D
Secure** (`standard:accept,3ds:decline`); **Every package** (one order per package
option, e.g. `standard:accept@1,standard:decline@2,standard:accept@3`); **Rehearsal
only** (`--dry-run`: fills everything and stops before submit).

- **Campaigns OS campaign**: its typed-card order proof is canonical. Run the check again
  with the same Campaigns OS flags plus `--campaigns-os-orders common` (or `full`).
- **Any other funnel**: the script places the orders itself, into the same report:

  ```bash
  node "$S" order "<checkout-url>" --into "<report-folder>" --orders <plans> [--rules <file>] [--confirm-test-params]
  ```

  Plans are `card[:accept|decline|accept-decline|decline-accept][@package]`, cards
  `standard` (`6011 1111 1111 1117`) and `3ds` (`6011 0009 9013 9424`). For each plan
  it opens the checkout with test UTMs, picks the package, fills a US test customer
  (pass `--country`, `--address1`, `--city`, `--province`, `--postal` for a store that
  does not ship to the US), types the card into the hosted card fields, submits once,
  accepts or declines each upsell, and stops at the receipt. It records the order
  request (shipping method, attribution, language), upsell calls, dataLayer events and
  the parameters on every page. Run `--dry-run` first on an unfamiliar checkout.
- **The tester prefers to type it themselves**: `node "$S" record "<checkout-url>" --into
  "<report-folder>"` opens a window and only watches.

Rules:

- Ask before every `order` run, with the exact plans. One order per plan; the script
  never resubmits once an order call has gone out, and refuses more than 4 orders
  unless `--max-orders` is raised after the tester agrees to that number.
- If an order fails, report the recorded steps and the screenshot; do not run it again
  without asking.
- The script refuses (exit 3) URLs with affiliate parameters. Re-run with
  `--confirm-test-params` only after the tester confirms they are agreed test values,
  or pass a rules file whose `allowedParams` lists them.
- A 3-D Secure challenge the script cannot complete is reported; offer `--headed` so
  the tester can finish it, or `record`.
- Afterwards: remind them to check each order in the store's orders page (order-every-package, AP-*)
  and to reject test conversions in their affiliate platform (reject-test-conversion).

## PR Mode

For a pull request on a campaign repo:

1. Get the diff: `gh pr diff <number> > pr.diff` (or `git diff <base>...<head> > pr.diff`).
2. Find the deploy preview URL: the PR's checks or the hosting bot's comment.
   If there is none, ask for it.
3. Run the funnel check on the preview with the diff:
   `node "$S" check "<preview-url>" --scope <what the PR changes> --diff pr.diff [--page …]`.
   The `pr-review` rows list file and line for typed prices, direct cart or
   order API calls, secrets, debug leftovers and placeholders.
4. Post the summary as a PR comment **only** if the tester says yes, and show
   them the text first.

## Never

- Never place an order without the tester's yes for those exact plans, and never type
  card details or submit a checkout outside the `order` command. Never retry an order.
- Never write the report or the checklist yourself; it comes from the script.
- Never push a purchase or conversion event into a page, and never open an
  upsell or receipt page with an order reference you did not just create.
- Never add affiliate parameters to a URL the tester did not give you.
- Never ask for an API key, token or password in the chat.
- Never upload the report or screenshots anywhere, and never post to a
  ticket, chat or PR without an explicit yes for that post.
- Never edit `results.json` or the report files by hand; re-run or `finalize`.

## Reference Files

| File | What it holds |
|---|---|
| `references/checklist.json` | The QA checklist: every item, the page types it applies to, and the automated checks that decide or support it |
| `references/families.md` | Every automated check: what it checks, severity, and what to do when it fails |
| `references/rules-file.md` | The rules file format: required tracking, by-design items, allowed test parameters, extra manual items |
| `references/safety.md` | What the script blocks, holds back and redacts, and why |
