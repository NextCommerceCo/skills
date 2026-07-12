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

Validation is strict by default. Use `--non-strict` only for an explicitly incomplete draft. The generator refuses to overwrite existing package files; pass `--force` when replacement is intentional.

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

## Canonical Asset Schema

`assets.json` uses the downstream `next-theme-dev` manifest shape, extended with Figma handoff decisions. Its top level contains `schema_version`, `figma_file_key`, `project`, and `assets`. Every asset contains:

- Consumer fields: `path` (starting with `assets/`), `asset_url_path` (without that prefix), `figma_node_id`, `role`, `alt`, `format`, `expected_width`, `expected_height`, `requires_alpha`, and `clean_export_verified`.
- Handoff fields: `asset_id`, `section_id`, `source_layer_name`, `prefix`, `canvas_rendered`, `optimization_status`, `replace_with_backend_product_media`, and `notes`.
- Optional consumer checks where relevant: `max_bytes`, `forbid_badges`, `forbid_baked_text`, `decorative`, and `source`.

The downstream validator's `CANONICAL_REQUIRED_ASSET_FIELDS` constant is the
required consumer subset: `asset_url_path`, `role`, `alt`, `format`,
`expected_width`, `expected_height`, `requires_alpha`, and
`clean_export_verified`. Missing keys are strict errors and non-strict warnings;
an empty value may still fail the field's semantic validation.

This is intentionally the richer union of the generator and consumer contracts. Do not use the former generator-only names `target_path`, `source_node_id`, or nested `expected_dimensions`.

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
