# Asset Export Rules

Use this before exporting, accepting, or naming assets from a Figma storefront source.

## Core Rule

Export the smallest real Figma node that represents the intended storefront asset. Do not use thumbnails, raw guessed crops, or full-section screenshots as production assets unless the handoff explicitly approves a static bitmap fallback.

Figma raw image URLs can point at the original uploaded file, not the canvas-rendered crop. Prefer node/image render exports for the selected node so masks, crops, and composed groups match the design.

## Prefix Decisions

### `img:`

Use for discrete content media:

- Product cutouts.
- Lifestyle photos inside a card/column.
- Logos and press marks.
- Icons and illustrations.

Export the image/vector node or its fixed-size frame. It should be fully contained, not offset outside the export bounds.

### `bg:`

Use for decorative fills behind live content:

- Section background photos.
- Column background textures.
- Decorative gradient/photo fills.

Record background position, crop, and overlay needs. The implementation should render live text/CTA over the background.

### `img-group:`

Use when the designed visual is intentionally a composition:

- Product plus badge plus lifestyle/photo stack.
- Phone/mockup plus overlays.
- Editorial collage.
- UGC mosaic that is meant to remain one image.

Export the parent group/frame, not individual children. The handoff must explain why a semantic rebuild is not appropriate.

## Full-Section Screenshots

Full-frame screenshots are diagnostic references by default. They are allowed as production assets only when:

- The user explicitly asks for a static prototype, or
- The section is a non-interactive visual fallback, and
- The handoff marks the section `screenshot-fallback` with approval and consequences.

Consequences to name: baked text, poor SEO, inaccessible links/controls, rigid responsive behavior, stale product/pricing state, and harder localization.

## Manifest Fields

For each asset, record:

- `asset_id`
- `section_id`
- `figma_node_id`
- `source_layer_name`
- `prefix`: `img`, `bg`, or `img-group`
- `path`: target file path beginning with `assets/`
- `asset_url_path`: theme template path without the `assets/` prefix
- `role`
- `alt`
- `format`: `png`, `jpg`, `jpeg`, `svg`, or `webp`
- `expected_width` and `expected_height` after export
- `requires_alpha` for raster assets; omit it for SVG assets
- `canvas_rendered`
- `optimization_status`: `not-started`, `source-selected`, `optimized`, or `blocked`
- `replace_with_backend_product_media`
- `clean_export_verified`
- `notes`

## Format Choices

- PNG: transparent product cutouts, logos, UI composites with alpha, crisp edges.
- JPG/JPEG: opaque photography where size matters.
- SVG: clean vector logos/icons with no unwanted text or embedded raster.
- WebP: optimized final media when the theme/ntk target supports it.
- AVIF: do not rely on it unless the target theme tooling accepts and uploads it.

## Product Media

If Figma uses product images that should ultimately come from backend product media:

- Mark `replace_with_backend_product_media: true`.
- Record the Figma media as a visual reference, not the final storefront source.
- Keep Spark product image aspect-ratio behavior unless the implementation explicitly changes the component safely.

## Badge Doubling

Audit promotional labels in three places:

- Pixels baked into product art.
- Spark-rendered sale badges, price blocks, or product cards.
- Dashboard product compare-at/sale state.

Only one layer should communicate the same sale/discount in a given placement. If the image includes a badge but Spark should own live sale state, request clean product art or record a divergence.

## Naming

Use lowercase, kebab-case filenames under a merchant/theme folder where possible:

```text
assets/img/example-store/hero-product.png
assets/img/example-store/logos/press-logo.svg
assets/img/example-store/pdp/how-it-works.jpg
```

Name by storefront role, not raw Figma layer names. Store source node IDs in the manifest, not filenames.

## Optimization

Optimize only after source selection and crops are approved. Do not repeatedly compress while visually iterating. Keep reference screenshots uncompressed.
