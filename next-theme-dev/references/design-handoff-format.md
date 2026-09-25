# Design Handoff Package Format (live site, V1)

Read this when you validate, read, or build from a package whose entry file is
`design-handoff.json`. `next-theme-dev` owns this format. `next-theme-design`
produces it from a rendered website or its HTML. The format is meant to become
the single package format for every design source; V1 covers live sites only.
Figma packages keep their own format (`figma-handoff.json`) and validator in
`next-theme-figma`, unchanged.

A package is a directory. Its files record what the source page shows, where
each fact came from, and what the build may and may not use. It separates what
the page shows from what is true or configured in NEXT: prices, offers,
shipping, coupons, and claims are recorded as divergences or gaps, never as
copy the build can reuse without NEXT evidence or an approval.

## Commands

The scaffolder and validator are one stdlib-only Python script:

```bash
python3 <next-theme-dev skill dir>/scripts/design-handoff.py new \
  --out <package dir> --project <slug> --source-url https://example.test/landing/ \
  --routes / --store <store> --repo <theme repo> --theme-family spark \
  --owner "<source owner, as the operator states it>" --reuse not-stated
python3 <next-theme-dev skill dir>/scripts/design-handoff.py validate <package dir> \
  [--report <file>] [--require-ready]
```

`new` writes a blank package and refuses to overwrite one unless `--force` is
passed. `validate` prints two results:

- **STRUCTURE**: `VALID` or `INVALID`. Invalid means a file is missing, a field
  is malformed, or a provenance rule below is broken. Each error names the file
  and entry.
- **READINESS**: `READY` or `NOT READY`, with an entry per section. A
  structurally valid package can still have blocked sections. Each blocked
  section lists its blockers, and any section can list further items to
  surface to the operator before building it, such as `omit` copy decisions.

Exit codes: 0 valid (ready or not), 1 invalid, 2 usage error or a missing
sibling skill, 3 valid but not ready when `--require-ready` is passed.
`--report` writes the same results as JSON
(`next-theme-dev/design-handoff-report/v1`).

For Spark targets the validator reads the Spark section roster from the
sibling `next-theme-figma` skill. If that skill is not installed, it stops,
names the missing skill, and prints its install command.

## Files

| File | Schema ID | What it holds |
|---|---|---|
| `design-handoff.json` | `next-theme-dev/design-handoff/v1` | Entry file: source, rights statement, target, mode, and gaps |
| `captures.json` | `next-theme-dev/handoff-captures/v1` | One record per capture: page state, screenshot, and page-relative boxes |
| `captures/` | | Screenshots (`<capture_id>.png`) and raw capture-script output (`raw/<capture_id>.json`) |
| `routes.json` | `next-theme-dev/handoff-routes/v1` | Source URL, storefront path, theme template, and section order per route |
| `sections.json` | `next-theme-dev/handoff-sections/v1` | Classification, roster mapping, and implementation target per section |
| `assets.json` | `next-theme-dev/handoff-assets/v1` | Each source media item with its state and treatment |
| `platform-divergence-ledger.json` | `next-theme-dev/handoff-divergence/v1` | Where the store must differ from the source, and the decision |
| `viewport-coverage.json` | `next-theme-dev/handoff-viewport-coverage/v1` | Which capture is the reference for each route and width |
| `geometry.json` | `next-theme-dev/handoff-geometry/v1` | Boxes the build must reproduce, for `assert-geometry.mjs` |
| `copy.json` | `next-theme-dev/handoff-copy/v1` | Captured source text and the text the build may show, for `copy-lint.py` |
| `observed-styles.json` | `next-theme-dev/handoff-observed-styles/v1` | Computed values observed on the page |
| `behaviors.json` | `next-theme-dev/handoff-behaviors/v1` | Responsive order, sticky elements, motion, media, and CTA destinations |
| `validation-checklist.md` | | Completion review |
| `notes.md` | | Human notes; must list every inferred entry |
| `media/` | | Downloaded source media, only when reuse is declared |

Compared with a Figma package: `figma-handoff.json` becomes
`design-handoff.json`; Figma node IDs, frame names, and `figma_ref` become
capture references (`capture_id`, `capture_refs`, `source_ref`); `tokens.json`
has no counterpart, because computed values seen on a page are not design
variables; `captures.json` and `behaviors.json` are new. A live-site package
that carries Figma-only files (`figma-handoff.json`, `tokens.json`) or fields
(`figma_*`, `node_id`, `frame_node_id`) fails validation.

## Identifiers and widths

Every `route_id`, `section_id`, and `capture_id` is a lowercase id
(`a-z`, `0-9`, `.`, `_`, `-`). Section ids are unique across the package.

Three widths are required for every route: `desktop` 1440, `tablet` 768, and
`mobile` 390 CSS pixels. Each needs at least one capture with a screenshot, or
a `missing-width` gap that says why.

## `design-handoff.json`

```json
{
  "schema_version": "next-theme-dev/design-handoff/v1",
  "generated_at": "2026-01-01T00:00:00Z",
  "generator": "next-theme-design",
  "project": "example-store",
  "mode": "implementation-handoff",
  "source": {
    "kind": "live-site",
    "urls": ["https://example.test/landing/"],
    "supplied_html": [],
    "captured_on": "2026-01-01",
    "rights": {
      "owner": "not stated",
      "reuse": "not-stated",
      "statement": "",
      "stated_by": ""
    }
  },
  "target": {
    "store": "example-store",
    "repo": "example-theme",
    "preview_url": "",
    "theme_id": "",
    "theme_family": "spark",
    "runtime_contract": "web-components"
  },
  "gaps": [],
  "unresolved_questions": []
}
```

- `mode` is `implementation-handoff` for a package a builder can use, or
  `intake-only` when the source could not be rendered and captured. An
  intake-only package needs only this file and `notes.md`, must name its gaps,
  and is never ready.
- `source.captured_on` is the capture date. Live pages change; the date says
  which version the package describes.
- `source.rights` records the operator's statement about the source. `owner`
  is who the operator says owns the source (`not stated` when they said
  nothing). `reuse` is `not-stated`, `reuse-allowed`, or `reuse-not-allowed`;
  a stated value needs `statement` (the operator's words) and `stated_by`.
  Unless reuse is `reuse-allowed`, the run is reference-only: the package keeps
  source URLs and screenshots and downloads no source media. The skill cannot
  enforce rights; it records what the operator said.
- `target.theme_family` and `target.runtime_contract` follow the same pairs as
  Figma packages: `spark` with `web-components`, `intro-bootstrap` with
  `jquery-core-js`, `custom` with any contract.

### Gaps

A gap records something the package could not capture or decide:

```json
{
  "gap_id": "home-tablet-missing",
  "kind": "missing-width",
  "route_id": "home",
  "section_id": null,
  "viewport": "tablet",
  "reason": "The page served a different layout below 800px that the capture could not load.",
  "next_action": "Capture from a desktop browser at 768px with the redirect disabled.",
  "blocks_build": true,
  "excluded_by": ""
}
```

- `kind` is one of `missing-width`, `extraction`, `input`, `commerce`, `claim`,
  `media`, `behavior`, `asset`, `copy`, `other`.
- `section_id` null means the gap covers every section of the route.
- `blocks_build` true blocks the named section, or every section of the route.
- A `missing-width` gap names its `viewport` and blocks its route's sections
  until the width is captured, or the operator excludes it: record who in
  `excluded_by`, then `blocks_build` may be false.
- A recorded gap never permits inventing the missing content.

## `captures.json`

One record per capture. A capture is one run of the capture script in one
known page state, plus the screenshot taken in that same state. Scrolling,
hovering, or seeking media makes a new capture.

```json
{
  "capture_id": "home-desktop-static",
  "route_id": "home",
  "viewport": "desktop",
  "state": "static",
  "state_detail": "",
  "url": "https://example.test/landing/",
  "viewport_width": 1440,
  "viewport_height": 900,
  "device_pixel_ratio": 1,
  "scroll_x": 0,
  "scroll_y": 0,
  "document_width": 1440,
  "document_height": 3200,
  "browser": "<user agent>",
  "tool": "playwright",
  "captured_at": "2026-01-01T00:00:00.000Z",
  "html_sha256": "0123456789abcdef",
  "readiness": { "fonts": "ready", "images": "ready", "media_metadata": "none" },
  "motion": "paused",
  "media_url": "",
  "media_time_s": null,
  "screenshot": "captures/home-desktop-static.png",
  "screenshot_kind": "full-page",
  "raw_output": "captures/raw/home-desktop-static.json",
  "boxes": {
    "hero": { "x": 0, "y": 120, "width": 1440, "height": 780 },
    "hero::heading": { "x": 120, "y": 280, "width": 600, "height": 112 }
  },
  "gaps": []
}
```

- `state` is `static` (finite animations finished, infinite ones paused,
  media paused), `motion` (nothing paused), `interaction` (after a scroll,
  hover, or similar, described in `state_detail`), or `media-frame` (media
  seeked to `media_time_s`, with `media_url`). Geometry cites `static` or
  `interaction` captures. A `static`, `interaction`, or `media-frame` capture
  with `motion` `running` fails validation.
- `readiness` records whether fonts, images, and media metadata finished
  loading (`ready`), or timed out (`timeout`). A timeout is recorded, not
  hidden.
- `html_sha256` is the first 16 or more hex digits of the SHA-256 of the
  rendered markup. It shows whether the markup changed between runs. It does
  not prove external styles or media are unchanged.
- `screenshot` is a PNG inside the package. Its pixel width must equal
  `viewport_width` times `device_pixel_ratio`: an unscaled screenshot at
  device-pixel resolution. `screenshot_kind` is `full-page` or `viewport`.
  `screenshot` may be null for a capture that only records motion.
- `boxes` are the raw page-relative measurements: bounding rectangle plus
  scroll offset, keyed `<section_id>` and `<section_id>::<element_id>`.
- `raw_output` is the full capture-script output: computed styles, text,
  animations and keyframes, media, images, links, and fixed elements.

## `routes.json` and `sections.json`

```json
{ "route_id": "home", "source_url": "https://example.test/landing/",
  "storefront_path": "/", "theme_template": "templates/index.html",
  "section_order": ["hero", "features"], "status": "ready", "notes": "" }
```

```json
{
  "section_id": "hero",
  "route_id": "home",
  "order": 1,
  "in_scope": true,
  "source_selector": ".hero-banner",
  "capture_refs": { "desktop": "home-desktop-static", "tablet": "home-tablet-static", "mobile": "home-mobile-static" },
  "classification": "semantic-rebuild",
  "classification_rationale": "Live heading, copy, CTA, and product image.",
  "spark_section": "section_hero",
  "roster_status": "shipped",
  "implementation_target": { "template": "partials/section_hero.html", "partials": [], "settings": [] },
  "commerce_surface": "",
  "asset_ids": ["hero-product"],
  "divergence_ids": [],
  "behavior_ids": ["hero-mobile-order"],
  "responsive_notes": ""
}
```

- `classification` is `semantic-rebuild`, `reusable-media`, `composed-asset`,
  `background-asset`, `live-commerce-component`, `platform-app-hook`, or
  `screenshot-fallback` (only with `screenshot_fallback_approved: true`).
- For a Spark target every section carries `roster_status` (`shipped`,
  `unshipped`, `chrome`, or `unmapped`); a live-site package has no Figma frame
  name for `infer-section` to fall back on. `spark_section` must be in the
  roster and agree with its status.
- `in_scope: false` with an `exclusion_reason` removes a section from
  readiness. The package is ready only when every in-scope section is.

## `assets.json`

```json
{
  "asset_id": "hero-product",
  "section_id": "hero",
  "role": "product-image",
  "media_type": "image",
  "source_url": "https://example.test/media/product.png",
  "capture_id": "home-desktop-static",
  "alt": "Product on a desk",
  "natural_width": 560,
  "natural_height": 1060,
  "state": "reference-only",
  "treatment": "replace-with-store-media",
  "notes": ""
}
```

Each asset has one `state`:

| `state` | Needs | Treatment |
|---|---|---|
| `reference-only` | `source_url` and `capture_id`; no local file | `omit`, `rebuild-semantic`, or `replace-with-store-media`. Empty treatment blocks the section. |
| `downloaded` | `reuse-allowed` rights and a `local_path` inside the package that resolves and passes the media checks (known extension, matching file signature, no script in SVG) | `use-downloaded` |
| `replacement-needed` | `target_asset`: the missing asset the store must supply | Blocks the section until supplied |

`media_type` is `image`, `video`, `svg`, `background-image`, `icon`, `audio`,
or `embed`. Never invent a local path to satisfy the validator.

## `platform-divergence-ledger.json`

```json
{
  "divergence_id": "delivery-promise",
  "kind": "commerce",
  "surface": "announcement bar",
  "route_ids": ["home"],
  "section_ids": ["announcement"],
  "capture_ids": ["home-desktop-static"],
  "source_expectation": "The source promises free delivery for a limited time.",
  "platform_behavior": "The store has no free-delivery rule configured.",
  "decision": "needs-approval",
  "implementation_guardrail": "Show delivery copy from a setting; never hardcode a promise.",
  "status": "open",
  "approved_by": "",
  "target_evidence": { "origin": "prior-input", "summary": "Operator notes from an earlier review." }
}
```

- `kind` is `commerce`, `claim`, `media`, `layout`, `platform`, or `other`.
- `decision` is `platform-wins`, `source-wins-with-guardrails`,
  `needs-approval`, or `blocked`. `status` is `open`, `approved`,
  `implemented`, `blocked`, or `accepted-gap`; `approved` and `accepted-gap`
  record `approved_by`.
- An entry is resolved when its decision is `platform-wins` or
  `source-wins-with-guardrails` and its status is `approved`, `implemented`,
  or `accepted-gap`. Otherwise it is unresolved and blocks every section in
  `section_ids`.
- `capture_ids` cite the captures that show the source side.
- `target_evidence.origin` says where the NEXT-side fact comes from:
  `fresh-read-only` (read from NEXT during this run), `prior-input` (an earlier
  audit or the operator's word; context, not current proof), or `none`.

## `geometry.json`

Same shape as the Figma geometry manifest, read by the same
`scripts/assert-geometry.mjs`:

```json
{
  "schema_version": "next-theme-dev/handoff-geometry/v1",
  "project": "example-store",
  "source": "dom-capture",
  "extracted_at": "2026-01-01T00:00:00Z",
  "routes": [{
    "route_id": "home",
    "viewports": {
      "desktop": {
        "capture_id": "home-desktop-static",
        "frame_width": 1440,
        "frame_height": 3200,
        "sections": [{
          "section_id": "hero",
          "capture_id": "home-desktop-static",
          "selector": "[data-geo-section=\"hero\"]",
          "source_selector": ".hero-banner",
          "box": { "x": 0, "y": 120, "width": 1440, "height": 780 },
          "elements": [{
            "element_id": "heading",
            "capture_id": "home-desktop-static",
            "selector": "[data-geo=\"hero-heading\"]",
            "source_selector": ".hero-banner h1",
            "role": "text",
            "box": { "x": 120, "y": 160, "width": 600, "height": 112 }
          }],
          "alignment_groups": [],
          "gaps": []
        }]
      }
    }
  }]
}
```

- `selector` is the hook the built theme must carry. `source_selector` is the
  source page's selector, recorded for provenance.
- Section boxes are relative to the page. Element boxes are relative to their
  section's top-left corner. These are the existing coordinate rules, and what
  `assert-geometry.mjs` compares; the capture record keeps the raw
  page-relative boxes.
- An extracted entry cites a `capture_id` of the same route and viewport in
  `static` or `interaction` state, and its box must match that capture's raw
  box (within 1px; element boxes after subtracting the section origin). An
  element cites the same capture as its section.
- An entry without a capture is accepted only with `"basis": "inferred"`, a
  `reason`, and a line in `notes.md` naming it:
  `geometry:<route_id>/<viewport>/<section_id>` or
  `geometry:<route_id>/<viewport>/<section_id>::<element_id>`.
- `assert`, `align_anchor`, `tolerance_px`, `alignment_groups`, and `gaps`
  mean what they mean in the Figma manifest.

## `copy.json`

```json
{
  "copy_id": "announcement_text",
  "section_id": "announcement",
  "capture_id": "home-desktop-static",
  "capture_key": "announcement::text",
  "viewport": "desktop",
  "role": "label",
  "source_text": "Free delivery this week only",
  "decision": "replace",
  "text": "Delivery options shown at checkout",
  "approved_by": "operator",
  "next_source": "store delivery settings",
  "divergence_id": "delivery-promise"
}
```

- `source_text` is the captured text. Source copy is never inferred: it is
  captured or recorded as a gap. It cites a `capture_id` of the section's
  route; with `capture_key`, the validator checks the text is in what that
  capture recorded.
- `decision` is `reuse`, `replace`, `omit`, or `unresolved`. `text` holds only
  the text the build may show:

| `decision` | `text` | Also needs |
|---|---|---|
| `reuse` | exactly `source_text` | `reuse_basis`; a commerce fact (`commerce_fact: true`) also needs `next_evidence` |
| `replace` | the approved replacement | `approved_by` or `next_source`; the approval never counts as captured source text |
| `omit` | `""` | nothing; surfaced to the operator before build |
| `unresolved` | `""` | blocks the section |

`scripts/copy-lint.py` in `next-theme-figma` treats `strings[].text` as the
list of permitted build text, so it enforces these decisions unchanged: a
build containing an omitted or unresolved source string fails as drift, and a
build using the approved replacement passes. `allowed_deviations` works as in
the Figma manifest.

## `observed-styles.json`

```json
{
  "style_id": "hero-heading-desktop-font-size",
  "route_id": "home",
  "viewport": "desktop",
  "section_id": "hero",
  "capture_id": "home-desktop-static",
  "capture_key": "hero::heading",
  "source_selector": ".hero-banner h1",
  "property": "font-size",
  "value": "56px",
  "category": "font"
}
```

These are computed values seen on the page, not design variables. Entries
must not carry `variable`, `token`, or `css_var` fields; the builder decides
whether a value becomes a Theme setting, a custom property, or section CSS.
With `capture_key`, the value must equal the computed value in that capture.
Provenance rules match geometry: a capture of the same route and viewport, or
`"basis": "inferred"` with a `reason` and a `style:<style_id>` line in
`notes.md`. `category` is `color`, `font`, `spacing`, `size`, `radius`,
`shadow`, `border`, `layout`, `motion`, or `other`.

## `behaviors.json`

```json
{
  "behavior_id": "hero-dots",
  "kind": "motion",
  "route_id": "home",
  "section_id": "hero",
  "viewport": null,
  "capture_ids": ["home-desktop-motion", "home-desktop-static"],
  "trigger": "page load",
  "observed": "Three dots under the heading fade in turn, 1.2 s each, starting 0.4 s apart.",
  "evidence": "extracted",
  "details": { "animation_name": "dot-fade", "duration_ms": 1200, "delays_ms": [0, 400, 800] }
}
```

- `kind` is `responsive-order`, `sticky`, `motion`, `media`,
  `cta-destination`, `accessible-text`, `interaction`, or `other`.
- `evidence` is `extracted` (read by the capture script) or `observed` (seen
  by a person or agent; `method` says how). Motion driven by JavaScript,
  keyframes the script cannot read, and similar details are observed or
  recorded as gaps; one unavailable detail does not stop the rest.
- Every cited capture must exist and match the behavior's route, and its
  viewport when one is given. Extracted motion cites a `motion` capture.
- A claim shown in a video frame goes in `claims`: `text`, `media_url`,
  `time_s` (playback time), and `capture_id` of a `media-frame` capture with a
  screenshot at that time (within 0.5 s). Link it to a ledger entry with
  `divergence_id`. A screen recording is optional supporting evidence.

## Readiness

A section is blocked when any of these applies:

- a gap with `blocks_build: true` names it, or names its route with no section;
- a `missing-width` gap for its route is not excluded;
- one of its copy strings is `unresolved`;
- one of its assets is `reference-only` without a treatment, or
  `replacement-needed`;
- a divergence entry listing it is unresolved.

Before building a section, surface to the operator its `omit` and
`unresolved` copy, its assets without a treatment, and its unresolved
divergence entries; the validator lists them per section. Build ready sections
and list blocked ones in the handback. The package counts as ready only when
every in-scope section is.
