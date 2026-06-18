# Handoff Manifest

Use this reference when creating or validating a Figma-to-theme handoff package.

Generate a starter package:

```bash
node <skill-dir>/scripts/theme-figma.js new-package \
  --out /path/to/handoff \
  --project merchant-slug \
  --figma-url "<figma-url>" \
  --store merchant.29next.store \
  --repo /path/to/theme-worktree \
  --mode implementation-handoff
```

Validate it:

```bash
node <skill-dir>/scripts/theme-figma.js validate-package /path/to/handoff
```

## Files

The package should contain:

- `figma-handoff.json`: top-level metadata and target context.
- `routes.json`: storefront route to Figma frame map.
- `sections.json`: section order, classification, target files, and gaps.
- `assets.json`: asset source and export manifest.
- `spark-divergence-ledger.json`: places where Spark/platform behavior wins or needs guardrails.
- `viewport-coverage.json`: desktop/tablet/mobile coverage by route/section.
- `validation-checklist.md`: human-readable completion checklist.
- `notes.md`: concise operator notes and unresolved questions.

## Classification Values

Sections must use one of:

- `semantic-rebuild`
- `composed-asset`
- `background-asset`
- `live-spark-component`
- `platform-app-hook`
- `screenshot-fallback`

`screenshot-fallback` requires explicit approval in `sections.json`.

## Asset Prefix Values

Assets must use one of:

- `img`
- `bg`
- `img-group`

Every asset should include source node ID, target path, format, expected dimensions, alpha needs, canvas-rendered status, optimization status, and backend media replacement intent.

## Divergence Status Values

Use:

- `open`
- `approved`
- `implemented`
- `blocked`
- `accepted-gap`

The ledger is not a bug list. It is the record of intentional differences between Figma and the live commerce platform.

## Completeness Check

Before handing to `next-theme-dev`, confirm:

- Routes have target storefront paths and Figma frame references.
- Sections are ordered and classified.
- Assets have source nodes and export decisions.
- Spark/platform divergences are explicit.
- Viewport refs are saved or missing viewports are called out.
- Mismatches have statuses.
- Screenshot fallbacks are approved.
- The package validates, or any validation failures are intentionally documented.
