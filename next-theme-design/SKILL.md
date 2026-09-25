---
name: next-theme-design
version: 0.1.0
description: |
  Prepare a live website, or its HTML, as a validated design handoff package
  for NEXT storefront work. Use when the design source is a rendered web
  page, such as a merchant landing page to rebuild as a Spark storefront:
  capture it at desktop, tablet, and mobile widths, map its sections, and
  record geometry, copy decisions, observed styles, assets, behaviors, and
  commerce divergences with evidence for next-theme-dev. Not for Figma
  sources (use next-theme-figma) and not for importing a funnel into Campaign
  Page Kit. It stops at the handoff: it does not build, upload, or publish a
  theme.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
---

# NEXT Theme Design (live site)

> **Three linked theme skills.** `next-theme-figma`, `next-theme-design`, and
> `next-theme-dev` are installed and used together. `next-theme-figma` turns a
> Figma source into a handoff package and holds the Spark section roster and
> the copy lint that the other two use. `next-theme-design` turns a live site
> or its HTML into a handoff package and holds the in-page capture script.
> `next-theme-dev` validates the package and builds the theme from it, and
> holds the package format, scaffolder, validator, and geometry assertion.
> Work always runs from one source skill into `next-theme-dev`. If a sibling
> this skill needs is not installed, stop and name it with its install command
> (`./skills.sh install <target> <skill>`); never re-derive what it provides.

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; install this skill, `next-theme-dev`, and `next-theme-figma` into the same target. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-theme-design` (and the same for `next-theme-dev` and `next-theme-figma`); add `-a <agent>` for a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |
| **Version check** | From a source checkout, run `./skills.sh status all next-theme-design`. |

## What It Produces

A package in the live-site handoff format that `next-theme-dev` owns
(`references/design-handoff-format.md` in that skill). The package preserves
enough evidence for a builder to reproduce the page's design and behavior
without visiting the source again, and it separates what the page *shows* from
what is true or configured in NEXT.

`<design-dir>`, `<dev-dir>`, and `<figma-dir>` below are the installed
directories of this skill, `next-theme-dev`, and `next-theme-figma`: siblings
in one skills target (for example `~/.claude/skills/`) or one checkout.

This skill stops at the handoff. It does not create, upload, or publish a
theme, configure offers, coupons, or shipping, place orders, or run `ntk`.
Those belong to `next-theme-dev` and the store operator's normal authority.

## Before You Start

```bash
python3 <design-dir>/scripts/design-package.py check-siblings
```

If it names a missing skill, stop and give the operator the install command it
prints. Do not work around a missing sibling.

## Workflow

Load `references/capture.md` before the first capture: it lists the capture
script's options and output, and shows how to run it from common browser
tools.

### 1. Intake

Record, before capturing:

- Source URL(s), or supplied HTML. Supplied HTML must be rendered with its
  assets (serve it locally) before it can produce a package ready to build.
  If it cannot be rendered, produce an `intake-only` package whose gaps say
  what is missing.
- Target store, theme, theme repo, target routes, and the preview URL if one
  exists.
- The source owner and whether reuse is allowed, as the **operator's
  statement**. When the operator states nothing, the run is reference-only:
  keep source URLs and screenshots, and download no source media. Declaring
  reuse lifts that default. The skill cannot enforce rights and does not try.
- The capture date. Live pages change.

Create the package with the sibling scaffolder:

```bash
python3 <dev-dir>/scripts/design-handoff.py new --out <package> --project <slug> \
  --source-url <url> --routes / --store <store> --repo <theme repo> \
  --theme-family spark --owner "<owner as stated, or not stated>" --reuse not-stated
```

### 2. Capture

Run the capture script, `<design-dir>/scripts/capture-page.js`, with any
browser tool that can set an exact viewport width, height, and device pixel
ratio, evaluate a script file in the page, and save an **unscaled** PNG
screenshot. Some tools downscale tall full-page screenshots; `record` refuses a
screenshot whose width is not viewport width times device pixel ratio.

For every route, capture at **1440×900, 768×1024, and 390×844**. A width you
cannot capture becomes a `missing-width` gap with its reason, never an
invented layout.

1. **Inventory.** Run the capture script once at 1440 with no targets. Its
   `inventory` lists the page's candidate sections with boxes, headings, and
   selector hints.
2. **Targets.** Write a targets file: one `{ "key", "selector" }` per section
   (`hero`) and per element the build must reproduce (`hero::heading`,
   `hero::cta`, `hero::product`). Each selector must match exactly one node.
3. **Static captures.** At each width: load the page, wait for it to settle,
   set `preload_scroll: true` so lazy media loads, run the script with
   `state: "static"`, save its output, then take the full-page screenshot
   without scrolling or resizing. The script finishes finite animations and
   holds infinite ones at their start, so geometry and screenshot share one
   known state.
4. **Motion.** For animation, run a separate capture with `state: "motion"`
   and `sample_count` while it runs. Its `animations` carry timing and
   keyframes (including per-element delays).
5. **Interaction.** For sticky elements, hover states, or a scrolled layout,
   scroll or hover first, then capture with `state: "interaction"` and a
   `state_detail` that says what you did.
6. **Media frames.** For text or claims shown inside a video, capture with
   `state: "media-frame"` and `media_seek` at each time you need, with a
   screenshot that shows the frame.

File each capture into the package:

```bash
python3 <design-dir>/scripts/design-package.py record --package <package> \
  --raw <raw.json> --screenshot <shot.png> --screenshot-kind full-page --tool "<browser tool>"
```

Stay read-only toward the source: never submit a form, add to cart in a way
that creates an order, complete checkout, or evade a block. A blocked or
missing view is a gap.

### 3. Map

Fill `routes.json` and `sections.json`: section order, `source_selector`,
`capture_refs`, and a classification: `semantic-rebuild`, `reusable-media`,
`composed-asset`, `background-asset`, `live-commerce-component`, or
`platform-app-hook`. Keep text, links, prices, FAQs, and controls live in the
theme; a page built from screenshots of sections is not an acceptable result.

For a Spark target, pick each section's `spark_section` from
`<figma-dir>/references/spark-section-roster.md` and record `roster_status`
(`shipped`, `unshipped`, `chrome`, or `unmapped` when nothing fits). Every
section needs it; there is no Figma frame name to infer it from later.

### 4. Extract Evidence

The helper does the mechanical parts from the capture records:

```bash
python3 <design-dir>/scripts/design-package.py geometry --package <package> --capture home-desktop-static
python3 <design-dir>/scripts/design-package.py draft-copy --package <package> --capture home-desktop-static
python3 <design-dir>/scripts/design-package.py draft-styles --package <package> \
  --capture home-desktop-static --keys hero::heading,hero::cta
```

- **Geometry.** Run `geometry` once per static capture (each width). Section
  boxes stay page-relative and element boxes become relative to their
  section. Review the build hooks (`selector`), and add `assert` or
  `align_anchor` where the source box is not the box the build must match.
- **Copy.** `draft-copy` records each captured string as `source_text` with
  decision `unresolved`. Set a role and a decision for each:

  | Decision | `text` | Needs |
  |---|---|---|
  | `reuse` | exactly `source_text` | `reuse_basis`; a commerce fact also needs `next_evidence` from NEXT |
  | `replace` | the approved replacement | `approved_by` or `next_source` |
  | `omit` | `""` | nothing; surfaced before build |
  | `unresolved` | `""` | blocks the section until decided |

  Source copy is captured or recorded as a gap; never type it in.
- **Observed styles.** Computed values, not design variables. Draft the ones
  the build needs (fonts, sizes, colors, spacing).
- **Assets.** One entry per media item: `reference-only` with a treatment
  (`omit`, `rebuild-semantic`, or `replace-with-store-media`), `downloaded`
  (only when reuse is declared, into the package's `media/`), or
  `replacement-needed` with the missing `target_asset`. Never carry source
  trackers or vendor checkout scripts into a package.
- **Behaviors.** Record responsive order, sticky elements, motion (from the
  motion capture's `animations`), media (duration, autoplay, loop, muted),
  CTA destinations (from `links`), and accessible text, each with the
  captures that show it. Mark each `extracted` or `observed`; an observed
  behavior records its `method`.
- **Inferred entries.** Geometry and styles may be inferred only with a
  reason and a line in `notes.md` naming the entry. Copy is never inferred.

### 5. Separate Commerce Truth

Capture how the source presents its offer and checkout path, then record
which facts need NEXT catalog, Site Offer, coupon, shipping, or checkout
configuration. Prices, product identity, bundles, savings, shipping promises,
coupons, and checkout URLs come from NEXT, never from the source HTML.

- Each such fact becomes a divergence-ledger entry (with the captures that
  show it) or a gap. Its copy is `omit`, `unresolved`, or an approved
  `replace`; a `reuse` needs NEXT evidence.
- Claims that need evidence (performance, safety, certification, ratings,
  savings percentages, claims inside video frames) become `claim` entries.
  Record the claim's basis as unresolved rather than deciding it.
- Label where each NEXT-side fact comes from in `target_evidence.origin`:
  `fresh-read-only` (read from NEXT during this run, read-only), or
  `prior-input` (an earlier audit or the operator's word). Prior input gives
  context; it does not prove current configuration.

### 6. Validate and Hand Off

```bash
python3 <design-dir>/scripts/design-package.py coverage --package <package>
python3 <dev-dir>/scripts/design-handoff.py validate <package> --report <package>/validation-report.json
```

Fix every `STRUCTURE` error. `READINESS` lists blocked sections; a valid
package can have them. Write `notes.md`: evidence origin for each finding
(fresh extraction, manual observation, or prior input), inferred entries,
blocked sections with next actions, and the limits of automated capture on
this page. Then hand the package to `next-theme-dev`, whose live-site entry
contract reads it.

## Limits of Automated Capture

Record these in `notes.md` when they apply, rather than guessing:

- Text drawn inside video frames or canvas is not readable by the script;
  read it from a media-frame screenshot and mark it observed.
- Motion driven by JavaScript (scroll libraries, canvas) may not appear in
  `animations`; describe it from observation or record a gap.
- Cross-origin iframes (hosted video players, review widgets) are measured as
  boxes only.
- Stylesheets from another origin cannot be read for keyframe rules; running
  animations still report their keyframes.
- Headless browsers may block autoplay by default; say how the capture ran.
- Pages that serve different markup by user agent or location may differ
  from what a shopper's phone sees.
