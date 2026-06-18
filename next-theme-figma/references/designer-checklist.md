# Designer Checklist

Share this when the Figma source needs designer or merchant cleanup before a low-inference theme handoff.

## Access

- Link sharing permits view access, or the implementer is invited to the file.
- The Figma file can be inspected through Figma tools/API, not only viewed as screenshots.

## Structure

- Pages or groups clearly map to storefront routes.
- Section frames are ordered and named clearly.
- Reusable sections follow `{category}{number}-{breakpoint}` where possible.
- Desktop, tablet, and mobile variants exist where responsive fidelity matters.

## Layout

- Key sections use auto layout or clear constraints.
- Mobile/tablet variants are complete, not rough desktop copies.
- Text wrapping, section spacing, and image crops are intentional at each viewport.

## Copy

- Visible copy is final enough for implementation.
- Placeholder text is marked as placeholder.
- Legal, guarantee, product, pricing, and review copy is approved or flagged.

## Images

- Image layers use `img:`, `bg:`, or `img-group:`.
- `img:` assets are fully contained and not accidentally clipped.
- `bg:` images are fills on the section/column that owns the background.
- `img-group:` wraps the exact composed visual to export.
- Product art is clean if Spark should render sale badges or live price state.
- Logos/icons are individual assets, not typed approximations.

## Typography And Color

- Fonts are web-available or flagged for image rendering.
- Colors use variables/styles or final hex values.
- Spacing and type scale are consistent across breakpoints.

## Commerce Behavior

Flag any area where design is illustrative rather than literal:

- PDP gallery.
- Variant picker.
- Prices or sale state.
- Add-to-cart.
- Cart drawer.
- Reviews/apps.
- Subscriptions/memberships.
- Account/cart/header state.

## Handoff Notes

Ask the designer to call out:

- Which areas must match exactly.
- Which areas are flexible.
- Which images should come from backend product media.
- Which composed visuals should remain static.
- Any missing mobile/tablet decisions.
