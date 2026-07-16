# Theme Figma Handoff

Prepares Figma storefront designs for Next Commerce / Spark theme implementation.
It treats Figma as structured source, not inspiration: inspect the file, classify
every section, build asset and divergence ledgers, capture reference screenshots,
and produce a validated, low-inference handoff package that
[`next-theme-dev`](../next-theme-dev/) can implement without guessing.

This skill does **not** write theme code — it sits upstream of implementation.

What the handoff package contains:

- Route/page manifest and section manifest with per-section classification
  (`semantic-rebuild`, `composed-asset`, `background-asset`,
  `live-spark-component`, `platform-app-hook`, `screenshot-fallback`).
- Asset manifest with source node IDs, formats, dimensions, and export decisions.
- Spark divergence ledger — every place the theme should intentionally differ
  from the Figma frame (PDP galleries, variant pickers, cart state, app hooks).
- Reference screenshots per viewport, implementation priority order, unresolved
  design gaps, and a validation checklist.

## Requirements

- **Figma file or selection links** with view access.
- **Figma MCP or REST API access** when live node inspection or asset export is
  needed.
- **Node.js** — for the bundled helper CLI
  [`scripts/theme-figma.js`](scripts/theme-figma.js).
- Target store/theme/repo context, and a preview URL + theme ID when comparing
  against an existing build.
- [`next-theme-dev`](../next-theme-dev/) downstream for the actual implementation.

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-theme-figma
```

## How to Use

Ask your AI tool something like:

> Use /next-theme-figma to prepare this Spark storefront Figma design for a
> next-theme-dev implementation handoff: <figma-url>

The workflow: intake gate → validate the Figma contract → build a source map →
classify every section → create asset and divergence ledgers → run the visual
verification loop → generate and validate the package:

```bash
node scripts/theme-figma.js new-package --out ./handoff/example-store-figma \
  --project example-store --figma-url "<figma-url>" \
  --store example.29next.store --mode implementation-handoff
node scripts/theme-figma.js validate-package ./handoff/example-store-figma
```

Detailed rules live in [`references/`](references/) — the Figma authoring
contract, asset export rules, Spark commerce surfaces, developer workflow,
handoff manifest schema, and a designer checklist for incomplete sources.

## Safety

- Read-only against Figma and the storefront; writes local handoff files only.
- Hard stop on screenshot-heavy pages: a production storefront is never built
  from full-section screenshots without explicit approval — text, links,
  controls, SEO, and live product data stay live.
- Package validation is strict by default; placeholder entries fail until
  resolved or explicitly marked.
