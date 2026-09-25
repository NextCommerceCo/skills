# Handoff: `next-theme-design`

**Status:** brief for a focused implementation session. Decisions D1–D6 were confirmed by the operator on 2026-09-25 and revised the same day after review: geometry coordinates, asset states, capture state, behavior evidence, copy decisions, and pilot evidence. The session should not reopen them. This brief lives on its branch and is not merged into `main`; the implementation PRs carry the contract.

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
| Behavior evidence | no dedicated file | `behaviors.json`: responsive order, sticky behavior, motion, media observations, and CTA destinations, linked to captures and sections |
| Checklist and notes | `validation-checklist.md`, `notes.md` | same |

**Geometry.** In `geometry.json`, `selector` keeps its current meaning: the hook the built theme must carry. The source page's own selector is recorded separately as `source_selector`. The capture script measures boxes relative to the page. `geometry.json` uses the existing coordinate rules: section boxes are relative to the page, and element boxes are relative to their section's top-left corner, which is what `assert-geometry.mjs` compares. Keep the raw page-relative measurements in the capture records. Accepting the new schema ID must not change coordinate rules for either source.

**Copy.** Each `copy.json` string keeps the captured text in `source_text` and records a target decision: `reuse`, `replace`, `omit`, or `unresolved`, with a divergence-ledger ID where one applies. `text` holds only the text the built theme may show: the source text for `reuse`, the approved replacement for `replace`, and nothing for `omit` or `unresolved`. `copy-lint.py` already treats `strings[].text` as the list of permitted build text, so it enforces these decisions without a new mode: an omitted or unresolved source string in the build fails as drift. A `reuse` decision needs a recorded basis, and a commerce fact needs NEXT evidence. A replacement records its approval or NEXT source; that approval never counts as captured source text.

**Assets.** Each asset is `reference-only`, `downloaded`, or `replacement-needed`. A reference-only asset keeps its source URL, capture reference, and intended treatment, and needs no local file. A downloaded asset needs a local path that resolves and passes the media checks. A replacement-needed asset names the missing target asset and the section it affects. A reference asset needs a stated treatment (omit, rebuild semantically, or replace with verified store media) before its section is ready to build. Never invent a local path to satisfy the validator.

### D2. Capture and provenance

`next-theme-design` bundles a dependency-free in-page JavaScript capture script that any browser tool can run: Playwright, the Chrome DevTools protocol, or an agent's built-in browser. The skill does not bundle browser automation. For each route and viewport, the script records:

- URL, viewport width and height, device pixel ratio, scroll position, browser identity, timestamp, and a hash of the rendered HTML;
- element boxes, computed styles, and visible text for the sections and elements it is given;
- CSS animation properties and keyframes, and media dimensions, duration, and autoplay/loop/muted state.

The HTML hash shows whether the page's markup changed between runs. It does not prove that external styles or media are unchanged.

Capture in a known state. Wait for fonts and the relevant images and media metadata, or record the timeout as a gap. Take a screenshot and its measurements in the same state; if scrolling or an interaction changes the page, that is a new capture record. Sample animation in its own capture record while motion runs, then take geometry and screenshots with motion paused as a separate record. Use unscaled screenshots at device-pixel resolution.

The validator enforces provenance:

- every extracted geometry entry and every captured copy entry cites a `capture_id` that exists in `captures.json` and matches its route, viewport, and state;
- geometry and observed-style entries without a capture reference are accepted only with `"basis": "inferred"` and a reason, and every inferred entry is listed in `notes.md`;
- source copy has no inferred basis: text is captured or recorded as a gap;
- every screenshot a capture record names exists, and its pixel width equals the record's viewport width times its device pixel ratio;
- widths 1440, 768, and 390 are captured for every route, or the missing width is recorded as a gap with a reason.

`behaviors.json` records, per behavior, the section, capture IDs, trigger or state, observed result, and whether it was `extracted` or `observed`. A claim shown in a video frame records the media URL, the playback time, and a supporting screenshot capture. Motion driven by JavaScript, keyframes the script cannot read, and similar details are described from observation or recorded as gaps; one unavailable detail must not stop the rest of the capture. A screen recording is optional supporting evidence.

### D3. Cross-skill dependencies

- The Spark section roster stays in `next-theme-figma/references/spark-section-roster.json`, which Spark's own docs already point to. The validator requires `roster_status` on every section of a Spark-targeted package, because `next-theme-dev`'s fallback runs `infer-section` on a Figma frame name that a live-site package does not have.
- `copy-lint.py` stays in `next-theme-figma` and accepts `next-theme-dev/handoff-copy/v1`; its logic does not change (see D1, Copy). `assert-geometry.mjs` in `next-theme-dev` accepts `next-theme-dev/handoff-geometry/v1`. Both keep their Figma checks unchanged. The copy lint reading a schema that `next-theme-dev` owns is deliberate for V1.
- `next-theme-design` calls `next-theme-dev`'s scaffolder and validator from the sibling skill directory.
- The roster and copy lint move to `next-theme-dev` as part of the Figma migration, not in V1.

### D4. Fixtures and the pilot stay public-safe

This repository is public. Committed fixtures are synthetic. The first real merchant page is a private pilot: its package, screenshots, copy, and findings stay outside this repository. The operator supplies the pilot brief, including where the pilot package goes. PR titles, bodies, commits, and review replies name no merchant and no private organization, and report the pilot's results without quoting the page.

### D5. Intake records rights on the operator's word

The skill cannot stop someone reusing assets they have no right to, and does not try. Intake records the source's owner and whether reuse is allowed as the operator's statement. When the operator states nothing, the run is reference-only: the package keeps source URLs and screenshots and does not download source media. Declaring reuse lifts that default.

### D6. Delivery in two PRs, pilot before review

1. **PR 1, the `next-theme-dev` contract:** format reference, scaffolder, validator, synthetic fixtures, the live-site intake subsection, `assert-geometry.mjs` and `copy-lint.py` accepting the new schema IDs, and tests.
2. **PR 2, the `next-theme-design` skill,** opened against PR 1's branch: entrypoint, README, capture script, catalog entry, the three-skill wiring above, and tests.
3. **The private pilot** runs from PR 2's branch. A problem it finds is fixed in the PR that owns it: the format, validator, or intake in PR 1; capture or package authoring in PR 2.

Both PRs open as drafts; the operator marks them ready for review. The session ends with both drafts passing their checks and the pilot result reported. If the pilot's required evidence is missing, the session reports PR 2 as blocked and names what blocks it. Do not merge either PR.

The pilot writes only its package and evidence, on a new branch of the private storefront repository the pilot brief names, and commits and pushes that branch so the package outlives the session. It does not change storefront templates, settings, or store configuration, and it does not upload or publish a theme.

Versions per `CONTRIBUTING.md`: `next-theme-design` enters the catalog at 0.1.0; `next-theme-dev` and `next-theme-figma` take a minor bump for the contracts they add.

## V1 scope

1. **Intake.** Record source URL(s), target store/theme/repo, target routes, preview URL if one exists, and the source ownership and reuse statement (D5). Record the capture date because live pages change. Supplied HTML must be rendered with its assets before it can produce a package ready to build; if it cannot be rendered, produce an intake-only result with named gaps.
2. **Capture.** Run the capture script at 1440, 768, and 390 and save a screenshot for each capture. Inspect actual behavior as well as source markup: responsive order, sticky elements, motion, video, CTA destinations, and accessible text. Record missing or blocked views as gaps rather than inventing them. Stay read-only toward the source: do not submit forms, buy, or evade blocks.
3. **Map.** Inventory routes and section order. Classify each section as semantic rebuild, reusable media, background/composed asset, live commerce component, or platform app hook. For Spark targets, map each section to the roster and record `roster_status`, using `unmapped` where nothing fits. Keep text, links, selectors, prices, FAQs, and controls live in the theme.
4. **Extract evidence.** Record geometry, source copy with its target decisions, observed fonts/colors/spacing in `observed-styles.json`, assets with their state, and behaviors in `behaviors.json`, each with the provenance D2 requires. Download source media only when reuse is declared, and never carry source trackers or vendor checkout scripts into a theme.
5. **Separate commerce truth.** Capture the source's offer and checkout presentation, then record which facts require NEXT catalog, Site Offer, coupon, shipping, or checkout configuration. Record claims that need evidence or softened copy. An unresolved configuration or claim becomes a named divergence or input gap, not a hardcoded promise.
6. **Validate and hand off.** Validate with `next-theme-dev`'s validator and write human-readable notes. A fresh builder should be able to read the package, implement with `next-theme-dev`, and compare source and preview at the same widths without re-deriving the design.

The validator reports two results: whether the package is structurally valid, and whether each surface is ready to build. A valid package can still have blocked surfaces. Each gap lists its route and section, the reason, the next action, and whether it blocks building. A missing required width, an `unresolved` copy decision, or an asset without a stated treatment blocks its section until resolved or explicitly excluded. A recorded gap never permits inventing content. The builder may build ready sections and lists the blocked ones in its handback; the package counts as ready only when every intended section is.

Keep the first implementation focused on public landing pages and one-page storefronts. General sites, authenticated pages, and full-funnel migrations can come after the handoff works on the pilot.

## `next-theme-dev` intake

- Add a separate live-site subsection to the Implementation-Handoff Entry Contract, with its own reading-order table and HARD STOP. Its validator command is `next-theme-dev`'s own validator. Leave the Figma subsection unchanged: `tests/test_handoff_ingestion.py` pins its eleven-row table and its `figma-handoff.json` mode check. Add a parallel test for the live-site subsection.
- Dispatch on the entry file: `figma-handoff.json` runs the existing Figma gate, and `design-handoff.json` runs the live-site subsection. Neither path falls back to the other or to re-reading the source.
- Before building a section, surface its `omit` and `unresolved` copy decisions, assets without a treatment, and unresolved divergence-ledger entries to the operator.
- Reuse the Spark roster routing, asset validation, geometry assertion, and copy lint where their contracts fit, and adapt source-specific checks instead of weakening them.
- The design package is the source of record during implementation. Theme `DESIGN.md` remains the house-style reference where the package is silent.

`next-theme-design` stops at the handoff. It does not create or publish a theme, configure offers/coupons/shipping, place orders, or push via `ntk`. Those actions belong to `next-theme-dev` and the store operator's normal authority.

## Acceptance for the focused session

- `next-theme-dev`'s validator rejects each of these, with a failing fixture per rule: a required width with neither a screenshot nor a recorded gap; a screenshot whose width does not match its capture record; a downloaded asset whose path does not resolve; a geometry or style entry with neither a valid `capture_id` nor an `inferred` basis and reason; a captured copy entry without a valid `capture_id`; a capture reference for the wrong route or viewport; Figma-only fields in a live-site package; a Spark section without `roster_status`. Documented gaps remain visible and affect readiness.
- Two synthetic HTML pages, committed with the fixtures, use different section names and markup. The session runs the capture script against both in a browser at the required widths, commits the capture records and screenshots, and builds the fixture packages from those outputs. CI validates the committed packages and needs no browser. The fixtures include a section below the top of the page (to prove geometry normalization), a reference-only asset that validates without a local file, and a motion or media behavior that survives capture and handoff.
- With a live-site package, the unchanged copy lint fails a build containing an `omit` or `unresolved` source string and passes a build using the approved replacement text. A structurally valid fixture with blocking gaps reports itself not ready.
- `next-theme-dev` accepts a validated live-site package without treating it as Figma, and its Figma path passes the existing tests unchanged.
- A discoverable `next-theme-design` skill exists with a concise entrypoint, only the references and scripts it needs, and a trigger distinct from `next-theme-figma` and funnel import.
- All three theme skills carry the shared roles statement and cross-listed `related_skills`, and a test covers the missing-sibling message.
- The private pilot package, produced from PR 2's branch, reproduces the expected findings in the operator's brief and is committed on a branch of the private storefront repository, not to this repository.
- Pilot findings label each item as freshly extracted, manually observed, or taken from prior-audit input. Prior-audit input gives context but cannot stand in for the fresh evidence the brief requires; missing required evidence makes the pilot incomplete, even when the gap is recorded. Report what the pilot found separately from whether the package is ready to build.
- The session runs the narrow checks, including `scripts/check_public_safety.py`, and reports the remaining limits of automated capture.

## Focused-session prompt

> Implement `next-theme-design` in the NextCommerceCo `skills` repo from `docs/next-theme-design-handoff.md` on the `next-theme-design-handoff` branch, following its confirmed decisions D1–D6. The three theme skills are linked and used together; make every entrypoint say so. Open PR 1 with the `next-theme-dev` package contract and live-site intake, then PR 2 with the `next-theme-design` skill against PR 1's branch, both as drafts, and run the relevant tests and the public-safety scanner for each. Then run the skill from PR 2's branch on the private pilot brief I give you: commit the pilot package and evidence on a new branch of the private storefront repository the brief names, and push that branch. Fix what the pilot finds in the PR that owns it. Return both draft PRs with the pilot result, package readiness, and concrete gaps; if the pilot's required evidence is missing, say PR 2 is blocked and what blocks it. Do not merge the PRs or mark them ready for review. Do not name the pilot merchant or any private organization in PRs, commits, or replies. Do not change storefront templates, settings, or store configuration, or upload or publish a theme.
