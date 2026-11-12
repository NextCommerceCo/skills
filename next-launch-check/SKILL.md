---
name: next-launch-check
version: 0.1.0
description: |
  Pre-launch check for a Next Commerce campaign funnel, storefront or page,
  starting from its URL. Crawls the funnel, checks routes, SDK, tracking,
  legal links, dead and placeholder links, copy, media weight and meta tags;
  optionally renders every page in Chromium (runtime errors, tags that load,
  phone and zoom layout, parameter carry-through, content toggles, locale,
  checkout form errors), records one test order the tester places, reviews
  a pull request diff, and compares a page with its Figma design. Writes a
  local report; uploads nothing.

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

Checks a campaign before it gets traffic, from its URL alone. It runs on the
tester's machine, needs no account, key or Campaign Map, and writes a local
report folder. It is built for agencies and merchants who build their own
campaigns.

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

Find the script next to this file and check the machine:

```bash
S="<directory containing this SKILL.md>/scripts/launch-check.js"
node "$S" env
```

- **Node 18 or newer** is required. The script has no npm dependencies.
- **Playwright** is needed for the browser lane, design mode and the order
  recorder. `env` prints where it was found, or `null`. If it is missing and
  the tester wants those parts, ask before installing anything. Then either:
  - in a Campaigns OS campaign folder: `npx campaigns-os qa install-browser`
    (the script finds that copy when run from the folder), or
  - anywhere else: `npm install -g playwright && npx playwright install chromium`.
- **`campaigns_os`** in the `env` output tells you whether Phase 3 can
  delegate the test order.
- Run the commands from a working folder that is **not** inside a git
  repository you will commit, or add `next-launch-check-output/` to its
  `.gitignore`. Reports hold page URLs and screenshots.

## Phase 1: Gather Inputs

Ask only for what is missing. Use AskUserQuestion when the answer is a choice.

| Input | Needed for | Notes |
|---|---|---|
| Entry URL | everything | Presell, landing or checkout; a deploy preview is fine. |
| Mode | everything | `funnel` (default), `storefront`, `design`, or PR (below). |
| Page URLs | funnels the crawl cannot reach | The crawl follows route tags and call-to-action links. Upsells reached only after an order are found from the checkout's route tags; pass `--page` for anything else, in funnel order. |
| Their QA rules | optional | A JSON rules file, or their Markdown checklist. See `references/rules-file.md`. |
| Browser lane | recommended | Adds roughly a minute per page. |
| Test order | optional | Phase 4. Ask separately; never assume. |

If the entry URL already carries affiliate parameters (`affid`, `sub1`…,
`oid`, `transaction_id`, click ids), say so now: Phase 1–3 are safe with
them (beacons are held back), but Phase 4 will refuse them unless the tester
confirms they are agreed test values.

## Phase 2: Run the Check

```bash
node "$S" funnel "<entry-url>" --browser [--page "<url>"]... [--rules <file>] [--pagespeed] [--mask-ids]
```

Other modes:

```bash
node "$S" storefront "<home-url>" --browser
node "$S" design "<page-url>" --figma-css figma.css [--frame desktop.png] [--frame mobile.png]
```

- `--pagespeed` asks Google PageSpeed Insights for up to three pages. It
  works without a key until the shared quota runs out; the tester can set
  their own `PAGESPEED_API_KEY` in the environment. Never ask them to paste
  a key into the chat.
- `--mask-ids` masks tracking ids in the report. Use it when the report will
  be pasted into a ticket or sent outside the team.
- `--param key=value` adds a parameter to the carry-through test (for
  example an agreed affiliate test value).
- Exit codes: `0` done (ready, ready with exceptions, or incomplete), `4`
  blocked, `2` usage error, `3` refused for safety.

The script prints the result line and the report path. Read `report.md`
(and `results.json` when you need detail).

## Phase 3: Explain the Result

Report in this order, in plain language:

1. **The result**: `blocked`, `ready_with_exceptions`, `ready`, or
   `incomplete` (automated checks done, manual items open). Say which lanes
   ran. Never call a lane passed when it was skipped or not requested.
2. **Blockers**, each with the page, what is wrong, and the likely fix. Use
   `references/families.md` for the fix wording.
3. **Failures and warnings**, grouped by family, most important first.
4. **Check by hand** rows: open the screenshots they name and describe what
   you see, then let the tester decide.
5. **By design** rows from their rules file, briefly.

If a Campaigns OS verdict exists for the same build (a `qa-output/…/<run>.json`
file in the campaign folder), run again with
`--campaigns-os-verdict <file>`. Rows in the families it covers are then
marked deferred, and its disposition counts toward the result.

Do not edit their code unless they ask. If they ask, fix in their repo and
run the check again; do not edit `results.json` or `report.md` by hand.

## Phase 4: Test Order (Optional, Gated)

Ask once, and only after Phase 3: "Do you want to place one test order now?"
A test order is a real order record on the store, marked as a test, with no
real charge and no fulfillment when the platform's test card is used. It
creates a customer record and sends confirmation emails to the address
typed in.

- **Campaigns OS campaign** (`env` shows a package or a Build Packet): use
  its typed-card order proof, which is canonical:
  `npx campaigns-os qa run --packet campaign-runtime.build.json --base-url <url> --browser --test-order common`.
  Then fold the verdict in as in Phase 3.
- **Otherwise**, record an order the tester places by hand:

  ```bash
  node "$S" record "<checkout-url>" [--rules <file>] [--confirm-test-params]
  ```

  A browser window opens. The tester fills the checkout with the platform's
  public test card (`6011 1111 1111 1117`, any future expiry, CVV `123`;
  3-D Secure: `6011 0009 9013 9424`), takes or declines each upsell, and
  stops at the receipt. The script only watches: it records the order call,
  upsell calls, dataLayer events and the parameters on each page, then
  closes after the receipt. Order data is redacted before it is written.

Rules:

- One order per yes. Never re-run `record` to "try again" after an order was
  created; report what happened and let the tester decide.
- The script refuses (exit 3) when the URL carries affiliate parameters.
  Re-run with `--confirm-test-params` only after the tester confirms they are
  the agreed test values for this offer, or pass a rules file whose
  `allowedParams` lists them. A live affiliate value can record a real
  conversion and payout.
- Afterwards, remind them to reject the test conversion in their affiliate
  platform if they came through an affiliate test link (manual item F-18).

## Phase 5: Manual Checklist

The report ends with the items no script can judge (prices against the offer
plan, product and brand details, devices, tag manager preview…). Walk the
open items with the tester in small groups. Do not answer for them. Write
their answers and close the run:

```bash
cat > answers.json <<'JSON'
{ "F-01": "pass", "F-05": { "answer": "fail", "note": "3-pack shows $89, plan says $79" }, "F-12": "na" }
JSON
node "$S" finalize "<report-folder>" --answers answers.json
```

Then give them the **Summary to paste** block from the report.

## PR Mode

For a pull request on a campaign repo:

1. Get the diff: `gh pr diff <number> > pr.diff` (or `git diff <base>...<head> > pr.diff`).
2. Find the deploy preview URL: the PR's checks or the hosting bot's comment.
   If there is none, ask for it.
3. Run the funnel check on the preview with the diff:
   `node "$S" funnel "<preview-url>" --browser --diff pr.diff [--page …]`.
   The `pr-review` rows list file and line for typed prices, direct cart or
   order API calls, secrets, debug leftovers and placeholders.
4. Post the summary as a PR comment **only** if the tester says yes, and show
   them the text first.

## Never

- Never place, submit or retry an order yourself. Phase 4 delegates or records.
- Never push a purchase or conversion event into a page, and never open an
  upsell or receipt page with an order reference you did not just create.
- Never add affiliate parameters to a URL the tester did not give you.
- Never ask for an API key, token or password in the chat.
- Never upload the report or screenshots anywhere, and never post to a
  ticket, chat or PR without an explicit yes for that post.
- Never edit `results.json` or `report.md` by hand; re-run or `finalize`.

## Reference Files

| File | What it holds |
|---|---|
| `references/families.md` | Every check family: what it checks, lane, severity, and what to do when it fails |
| `references/manual-checklist.json` | The manual checklist items the report lists, by mode |
| `references/rules-file.md` | The rules file format: required tracking, by-design items, allowed test parameters, extra manual items |
| `references/safety.md` | What the script blocks, holds back and redacts, and why |
