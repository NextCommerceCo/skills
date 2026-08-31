---
name: next-theme-figma
version: 0.4.2
description: |
  Prepare Figma storefront designs for NEXT Commerce theme
  implementation handoff. Use when auditing, inspecting, extracting assets
  from, or preparing NEXT storefront/theme work from a Figma design source,
  including Spark, Intro Bootstrap, and custom themes; PDPs, homepage sections, content pages, responsive
  desktop/tablet/mobile fidelity, Figma asset extraction, section
  classification, visual comparison, or handoff into next-theme-dev. This
  skill creates a low-inference design-source package; it does not implement
  theme code.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
---

# NEXT Theme Figma

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-theme-figma` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |
| **Version check** | From a source checkout, run `./skills.sh status all next-theme-figma`. `stale` means the installed copy is older, `modified` means equal versions differ, `local-newer` protects a newer installed copy, and `unknown-version` flags a version outside `X.Y.Z`. Review with `dry-run` before refreshing. |

## Overview

Use this skill upstream of `next-theme-dev`. Treat Figma as structured source, not inspiration: inspect the file, classify sections and assets, record theme/platform divergences, capture references, and produce a handoff package that a theme implementation agent can consume without guessing.

If the user asks to implement a theme directly from Figma, first run this workflow until the design-source package is clear enough. Then load `next-theme-dev` for DTL/theme edits, ntk push/pull, CSS builds, and storefront QA.

## Load References

Load only the references needed for the current step:

- `references/figma-contract.md` for intake, naming, viewport, layer-tree, token, and authoring validation.
- `references/asset-export-rules.md` before exporting or accepting any Figma media asset.
- `references/spark-commerce-surfaces.md` when `theme_family` is `spark` and the design touches PDPs, cart, account/header state, subscriptions, reviews/apps, product media, or other dynamic commerce behavior.
- `references/intro-commerce-surfaces.md` when `theme_family` is `intro-bootstrap` and the design touches PDPs, cart, subscriptions, typography settings, or other dynamic commerce behavior.
- `references/developer-workflow.md` for section classification, visual verification, remediation loops, and handoff sequencing.
- `references/handoff-manifest.md` when creating or validating the handoff package.
- `references/designer-checklist.md` when the Figma source is incomplete and the designer/merchant needs actionable fixes.

## Workflow

### Figma Tool Failure-Mode Runbook

Run this before interpreting incomplete Figma results:

1. If metadata for a large frame is unexpectedly empty or contains the frame
   without its expected children, treat it as truncation/tool failure, not as
   an empty design. Record the node ID, dimensions, and failed operation.
2. Treat Figma tool calls as a budgeted resource: session rate limits and
   per-request asset caps are real. Reuse saved node data and renders instead
   of spending calls on the same source repeatedly.
3. Use this ordered export fallback ladder:
   1. Prefer exporting the smallest useful parent section or frame over many
      leaf-node calls when it preserves the intended composition.
   2. Export distinct production asset nodes in bounded batches. Avoid a broad
      parent dominated by repeated component instances, where repeated icons
      can exhaust caps and crowd out the intended assets.
   3. If per-node export floods the cap for a composed icon, recover that
      composition through an available Figma plugin-API export.
   4. Use an operator-authorized local `.fig` archive when tool exports cannot
      recover original assets. An archive can contain sources such as videos;
      never inspect one without authorization.
   5. As the last rung, save one full-page render and crop section imagery
      locally using recorded geometry. Record that the crop came from a render.
4. Retry selection synchronization at most three times. If it keeps returning
   the top-level frame or wrong node, stop retrying and switch to targeted section node URLs,
   the full-page-render last rung, or the
   authorized `.fig` archive.
5. Before implementation handoff, prove that both a Figma reference PNG and a
   storefront preview PNG can be captured at matching widths. Follow the
   screenshot fallback ladder in `references/developer-workflow.md`; reuse an
   available agent, connector, local browser, or manual operator capture rather
   than installing or bundling browser automation for the skill; desktop is
   1440px and mobile is 390px. If either side cannot produce screenshots, stop
   or record an explicit `accepted-gap` with owner, affected routes/viewports,
   and rationale.
   DOM metrics alone are not visual QA and must never be silently substituted
   for screenshots.

This runbook does not relax the per-route desktop/mobile coverage gate. Every
route still needs its own coverage entry and explicit missing-viewport status.
Record tablet coverage separately when the design supplies a tablet frame.

### 1. Intake Gate

Require or infer these before fetching deeply or editing theme code:

- Figma file URL, file key, or selection links for the relevant page/frames.
- Target store, theme, theme project folder, theme family, and runtime contract.
- Target pages/routes, such as `/`, `/products/<slug>/`, `/pages/<slug>/`.
- Current preview URL and theme ID when there is an existing theme to compare.
- Available Figma viewports: desktop, tablet, mobile.
- Work mode: `design-audit`, `handoff-prep`, or `implementation-handoff`.

Ask only for missing information that blocks the next step. For three raw Figma links with no target or mode, ask for the target store, theme, and theme project folder and whether this is audit, handoff prep, or implementation handoff.

Useful local CLI:

`<skill-dir>` is the directory this skill is installed into, which depends on the install target — `~/.claude/skills/next-theme-figma` (Claude Code), `~/.codex/skills/next-theme-figma` (Codex), `~/.agents/skills/next-theme-figma` (other agents) — or the `next-theme-figma/` folder of a repo checkout.

```bash
node <skill-dir>/scripts/theme-figma.js parse-url "<figma-url>"
node <skill-dir>/scripts/theme-figma.js infer-section "hero1-desktop"
```

### 2. Validate The Figma Contract

Before deciding implementation shape, inspect the Figma source:

- Page/frame organization and route grouping.
- Naming convention: `{category}{number}-{breakpoint}` where possible.
- Desktop/tablet/mobile frame coverage and section order.
- Layer tree availability, auto layout, text layers, fills, masks, and hidden variants.
- Asset prefixes: `img:`, `bg:`, `img-group:`.
- Typography, font availability, color/token usage, and spacing intent.
- Dynamic commerce surfaces that theme/platform code should own.

Read `references/figma-contract.md` for the full validation checklist. If the design violates the contract, decide whether to continue with documented gaps, request designer fixes, or create a partial handoff.

### 3. Build A Source Map

For each target route, capture:

- Figma page/frame names and node IDs by viewport.
- Reference screenshot paths by viewport.
- Storefront route/template target.
- Section order and section node IDs.
- Existing preview URL and screenshot paths if comparing against a current build.

Fetch all available breakpoints for a section/page before making classification calls. Avoid repeated Figma fetches during refinement; work from saved refs, node data, and explicit notes unless the design changed or a source fact is ambiguous.

### 4. Classify Every Section

Classify each section before implementation:

- `semantic-rebuild`: live text, links, forms, FAQ, grids, tables, nav/footer, content sections.
- `composed-asset`: an intentional `img-group:` composite that should export as one asset.
- `background-asset`: a `bg:` fill used behind live content.
- `live-commerce-component`: PDP gallery, variant picker, price, add-to-cart, cart drawer, product cards, app hooks, or subscription surfaces.
- `platform-app-hook`: review widgets, loyalty apps, analytics/view/add-to-cart hooks, account/cart state, or dashboard-driven integrations.
- `screenshot-fallback`: only with explicit approval, and only when the output is a static prototype or a non-interactive visual fallback.

Hard stop: do not produce a page made mostly from full-section screenshots for a production storefront unless the user explicitly accepts a static prototype. Text, links, controls, SEO, accessibility, product data, and responsive behavior should remain live.

### 5. Create Asset And Divergence Ledgers

For every asset, record the source node ID, prefix/type, target filename, format, dimensions, alpha needs, optimization status, canvas-rendered status, and whether theme/backend product media should replace it. Read `references/asset-export-rules.md`.

For every place where Figma should not be implemented literally, add a platform divergence entry. Read the commerce-surface reference for the recorded `theme_family`; common divergences include PDP gallery/carousel behavior, product image aspect ratios, variant control names, price/availability bindings, add-to-cart form contracts, cart drawer hooks, subscriptions, reviews/apps, and cached header/account/cart state.

### 5a. Optional Product Media Handoff

When PDP Figma gallery images differ from the store's backend product media, offer the user an explicit follow-up path: extract the Figma product media as a backend-update manifest for `next-theme-dev`. This is a handoff step, not a theme implementation shortcut.

For each product, record:

- Storefront route, parent product ID, variant IDs, option labels, current backend image count, and existing backend image IDs plus `display_order` values.
- Source Figma node IDs for each hero/carousel image, including viewport/frame provenance.
- Whether the asset is square and product-listing-safe. If square media is required, export or canvas-render to a square source before optimization.
- Intended display order, captions/alt text, and variant associations.
- File format, dimensions, byte size before/after final optimization, and whether `cwebp` or another lossless/lossy pass was used.
- Which images should replace old backend media and which old images should remain.
- Platform divergence deltas surfaced by the handoff; use the family-specific commerce guidance above for the canonical rules.

Do not use page thumbnails, estimated crops, or full PDP screenshots as product listing media. Use original or canvas-rendered Figma assets only, and keep the original source export in its native format plus the optimized upload candidate in the handoff package.

### 6. Run The Visual Verification Loop

Repeat until the package is close enough for implementation or all gaps are documented:

1. Complete the screenshot-capability preflight above, then capture Figma refs
   for desktop/tablet/mobile where available.
2. Compare against real existing preview screenshots at matching widths when a
   theme already exists. Never substitute DOM geometry for an unavailable PNG.
3. Record mismatches by route, section, viewport, and severity.
4. Mark each mismatch `fix-now`, `platform-divergence`, `designer-input-needed`, or `accepted-gap`.
5. Update the handoff package.
6. Re-check the affected viewports.

Do not compress assets during iteration. Compression is a final handoff step after source selection and visual crop decisions are stable.

### 7. Produce The Handoff Package

Use the local generator to avoid blank-page drift:

```bash
node <skill-dir>/scripts/theme-figma.js new-package \
  --out /path/to/handoff/example-store-figma \
  --project example-store \
  --figma-url "<figma-url>" \
  --store example.29next.store \
  --repo /path/to/theme-project \
  --theme-family custom \
  --runtime-contract unknown \
  --mode implementation-handoff
```

Fill the generated JSON/Markdown files, then validate:

```bash
node <skill-dir>/scripts/theme-figma.js validate-package /path/to/handoff/example-store-figma
```

Validation is strict by default: placeholder or incomplete routes, nodes, assets, and divergence entries fail. Use `--non-strict` only while drafting. `new-package` refuses to replace its package files unless `--force` is supplied explicitly.

Strict validation also fails when a `reference_screenshots` or `figma_ref`/`preview_ref` path does not exist inside the package (non-strict warns).

A complete handoff includes:

- Route/page manifest.
- Section manifest with classification and implementation targets.
- Asset manifest with source node IDs and export decisions.
- Platform divergence ledger.
- Reference screenshot paths.
- Implementation priority order.
- Unresolved design gaps.
- Validation checklist.

After validation, hand the package to `next-theme-dev` for actual theme edits.
