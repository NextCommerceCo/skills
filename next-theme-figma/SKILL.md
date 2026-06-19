---
name: next-theme-figma
version: 0.1.0
description: |
  Prepare Figma storefront designs for NEXT Commerce and Spark theme
  implementation handoff. Use when auditing, inspecting, extracting assets
  from, or preparing NEXT storefront/theme work from a Figma design source,
  especially Spark themes, PDPs, homepage sections, content pages, responsive
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

## Overview

Use this skill upstream of `next-theme-dev`. Treat Figma as structured source, not inspiration: inspect the file, classify sections and assets, record Spark/platform divergences, capture references, and produce a handoff package that a theme implementation agent can consume without guessing.

If the user asks to implement a theme directly from Figma, first run this workflow until the design-source package is clear enough. Then load `next-theme-dev` for DTL/Spark edits, ntk push/pull, CSS builds, and storefront QA.

## Load References

Load only the references needed for the current step:

- `references/figma-contract.md` for intake, naming, viewport, layer-tree, token, and authoring validation.
- `references/asset-export-rules.md` before exporting or accepting any Figma media asset.
- `references/spark-commerce-surfaces.md` for PDPs, cart, account/header state, subscriptions, reviews/apps, product media, or any dynamic commerce behavior.
- `references/developer-workflow.md` for section classification, visual verification, remediation loops, and handoff sequencing.
- `references/handoff-manifest.md` when creating or validating the handoff package.
- `references/designer-checklist.md` when the Figma source is incomplete and the designer/merchant needs actionable fixes.

## Workflow

### 1. Intake Gate

Require or infer these before fetching deeply or editing theme code:

- Figma file URL, file key, or selection links for the relevant page/frames.
- Target store, theme, repo/worktree, and theme family if known.
- Target pages/routes, such as `/`, `/products/<slug>/`, `/pages/<slug>/`.
- Current preview URL and theme ID when there is an existing theme to compare.
- Available Figma viewports: desktop, tablet, mobile.
- Work mode: `design-audit`, `handoff-prep`, or `implementation-handoff`.

Ask only for missing information that blocks the next step. For three raw Figma links with no target or mode, ask for the target store/theme/repo and whether this is audit, handoff prep, or implementation handoff.

Useful local CLI:

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
- Dynamic commerce surfaces that Spark/platform code should own.

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
- `live-spark-component`: PDP gallery, variant picker, price, add-to-cart, cart drawer, product cards, app hooks, or subscription surfaces.
- `platform-app-hook`: review widgets, loyalty apps, analytics/view/add-to-cart hooks, account/cart state, or dashboard-driven integrations.
- `screenshot-fallback`: only with explicit approval, and only when the output is a static prototype or a non-interactive visual fallback.

Hard stop: do not produce a page made mostly from full-section screenshots for a production storefront unless the user explicitly accepts a static prototype. Text, links, controls, SEO, accessibility, product data, and responsive behavior should remain live.

### 5. Create Asset And Divergence Ledgers

For every asset, record the source node ID, prefix/type, target filename, format, dimensions, alpha needs, optimization status, canvas-rendered status, and whether Spark/backend product media should replace it. Read `references/asset-export-rules.md`.

For every place where Figma should not be implemented literally, add a Spark divergence entry. Read `references/spark-commerce-surfaces.md`; common divergences include PDP gallery/carousel behavior, product image aspect ratios, variant control names, price/availability bindings, add-to-cart form contracts, cart drawer hooks, subscriptions, reviews/apps, and cached header/account/cart state.

### 5a. Optional Product Media Handoff

When PDP Figma gallery images differ from the store's backend product media, offer the user an explicit follow-up path: extract the Figma product media as a backend-update manifest for `next-theme-dev`. This is a handoff step, not a theme implementation shortcut.

For each product, record:

- Storefront route, parent product ID, variant IDs, option labels, current backend image count, and existing backend image IDs plus `display_order` values.
- Source Figma node IDs for each hero/carousel image, including viewport/frame provenance.
- Whether the asset is square and product-listing-safe. If square media is required, export or canvas-render to a square source before optimization.
- Intended display order, captions/alt text, and variant associations.
- File format, dimensions, byte size before/after final optimization, and whether `cwebp` or another lossless/lossy pass was used.
- Which images should replace old backend media and which old images should remain.
- Spark divergence deltas surfaced by the handoff; use the Spark divergence guidance above for the canonical rules.

Do not use page thumbnails, estimated crops, or full PDP screenshots as product listing media. Use original or canvas-rendered Figma assets only, and keep the original source export in its native format plus the optimized upload candidate in the handoff package.

### 6. Run The Visual Verification Loop

Repeat until the package is close enough for implementation or all gaps are documented:

1. Capture Figma refs for desktop/tablet/mobile where available.
2. Compare against existing preview screenshots when a theme already exists.
3. Record mismatches by route, section, viewport, and severity.
4. Mark each mismatch `fix-now`, `spark-divergence`, `designer-input-needed`, or `accepted-gap`.
5. Update the handoff package.
6. Re-check the affected viewports.

Do not compress assets during iteration. Compression is a final handoff step after source selection and visual crop decisions are stable.

### 7. Produce The Handoff Package

Use the local generator to avoid blank-page drift:

```bash
node <skill-dir>/scripts/theme-figma.js new-package \
  --out /path/to/handoff/uvbrite-figma \
  --project uvbrite \
  --figma-url "<figma-url>" \
  --store uvbrite.29next.store \
  --repo /path/to/theme-worktree \
  --mode implementation-handoff
```

Fill the generated JSON/Markdown files, then validate:

```bash
node <skill-dir>/scripts/theme-figma.js validate-package /path/to/handoff/uvbrite-figma
```

A complete handoff includes:

- Route/page manifest.
- Section manifest with classification and implementation targets.
- Asset manifest with source node IDs and export decisions.
- Spark divergence ledger.
- Reference screenshot paths.
- Implementation priority order.
- Unresolved design gaps.
- Validation checklist.

After validation, hand the package to `next-theme-dev` for actual theme edits.
