# Geometry And Copy Manifests

Read this when producing or validating the `geometry.json` and `copy.json`
members of a handoff package.

Both manifests exist for the same reason: the two most expensive classes of
rework in a Figma-to-theme build are **geometry stated from memory** and
**copy invented at the keyboard**. Both are cheap to extract from the Figma
source at handoff time and cheap to assert against a build. Neither is
recoverable later without a review round.

Both are required in strict validation when `figma-handoff.json` has
`mode: implementation-handoff`. In other modes their absence is a warning.

## `geometry.json`

Schema `next-theme-figma/geometry/v1`. Per route, per viewport, per section:
the element boxes the implementation must reproduce.

```json
{
  "schema_version": "next-theme-figma/geometry/v1",
  "project": "example-store",
  "source": "figma-metadata",
  "extracted_at": "2026-01-01T00:00:00.000Z",
  "routes": [
    {
      "route_id": "home",
      "viewports": {
        "desktop": {
          "frame_node_id": "10:1",
          "frame_width": 1440,
          "frame_height": 2400,
          "sections": [
            {
              "section_id": "hero-1",
              "node_id": "20:1",
              "selector": "[data-geo-section=\"hero-1\"]",
              "box": { "x": 0, "y": 0, "width": 1440, "height": 720 },
              "elements": [
                {
                  "element_id": "hero_heading",
                  "node_id": "20:2",
                  "selector": "[data-geo=\"hero-heading\"]",
                  "role": "text",
                  "align_anchor": "left",
                  "assert": ["position-x", "position-y", "height"],
                  "box": { "x": 120, "y": 180, "width": 560, "height": 96 }
                }
              ],
              "alignment_groups": [
                {
                  "group_id": "hero_text_left",
                  "edge": "left",
                  "element_ids": ["hero_heading", "hero_body", "hero_cta"]
                }
              ],
              "gaps": [
                {
                  "gap_id": "heading_to_body",
                  "axis": "vertical",
                  "from": "hero_heading",
                  "to": "hero_body",
                  "value": 24
                }
              ]
            }
          ]
        }
      }
    }
  ]
}
```

### Rules

- **`source` must be `figma-metadata`.** Every number comes from the Figma node
  metadata that already carries exact `x`/`y`/`width`/`height`. Hand-typed
  geometry defeats the manifest's whole purpose and the validator rejects any
  other source value.
- **Section boxes are frame-relative. Element boxes are section-relative.**
  A section moving down the page because something above it grew is not an
  element failure, and the comparator must not report it as one.
- **`frame_width` must be the viewport's canonical width** — 1440 desktop,
  768 tablet, 375 or 390 mobile — because the comparison is only meaningful at
  the width the frame was designed at.
- **Every section and element carries a `selector`** that matches exactly one
  DOM node on the built route. Selectors are unique within a section. Prefer a
  stable hook the implementation owns, such as
  `[data-geo="hero-heading"]`, over a class chain that restyling will break.
- **`route_id` and `section_id` must exist** in `routes.json` and
  `sections.json`. The manifests describe the same package, not a parallel one.
- **`alignment_groups` record shared edges**, which the design implies and
  which the per-element boxes alone will not catch: three elements can each sit
  inside their position tolerance and still form a visibly ragged left edge.
- **`gaps` record the spacing that must hold** between two siblings, so a
  changed margin is reported as a gap, not as two unrelated position deltas.
- **`tolerance_px` on an element** overrides the comparator's position and size
  tolerance for that element. Use it for genuinely elastic content, and record
  why in the section's package notes.
- **`assert` names which checks the extraction can actually support**, as a
  subset of `position-x`, `position-y`, `width`, `height`. It defaults to all
  four. A Figma text layer's box is its text frame, and that is not always the
  DOM block box: a hug-width layer measures the glyphs while its DOM
  counterpart fills the column, so asserting that width fails a correct build.
  Drop `width` for hug-width text layers. Do not raise `tolerance_px` to paper
  over a check the extraction cannot support; that weakens the checks that do
  work on the same element.
- **`align_anchor`** is `left` (default), `center`, or `right`, and selects the
  point the `position-x` check compares. A centered heading has a different box
  width in Figma than in the DOM but the same center, so `center` is the only
  anchor that holds for it.

### Extraction

Pull the node tree for each route frame at each viewport and record the
absolute box of every section-level child, then the boxes of the elements the
implementation will build as distinct nodes: headings, body blocks, media,
controls, and any element whose indent or alignment is load-bearing. Subtract
the section origin from each element box before writing it. Do not record every
leaf node; record the ones a fix card would ever have to talk about.

## `copy.json`

Schema `next-theme-figma/copy/v1`. The verbatim text inventory, extracted from
the Figma text layers.

```json
{
  "schema_version": "next-theme-figma/copy/v1",
  "project": "example-store",
  "source": "figma-text-layers",
  "extracted_at": "2026-01-01T00:00:00.000Z",
  "strings": [
    {
      "copy_id": "hero_heading",
      "section_id": "hero-1",
      "node_id": "20:2",
      "role": "heading",
      "text": "Everything your morning needs"
    }
  ],
  "allowed_deviations": [
    {
      "deviation_id": "returns-legal-line",
      "text": "Free returns within thirty days, no questions asked.",
      "reason": "Merchant legal copy added after the Figma export.",
      "approved_by": "operator"
    }
  ]
}
```

### Rules

- **`source` must be `figma-text-layers`.** Retyped copy is how invented copy
  enters a build in the first place.
- **`text` is verbatim**, including punctuation and capitalization. The lint
  normalizes smart quotes, dashes, HTML entities, and whitespace runs, so those
  differences never register as drift; anything else does.
- **`role`** is one of `heading`, `body`, `label`, `cta`, `legal`, `alt`.
- **An allowed deviation carries exactly one of `text` or `pattern`, plus a
  `reason` and an `approved_by`.** A deviation without a recorded reason is
  indistinguishable from drift someone silenced.

## Downstream gates

`next-theme-dev` runs both manifests as gates:

```bash
# Geometry: emit the probe, measure in a browser, compare.
node <next-theme-dev skill dir>/scripts/assert-geometry.mjs probe \
  --manifest <package>/geometry.json --route home --viewport desktop \
  --out /tmp/geometry-probe.js
node <next-theme-dev skill dir>/scripts/assert-geometry.mjs compare \
  --manifest <package>/geometry.json --route home --viewport desktop \
  --boxes /tmp/geometry-boxes.json --report ./qa-output/geometry-desktop.json

# Copy: diff the built templates against the inventory.
python3 <skill-dir>/scripts/copy-lint.py \
  --package <package> --templates partials --templates templates
```

Both exit non-zero on failure and write machine-readable reports. Geometry
assertion runs **before** pixel scoring and gates the fix round; percentage
pixel mismatch is telemetry.
