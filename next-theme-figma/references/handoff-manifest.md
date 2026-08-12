# Handoff Manifest

Use this reference when creating or validating a Figma-to-theme handoff package.

Generate a starter package:

```bash
node <skill-dir>/scripts/theme-figma.js new-package \
  --out /path/to/handoff \
  --project merchant-slug \
  --figma-url "<figma-url>" \
  --store merchant.29next.store \
  --repo /path/to/theme-project \
  --theme-family custom \
  --runtime-contract unknown \
  --mode implementation-handoff
```

Validate it:

```bash
node <skill-dir>/scripts/theme-figma.js validate-package /path/to/handoff
```

Validation is strict by default. Use `--non-strict` only for an explicitly incomplete draft. The generator refuses to overwrite existing package files; pass `--force` when replacement is intentional.

## Files

The package should contain:

- `figma-handoff.json`: top-level metadata and target context.
- `routes.json`: storefront route to Figma frame map.
- `sections.json`: section order, classification, target files, and gaps.
- `assets.json`: asset source and export manifest.
- `platform-divergence-ledger.json`: places where theme/platform behavior wins or needs guardrails.
- `viewport-coverage.json`: desktop/tablet/mobile coverage by route/section.
- `validation-checklist.md`: human-readable completion checklist.
- `notes.md`: concise operator notes and unresolved questions.

## Classification Values

Sections must use one of:

- `semantic-rebuild`
- `composed-asset`
- `background-asset`
- `live-commerce-component`
- `platform-app-hook`
- `screenshot-fallback`

`screenshot-fallback` requires explicit approval in `sections.json`.

## Asset Prefix Values

Assets must use one of:

- `img`
- `bg`
- `img-group`

## Canonical Asset Schema

`assets.json` uses the downstream `next-theme-dev` manifest shape, extended with Figma handoff decisions. Its top level contains `schema_version`, `figma_file_key`, `project`, and `assets`. Every asset contains:

- Consumer fields: `path` (starting with `assets/`), `asset_url_path` (without that prefix), `figma_node_id`, `role`, `alt`, `format`, `expected_width`, `expected_height`, and `clean_export_verified`. Raster assets also require `requires_alpha`; omit it for SVG assets.
- Handoff fields: `asset_id`, `section_id`, `source_layer_name`, `prefix`, `canvas_rendered`, `optimization_status`, `replace_with_backend_product_media`, and `notes`.
- Optional consumer checks where relevant: `max_bytes`, `forbid_badges`, `forbid_baked_text`, `decorative`, and `source`.

The downstream validator's `CANONICAL_REQUIRED_ASSET_FIELDS` constant is the
required consumer subset: `asset_url_path`, `role`, `alt`, `format`,
`expected_width`, `expected_height`, and `clean_export_verified`, plus
`requires_alpha` for raster assets only. Missing required keys are strict errors
and non-strict warnings; an empty value may still fail the field's semantic
validation.

This is intentionally the richer union of the generator and consumer contracts. Do not use the former generator-only names `target_path`, `source_node_id`, or nested `expected_dimensions`.

## Divergence Status Values

Use:

- `open`
- `approved`
- `implemented`
- `blocked`
- `accepted-gap`

The ledger is not a bug list. It is the record of intentional differences between Figma and the live commerce platform.

## Theme Identity

Handoff schema `next-theme-figma/handoff/v1` requires both identity fields under `target`:

- `theme_family`: `spark`, `intro-bootstrap`, or `custom`.
- `runtime_contract`: `web-components`, `jquery-core-js`, or `unknown`.

Spark uses `web-components`; Intro Bootstrap uses `jquery-core-js`. A `custom` theme may use any listed runtime contract. A family/runtime contradiction is always a hard validation error, including in non-strict mode.

## Divergence Decision Values

Use:

- `platform-wins`
- `figma-wins-with-guardrails`
- `needs-approval`
- `blocked`

Each entry records the live contract in `platform_behavior`.

## Migration (v0 → v1)

Version 1 makes the handoff vocabulary family-neutral and adds explicit theme identity:

- Rename `spark-divergence-ledger.json` to `platform-divergence-ledger.json`.
- Rename the handoff manifest key `spark_divergence_ledger` to `platform_divergence_ledger`.
- Rename each ledger entry's `spark_platform_behavior` field to `platform_behavior`.
- Replace the `spark-wins` decision with `platform-wins`; the other decision values are unchanged.
- Change the handoff schema from `next-theme-figma/handoff/v0` to `next-theme-figma/handoff/v1`.
- Change the divergence schema from `next-theme-figma/spark-divergence/v0` to `next-theme-figma/platform-divergence/v1`.
- Add the required `target.theme_family` and `target.runtime_contract` fields using the values and combinations above.

The route, section, asset, and viewport-coverage schema strings remain at v0. Legacy v0 packages are interpreted as Spark with the `web-components` runtime and validate with a deprecation warning when their optional identity fields match that legacy identity. A foreign legacy family or runtime is an error in strict mode; non-strict mode reports it as a warning and accepts the package for migration. This compatibility path will be removed in a future minor release, version 0.5.0 or later, so downstream repositories have at least one release to migrate.

## Completeness Check

Before handing to `next-theme-dev`, confirm:

- Routes have target storefront paths and Figma frame references.
- Sections are ordered and classified.
- Assets have source nodes and export decisions.
- Theme/platform divergences are explicit.
- Viewport refs are saved or missing viewports are called out.
- Mismatches have statuses.
- Screenshot fallbacks are approved.
- The package validates, or any validation failures are intentionally documented.
