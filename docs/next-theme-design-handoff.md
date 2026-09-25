# Handoff: `next-theme-design`

**Status:** brief for a focused implementation session, 2026-09-25. It proposes a plan; no skill or runtime change is part of this handoff. The decisions in "Proposed decisions" need the operator's confirmation before the session starts.

## Outcome

Create a source-preparation skill for NEXT storefront work that starts from a rendered website or its HTML and produces a validated design handoff for `next-theme-dev`. Use `next-theme-design` for live-site references and `next-theme-figma` for Figma sources. `next-theme-dev` remains the builder: DTL/Spark implementation, platform data and checkout integration, `ntk` upload, and preview QA.

The first use case is a merchant landing page rebuilt as a Spark storefront. The skill should preserve enough evidence that a builder can reproduce the design and behavior without repeatedly rediscovering the source page. It should distinguish what the page *shows* from what is true or configured in NEXT.

## Why this deserves a separate skill

- `next-theme-figma` already follows the useful pattern: inspect a design source, classify sections and assets, record divergences, and hand a validated package to `next-theme-dev`. Its schemas and validator deliberately require Figma metadata, text layers, and variables.
- `next-theme-dev` currently treats `implementation-handoff` as a Figma package: it requires `figma-handoff.json` and runs the Figma validator. A website package must not pose as a Figma package to pass this gate.
- Funnel-import tooling that extracts a merchant funnel for Campaign Page Kit has relevant safety and asset-capture ideas, but its output is a campaign, not a Spark/DTL design handoff.
- A recent landing-page rebuild showed the missing source-preparation work. The desktop and mobile geometry, the hero animation timing, a product video and the claims shown in its frames, offer-dependent shipping copy, and a small hero callout were each found in a separate pass.

## Proposed decisions

Confirm or revise these before the session starts. The session should not reopen them.

### D1. Package format: reuse the neutral files, replace the Figma-specific ones

Most of the Figma package already has source-neutral names. The live-site package keeps those files and their shapes wherever an existing tool reads them, and replaces only the Figma-specific pieces. No shared core is extracted in V1, and the Figma package, validator, schemas, and tests stay as they are.

| Purpose | Figma package | Live-site package |
|---|---|---|
| Entry point and target identity | `figma-handoff.json` | `design-handoff.json` with `source.kind: "live-site"`, source URLs, capture date, and reuse permission |
| Capture records | none; Figma node IDs serve this role | `captures.json` plus a `captures/` directory of screenshots |
| Routes, sections, assets, divergence ledger, viewport coverage | same file names | same file names and shapes; `figma_ref` becomes `source_ref`, and `figma_node_id` and `figma_names` become capture references |
| Geometry and copy | `geometry.json`, `copy.json` with Figma sources | same files and shapes, schema IDs `next-theme-design/geometry/v1` and `next-theme-design/copy/v1`, source `dom-capture` |
| Visual values | `tokens.json` (Figma variables) | `observed-styles.json` (computed values observed on the page, never labeled as variables) |
| Checklist and notes | `validation-checklist.md`, `notes.md` | same |

In `geometry.json`, `selector` keeps its current meaning: the hook the built theme must carry. The source page's own selector is recorded separately as `source_selector`. Boxes are page-relative CSS pixels at the named viewport width.

`assert-geometry.mjs` (in `next-theme-dev`) and `copy-lint.py` (in `next-theme-figma`) change only to accept the second schema ID. The Figma validator's source rules stay strict.

### D2. Capture script and provenance

The skill bundles a dependency-free in-page JavaScript capture script that any browser tool can run: Playwright, the Chrome DevTools protocol, or an agent's built-in browser. Like `next-theme-figma`, the skill does not bundle browser automation or depend on one browser tool. For each route and viewport, the script records:

- URL, viewport size, timestamp, and a hash of the rendered HTML, so a later run can tell the source page changed;
- element boxes, computed styles, and visible text for the sections and elements it is given;
- CSS animation properties and keyframes, and media dimensions, duration, and autoplay/loop/muted state.

The operator or agent saves a screenshot for each capture record. The validator then enforces provenance mechanically:

- every geometry and copy entry cites a `capture_id` that exists in `captures.json`;
- a value without a capture reference is accepted only when it carries `"basis": "inferred"` and a reason;
- every screenshot a capture record names exists in the package.

Motion driven by JavaScript is described from observation and marked as observed rather than extracted. A screen recording is optional supporting evidence, not a requirement.

### D3. Spark section roster

The roster stays in `next-theme-figma/references/spark-section-roster.json`. `next-theme-design` reads the sibling copy and lists `next-theme-figma` as a prerequisite. `next-theme-dev`'s fallback for a Spark section without `roster_status` runs `infer-section` on the Figma frame name, which a live-site package does not have. The live-site validator therefore requires `roster_status` on every section of a Spark-targeted package.

### D4. Fixtures and the pilot stay public-safe

This repository is public. Committed fixtures are synthetic. The first real merchant page is a private pilot: its package, screenshots, copy, and findings stay outside this repository. The operator supplies the pilot brief at session start. The PR body reports the pilot's results without naming the merchant or quoting its page.

## V1 scope

1. **Intake.** Record source URL(s), target store/theme/repo, target routes, preview URL if one exists, and whether source assets may be reused. Reference-only is the default until reuse is established. Record the capture date because live pages change.
2. **Capture.** Run the D2 capture script at matching, named viewport widths (default 1440 and 390) and save a screenshot for each capture. Inspect actual behavior as well as source markup: responsive order, sticky elements, motion, video, CTA destinations, and accessible text. Record missing or blocked views as gaps rather than inventing them. Stay read-only toward the source: do not submit forms, buy, or evade blocks.
3. **Map.** Inventory routes and section order. Classify each section as semantic rebuild, reusable media, background/composed asset, live commerce component, or platform app hook. For Spark targets, map each section to the roster and record `roster_status`; mark unmapped sections explicitly. Keep text, links, selectors, prices, FAQs, and controls live in the theme.
4. **Extract evidence.** Record geometry, verbatim visible copy, observed fonts/colors/spacing in `observed-styles.json`, asset URLs and provenance, image/video dimensions and role, and interaction/motion behavior. Label every value measured or inferred per D2. Optimize or export reusable media only when reuse is allowed, and never carry source trackers or vendor checkout scripts into a theme.
5. **Separate commerce truth.** Capture the source's offer and checkout presentation, then record which facts require NEXT catalog, Site Offer, coupon, shipping, or checkout configuration. Record claims that need evidence or softened copy. An unresolved configuration or claim becomes a named divergence or input gap, not a hardcoded promise.
6. **Validate and hand off.** Produce the package and human-readable notes. Validate required captures, route/section coverage, asset paths, provenance, and unresolved gaps. A fresh builder should be able to read the package, implement with `next-theme-dev`, and compare source and preview at the same widths without re-deriving the design.

Keep the first implementation focused on public landing pages and one-page storefronts. General sites, authenticated pages, and full-funnel migrations can come after the handoff works on the pilot.

## `next-theme-dev` intake

- Add a separate live-site subsection to the Implementation-Handoff Entry Contract, with its own reading-order table, validator command, and HARD STOP. Leave the Figma subsection unchanged: `tests/test_handoff_ingestion.py` pins its eleven-row table and its `figma-handoff.json` mode check. Add a parallel test for the live-site subsection.
- Dispatch on the entry file. `figma-handoff.json` runs the existing Figma gate; `design-handoff.json` with `source.kind: "live-site"` runs the `next-theme-design` validator. Neither path falls back to the other or to re-reading the source.
- Reuse the Spark roster routing, asset validation, geometry assertion, and copy lint where their contracts fit, and adapt source-specific checks instead of weakening them.
- The design package is the source of record during implementation. Theme `DESIGN.md` remains the house-style reference where the package is silent.

`next-theme-design` stops at the handoff. It does not create or publish a theme, configure offers/coupons/shipping, place orders, or push via `ntk`. Those actions belong to `next-theme-dev` and the store operator's normal authority.

## Order of work and versions

1. `next-theme-design`: package format, validator, capture script, a complete synthetic fixture, one failing fixture per validator rule, and tests.
2. `assert-geometry.mjs` and `copy-lint.py` accept the new schema IDs, with tests.
3. `next-theme-dev` live-site intake and its test.
4. The private pilot run against the operator's brief.

Steps 1–3 can land as one PR with a commit per step. Per `CONTRIBUTING.md`, add the new catalog entry at 0.1.0 and bump `next-theme-dev` and `next-theme-figma` for the files they change.

## Acceptance for the focused session

- A discoverable `next-theme-design` skill exists with a concise entrypoint, only the references and scripts it needs, and a trigger distinct from `next-theme-figma` and funnel import.
- Its validator rejects each of these, with a failing fixture per rule: a route or viewport without a screenshot and without an accepted gap; an asset path that does not resolve; a geometry or copy entry with neither a valid `capture_id` nor an `inferred` basis and reason; Figma-only fields in a live-site package; a Spark section without `roster_status`. Documented gaps remain visible.
- The synthetic fixtures use different section names and markup from each other, so the skill is shown not to depend on one page's class names.
- `next-theme-dev` accepts a validated live-site package without treating it as Figma, and its Figma path passes the existing tests unchanged.
- The private pilot package reproduces the expected findings in the operator's brief and is not committed to this repository.
- The session runs the narrow checks, including `scripts/check_public_safety.py`, and reports the remaining limits of automated capture.

## Focused-session prompt

> Implement `next-theme-design` in the NextCommerceCo `skills` repo from `docs/next-theme-design-handoff.md`, following its confirmed decisions D1–D4. Make it the live-site/HTML design-source step before `next-theme-dev` and preserve the existing Figma path. Build the package format, validator, capture script, synthetic fixtures, and tests; wire the live-site intake into `next-theme-dev`; run the relevant tests and the public-safety scanner. Then run the skill on the private pilot brief I give you and keep that package outside this repository. Return the branch/PR with concrete gaps, and do not name the pilot merchant in the PR. Do not change, push, or publish the pilot storefront.
