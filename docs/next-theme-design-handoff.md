# Handoff: `next-theme-design`

**Status:** brief for a focused implementation session. Decisions D1–D6 were confirmed by the operator on 2026-09-25; the session should not reopen them. No skill or runtime change is part of this handoff.

## Outcome

Create a source-preparation skill for NEXT storefront work that starts from a rendered website or its HTML and produces a validated design handoff for `next-theme-dev`. Use `next-theme-design` for live-site references and `next-theme-figma` for Figma sources. `next-theme-dev` remains the builder (DTL/Spark implementation, platform data and checkout integration, `ntk` upload, and preview QA) and becomes the owner of the handoff package format that both source skills produce.

The first use case is a merchant landing page rebuilt as a Spark storefront. The package should preserve enough evidence that a builder can reproduce the design and behavior without repeatedly rediscovering the source page. It should distinguish what the page *shows* from what is true or configured in NEXT.

## The three theme skills work together

`next-theme-figma`, `next-theme-design`, and `next-theme-dev` are linked skills meant to be installed and used together. An agent that loads any one of them must understand that from its entrypoint alone:

| Skill | Role | Also holds |
|---|---|---|
| `next-theme-figma` | Turns a Figma source into a handoff package | The Spark section roster and the copy lint, used by the other two |
| `next-theme-design` | Turns a live site or its HTML into a handoff package | The in-page capture script |
| `next-theme-dev` | Validates the package and builds the theme from it | The package format, scaffolder, validator, and geometry assertion |

Work always runs from one source skill into `next-theme-dev`. Required wiring:

- Each of the three `SKILL.md` files opens with the same short statement of these roles and that order, and says the three are installed together.
- Each `skills.json` entry lists the other two in `related_skills` with the relationship.
- Each `README.md` tells the person to install all three. `skills.sh` installs one named skill or `all` and does not resolve dependencies, so the README gives the three install commands.
- When a skill needs a sibling that is not installed, it stops with a message naming the missing skill and its install command. It never falls back to re-deriving what the sibling provides.

## Why a separate skill

- `next-theme-figma` already follows the useful pattern: inspect a design source, classify sections and assets, record divergences, and hand a validated package to `next-theme-dev`. Its schemas and validator deliberately require Figma metadata, text layers, and variables.
- `next-theme-dev` currently treats `implementation-handoff` as a Figma package: it requires `figma-handoff.json` and runs the Figma validator. A website package must not pose as a Figma package to pass this gate.
- Funnel-import tooling that extracts a merchant funnel for Campaign Page Kit has relevant safety and asset-capture ideas, but its output is a campaign, not a Spark/DTL design handoff.
- A recent landing-page rebuild showed the missing source-preparation work. The desktop and mobile geometry, the hero animation timing, a product video and the claims shown in its frames, offer-dependent shipping copy, and a small hero callout were each found in a separate pass.

## Decisions

### D1. `next-theme-dev` owns the shared package format

The format is meant to become the single package format for every design source. V1 implements it for live sites only. Moving the Figma path onto it is out of scope and has no date; the Figma package, validator, schemas, and tests stay as they are.

`next-theme-dev` holds the format reference, the scaffolder that writes a blank package, the validator, and the synthetic fixtures. Schema IDs are `next-theme-dev/design-handoff/v1`, `next-theme-dev/handoff-geometry/v1`, and `next-theme-dev/handoff-copy/v1`.

| Purpose | Figma package (unchanged) | Shared format (live-site in V1) |
|---|---|---|
| Entry point and target identity | `figma-handoff.json` | `design-handoff.json` with `source.kind: "live-site"`, source URLs, capture date, source ownership, and reuse statement |
| Capture records | none; Figma node IDs serve this role | `captures.json` plus a `captures/` directory of screenshots |
| Routes, sections, assets, divergence ledger, viewport coverage | `routes.json`, `sections.json`, `assets.json`, `platform-divergence-ledger.json`, `viewport-coverage.json` | same file names and shapes; `figma_ref` becomes `source_ref`, and `figma_node_id` and `figma_names` become capture references |
| Geometry and copy | `geometry.json`, `copy.json` with Figma sources | same file names and shapes, the new schema IDs, source `dom-capture` |
| Visual values | `tokens.json` (Figma variables) | `observed-styles.json` (computed values observed on the page, never labeled as variables) |
| Checklist and notes | `validation-checklist.md`, `notes.md` | same |

In `geometry.json`, `selector` keeps its current meaning: the hook the built theme must carry. The source page's own selector is recorded separately as `source_selector`. Boxes are page-relative CSS pixels at the named viewport width.

### D2. Capture and provenance

`next-theme-design` bundles a dependency-free in-page JavaScript capture script that any browser tool can run: Playwright, the Chrome DevTools protocol, or an agent's built-in browser. The skill does not bundle browser automation. For each route and viewport, the script records:

- URL, viewport size, device pixel ratio, timestamp, and a hash of the rendered HTML, so a later run can tell the source page changed;
- element boxes, computed styles, and visible text for the sections and elements it is given;
- CSS animation properties and keyframes, and media dimensions, duration, and autoplay/loop/muted state.

The operator or agent saves a screenshot for each capture record. The validator enforces provenance:

- every geometry and copy entry cites a `capture_id` that exists in `captures.json`;
- geometry and observed-style entries without a capture reference are accepted only with `"basis": "inferred"` and a reason, and every inferred entry is listed in `notes.md`;
- copy has no inferred basis: text is captured or recorded as a gap;
- every screenshot a capture record names exists, and its pixel width equals the record's viewport width times its device pixel ratio;
- widths 1440, 768, and 390 are captured for every route, or the missing width is recorded as a gap with a reason.

Motion driven by JavaScript is described from observation and marked as observed rather than extracted. A screen recording is optional supporting evidence.

### D3. Cross-skill dependencies

- The Spark section roster stays in `next-theme-figma/references/spark-section-roster.json`, which Spark's own docs already point to. The validator requires `roster_status` on every section of a Spark-targeted package, because `next-theme-dev`'s fallback runs `infer-section` on a Figma frame name that a live-site package does not have.
- `copy-lint.py` stays in `next-theme-figma` and accepts `next-theme-dev/handoff-copy/v1`. `assert-geometry.mjs` in `next-theme-dev` accepts `next-theme-dev/handoff-geometry/v1`. Both keep their Figma checks unchanged.
- `next-theme-design` calls `next-theme-dev`'s scaffolder and validator from the sibling skill directory.
- The roster and copy lint move to `next-theme-dev` as part of the Figma migration, not in V1.

### D4. Fixtures and the pilot stay public-safe

This repository is public. Committed fixtures are synthetic. The first real merchant page is a private pilot: its package, screenshots, copy, and findings stay outside this repository. The operator supplies the pilot brief, including where the pilot package goes. PR titles, bodies, commits, and review replies name no merchant and no private organization, and report the pilot's results without quoting the page.

### D5. Intake records rights on the operator's word

The skill cannot stop someone reusing assets they have no right to, and does not try. Intake records the source's owner and whether reuse is allowed as the operator's statement. When the operator states nothing, the run is reference-only: the package keeps source URLs and screenshots and does not download source media. Declaring reuse lifts that default.

### D6. Delivery in two PRs, pilot before review

1. **PR 1, the `next-theme-dev` contract:** format reference, scaffolder, validator, synthetic fixtures, the live-site intake subsection, `assert-geometry.mjs` and `copy-lint.py` accepting the new schema IDs, and tests.
2. **PR 2, the `next-theme-design` skill,** built on PR 1: entrypoint, README, capture script, catalog entry, the three-skill wiring above, and tests.
3. **The private pilot** runs from PR 2's branch before PR 2 is marked ready for review. A gap it finds is fixed in PR 2.

Versions per `CONTRIBUTING.md`: `next-theme-design` enters the catalog at 0.1.0; `next-theme-dev` and `next-theme-figma` take a minor bump for the contracts they add.

## V1 scope

1. **Intake.** Record source URL(s), target store/theme/repo, target routes, preview URL if one exists, and the source ownership and reuse statement (D5). Record the capture date because live pages change.
2. **Capture.** Run the capture script at 1440, 768, and 390 and save a screenshot for each capture. Inspect actual behavior as well as source markup: responsive order, sticky elements, motion, video, CTA destinations, and accessible text. Record missing or blocked views as gaps rather than inventing them. Stay read-only toward the source: do not submit forms, buy, or evade blocks.
3. **Map.** Inventory routes and section order. Classify each section as semantic rebuild, reusable media, background/composed asset, live commerce component, or platform app hook. For Spark targets, map each section to the roster and record `roster_status`, using `unmapped` where nothing fits. Keep text, links, selectors, prices, FAQs, and controls live in the theme.
4. **Extract evidence.** Record geometry, verbatim visible copy, observed fonts/colors/spacing in `observed-styles.json`, asset URLs and provenance, image/video dimensions and role, and interaction/motion behavior, each with the provenance D2 requires. Export reusable media only when reuse is declared, and never carry source trackers or vendor checkout scripts into a theme.
5. **Separate commerce truth.** Capture the source's offer and checkout presentation, then record which facts require NEXT catalog, Site Offer, coupon, shipping, or checkout configuration. Record claims that need evidence or softened copy. An unresolved configuration or claim becomes a named divergence or input gap, not a hardcoded promise.
6. **Validate and hand off.** Validate with `next-theme-dev`'s validator and write human-readable notes. A fresh builder should be able to read the package, implement with `next-theme-dev`, and compare source and preview at the same widths without re-deriving the design.

Keep the first implementation focused on public landing pages and one-page storefronts. General sites, authenticated pages, and full-funnel migrations can come after the handoff works on the pilot.

## `next-theme-dev` intake

- Add a separate live-site subsection to the Implementation-Handoff Entry Contract, with its own reading-order table, validator command, and HARD STOP. Leave the Figma subsection unchanged: `tests/test_handoff_ingestion.py` pins its eleven-row table and its `figma-handoff.json` mode check. Add a parallel test for the live-site subsection.
- Dispatch on the entry file. `figma-handoff.json` runs the existing Figma gate; `design-handoff.json` runs `next-theme-dev`'s own validator. Neither path falls back to the other or to re-reading the source.
- Reuse the Spark roster routing, asset validation, geometry assertion, and copy lint where their contracts fit, and adapt source-specific checks instead of weakening them.
- The design package is the source of record during implementation. Theme `DESIGN.md` remains the house-style reference where the package is silent.

`next-theme-design` stops at the handoff. It does not create or publish a theme, configure offers/coupons/shipping, place orders, or push via `ntk`. Those actions belong to `next-theme-dev` and the store operator's normal authority.

## Acceptance for the focused session

- `next-theme-dev`'s validator rejects each of these, with a failing fixture per rule: a required width with neither a screenshot nor a recorded gap; a screenshot whose width does not match its capture record; an asset path that does not resolve; a geometry or style entry with neither a valid `capture_id` nor an `inferred` basis and reason; a copy entry without a valid `capture_id`; Figma-only fields in a live-site package; a Spark section without `roster_status`. Documented gaps remain visible.
- Two complete synthetic fixtures use different section names and markup, so the format is shown not to depend on one page's class names.
- `next-theme-dev` accepts a validated live-site package without treating it as Figma, and its Figma path passes the existing tests unchanged.
- A discoverable `next-theme-design` skill exists with a concise entrypoint, only the references and scripts it needs, and a trigger distinct from `next-theme-figma` and funnel import.
- All three theme skills carry the shared roles statement and cross-listed `related_skills`, and a test covers the missing-sibling message.
- The private pilot package, produced from PR 2's branch, reproduces the expected findings in the operator's brief and is not committed to this repository.
- The session runs the narrow checks, including `scripts/check_public_safety.py`, and reports the remaining limits of automated capture.

## Focused-session prompt

> Implement `next-theme-design` in the NextCommerceCo `skills` repo from `docs/next-theme-design-handoff.md`, following its confirmed decisions D1–D6. The three theme skills are linked and used together; make every entrypoint say so. Open PR 1 with the `next-theme-dev` package contract and live-site intake, then PR 2 with the `next-theme-design` skill on top of it, and run the relevant tests and the public-safety scanner for each. Before marking PR 2 ready, run the skill from PR 2's branch on the private pilot brief I give you and write the pilot package where that brief says, outside this repository. Return both PRs with concrete gaps. Do not name the pilot merchant or any private organization in PRs, commits, or replies, and do not change, push, or publish the pilot storefront.
