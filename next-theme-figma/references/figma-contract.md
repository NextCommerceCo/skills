# Figma Contract

Use this reference when validating a Figma storefront source before theme implementation. The goal is to reduce inference: the design file should say what each page, viewport, section, and asset is.

## Required Intake

Record:

- Figma file URL or file key.
- Relevant page/frame URLs or node IDs.
- Target store, theme, theme project folder, and theme family.
- Target storefront routes and templates.
- Current preview URL/theme ID if comparing to an existing build.
- Work mode: `design-audit`, `handoff-prep`, or `implementation-handoff`.
- Available viewports: desktop, tablet, mobile.

## Page And Frame Structure

Prefer:

- A clear page or group per storefront route.
- Top-level route frames or route groups for homepage, PDP, content pages, and any special templates.
- Sections ordered visually in the Figma layer tree or documented in the handoff.
- Frame names that can be mapped to route and viewport without guessing.

For reusable section exports, use the convention:

```text
{category}{number}-{breakpoint}
hero1-desktop
hero1-tablet
hero1-mobile
```

Normalize to section IDs such as `hero-1`, `benefits-2`, `faq-1`. Use:

```bash
node <skill-dir>/scripts/theme-figma.js infer-section "hero1-desktop"
```

## Viewport Coverage

Expected widths:

- Desktop: 1440px when available.
- Tablet: 768px when available.
- Mobile: 375px or 390px when available.

If a viewport is missing, record it as an unresolved design gap and do not invent responsive behavior silently. It is fine to proceed when the implementation agent can preserve existing Spark behavior, but the handoff should make the missing source explicit.

## Layer Tree And Layout

Check:

- Layer tree is available through Figma tools/API.
- Key sections use auto layout or clear constraints.
- Text is real text unless intentionally image-rendered.
- Repeated cards/rows have consistent structure.
- Hidden variants are inspected for cleaner mobile/desktop crops.
- Masks, fills, and clipping are understood before asset export.

When rebuilding semantically, preserve Figma grouping where it controls spacing. Do not regroup siblings by meaning if the Figma parent owns the gap, padding, or alignment.

## Asset Prefix Taxonomy

Require asset layers to declare intent:

- `img:` for a discrete contained image, icon, logo, product cutout, or illustration.
- `bg:` for a decorative background fill on a section/column.
- `img-group:` for an intentional composed visual made from multiple layers.

Unprefixed media layers should be treated as ambiguous. Either inspect and classify manually, or ask the designer to rename before handoff.

## Typography And Fonts

Record:

- Font families and whether they are web-safe, Google-hosted, bundled, or unavailable.
- Size/weight/line-height by viewport when visible.
- Any display/script text that should become an image because the font cannot ship.
- Where typography should map to theme tokens/settings rather than hard-coded CSS.

## Colors, Tokens, And Settings

Prefer Figma variables or named styles for:

- Brand colors.
- Surface/background colors.
- Text colors.
- Border colors.
- State colors.
- Spacing scales.

If the file only has hard-coded values, record the actual hex/spacing values and whether they should become Spark theme settings, CSS custom properties, or one-off section styles.

## Dynamic Commerce Surfaces

Flag any section touching:

- PDP media/gallery.
- Product cards and backend media.
- Variant pickers.
- Price, compare-at price, subscriptions, availability, or inventory.
- Add-to-cart forms and cart drawers.
- Reviews, ratings, loyalty, subscriptions, memberships, or app widgets.
- Account, cart count, or header state.

These require a Spark divergence decision before implementation. Read `spark-commerce-surfaces.md`.

## Contract Result

End validation with one of:

- `ready`: enough source truth exists for implementation handoff.
- `ready-with-gaps`: handoff can proceed, but unresolved gaps are explicit.
- `designer-input-needed`: key source structure/assets/viewports are missing.
- `audit-only`: no implementation handoff requested.
