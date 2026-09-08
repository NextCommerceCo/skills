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

For reusable section exports, both the design-family convention and Spark's own
section convention are valid:

```text
{category}{number}-{breakpoint}
hero1-desktop
hero1-tablet
hero1-mobile
section_<name>-{breakpoint}
section_hero-desktop
```

The Spark form is the literal `section_<name>-{breakpoint}`, matched
case-insensitively; a name that also fits the numbered design-family pattern is
treated as design-family. Hyphen or space variants are not Spark form.

Normalize to section IDs such as `hero-1`, `benefits-2`, `faq-1`. Use:

```bash
node <skill-dir>/scripts/theme-figma.js infer-section "hero1-desktop"
node <skill-dir>/scripts/theme-figma.js infer-section "section_hero-desktop"
```

See `references/spark-section-roster.md` for the advisory mapping from design
families to Spark sections. When a Spark name is listed only as an alternate,
`family` reports the first roster row in file order that lists it.

## Viewport Coverage

Expected widths:

- Desktop: 1440px when available.
- Tablet: 768px when available.
- Mobile: 375px or 390px when available.

If a viewport is missing, record it as an unresolved design gap and do not invent responsive behavior silently. It is fine to proceed when the implementation agent can preserve existing theme behavior, but the handoff should make the missing source explicit.

## Layer Tree And Layout

Check:

- Layer tree is available through Figma tools/API.
- Key sections use auto layout or clear constraints.
- Text is real text unless intentionally image-rendered.
- Repeated cards/rows have consistent structure.
- Hidden variants are inspected for cleaner mobile/desktop crops.
- Masks, fills, and clipping are understood before asset export.

An unexpectedly childless response for a large frame is a tool/truncation
failure, not evidence that the design is empty. After at most three failed
selection-sync attempts, switch to targeted node URLs, full-frame render plus
local slicing, or an operator-authorized local `.fig` archive.

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

Use this canonical variable namespace. Alias matching is exact and
case-sensitive after trimming whitespace.

The **Bound** column records what the Debranded Sections library and a
merchant storefront file built on it actually bind, confirmed against the live
files on 2026-09-08 by the library's owner. `yes` rows exist in both files. `reserved` rows are names this contract holds open
for the design side; nothing binds them today, so an export must not expect
them, and a handoff that records one is transcribing intent rather than the
file. The file is the truth over any handoff PDF or doc table.

| Canonical name | Aliases | Type | Bound |
|---|---|---|---|
| `color/brand/primary` | `brand/primary` | color | yes |
| `color/brand/secondary` | `brand/secondary` | color | yes |
| `color/brand/accent` | `brand/accent` | color | reserved |
| `color/brand/whitespace` | `surface/background`, `surface/bg` | color | yes |
| `color/text/primary` | `text/primary` | color | yes |
| `color/text/secondary` | `text/secondary` | color | reserved |
| `color/text/inverse` | `text/inverse` | color | reserved |
| `color/border/default` | `border/default` | color | yes |
| `color/state/success` | `state/success` | color | reserved |
| `color/state/warning` | `state/warning` | color | reserved |
| `color/state/error` | `state/error` | color | reserved |
| `spacing/sectionpadding-small`, `-medium`, `-big` | — | dimension | yes |
| `spacing/contentgap-tiny`, `-small`, `-medium`, `-big` | — | dimension | yes |
| `spacing/contentgap-8static` | — | dimension | yes |
| `radius/radius-medium` | `radius/medium` | radius | yes |
| `radius/radius-small`, `-big` | `radius/small`, `radius/big` | radius | reserved |
| `font/size-heading2` | — | font-size | yes |
| `font/size-heading1`, `-heading3` | — | font-size | reserved |
| `font/size-p-small`, `-p-big` | — | font-size | yes |
| `font/size-p` | — | font-size | reserved |
| `font/family-heading`, `font/family-body` | — | font-family | reserved |
| `maxw/cta` | — | dimension | yes |
| `maxw/container` | — | dimension | reserved |

There is no `surface/*` collection in the library; the alias exists only so an
older handoff that used the PDF's names still classifies. The `font/family-*`
variables are planned on the design side and may be absent. Unknown variables
are preserved rather than forced into this namespace. If a token is not a
variable, record its literal value and let the operator decide whether
`target.kind` is `theme-setting`, `css-custom-property`, `one-off`, or
`unmapped`.

## Dynamic Commerce Surfaces

Flag any section touching:

- PDP media/gallery.
- Product cards and backend media.
- Variant pickers.
- Price, compare-at price, subscriptions, availability, or inventory.
- Add-to-cart forms and cart drawers.
- Reviews, ratings, loyalty, subscriptions, memberships, or app widgets.
- Account, cart count, or header state.

These require a platform divergence decision before implementation. Read the commerce-surface reference for the handoff's `theme_family`.

## Contract Result

End validation with one of:

- `ready`: enough source truth exists for implementation handoff.
- `ready-with-gaps`: handoff can proceed, but unresolved gaps are explicit.
- `designer-input-needed`: key source structure/assets/viewports are missing.
- `audit-only`: no implementation handoff requested.
