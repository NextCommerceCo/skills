# Tokens Manifest

Read this when producing or validating the `tokens.json` member of a handoff
package. It inventories Figma variables, preserves disagreements between the
two extraction sources, and records how each value should reach the theme.

Strict validation requires this manifest when `figma-handoff.json` has
`mode: implementation-handoff`. In other modes its absence is a warning.

## Schema

Schema `next-theme-figma/tokens/v1`:

```json
{
  "schema_version": "next-theme-figma/tokens/v1",
  "project": "example-store",
  "source": "figma-variables",
  "extracted_at": "2026-01-01T00:00:00.000Z",
  "sources": {
    "variable_defs": { "node_ids": ["471:1219"] },
    "design_context": { "node_ids": ["471:1219", "471:1300"] }
  },
  "tokens": [
    {
      "token_id": "color.text.primary",
      "figma_name": "color/text/primary",
      "collection": "color",
      "type": "color",
      "value": "#0F172A",
      "modes": { "default": "#0F172A" },
      "observed": [
        { "source": "variable_defs", "value": "#0F172A" },
        {
          "source": "design_context",
          "value": "#0f172a",
          "node_id": "471:1219"
        }
      ],
      "target": {
        "kind": "theme-setting",
        "setting_id": "body_text_color",
        "css_var": "--body-text-color"
      },
      "figma_node_ids": ["471:1219"],
      "notes": ""
    }
  ]
}
```

## Field Rules

- `source` must be exactly `figma-variables`. The validator reports:
  `tokens.json: source must be one of figma-variables (tokens are extracted from Figma variables, never transcribed by hand)`.
- `sources` is optional provenance for the nodes queried through
  `get_variable_defs` and `get_design_context`. When present, it must be an
  object.
- `tokens` is required and must be an array. An empty array reports
  `tokens.json: no tokens recorded` as a strict error or non-strict warning.
- Every `tokens[]` member must be an object; anything else reports
  `tokens.json: token entry must be an object`.
- `token_id` is a required, non-empty string and must be unique. A missing or
  blank ID reports `tokens.json: token missing token_id`; duplicates report
  `<label>: duplicate token_id`. A token's label is
  `tokens.json: <token_id or 'token'>`.
- `figma_name` is a required, non-empty string. Duplicate names and conflicting
  values follow the conflict rules below.
- `collection` is an optional string.
- `type` is required and must be `color`, `dimension`, `font-size`, `radius`,
  or `font-family`. An invalid type reports `<label>: invalid type "<x>"`.
- `value` is required and must parse for its type. A failure reports
  `<label>: value "<v>" does not parse as <type>`.
- `modes` is an optional object. Every mode value must parse for the token type.
- `observed` is an optional array. Each entry has a `source`, `value`, and
  optional `node_id`. The source must be `variable_defs` or `design_context`,
  and every value must parse for the token type.
- `target` is required and must be an object. Its `kind` must be
  `theme-setting`, `css-custom-property`, `one-off`, or `unmapped`.
  - `theme-setting` requires a `setting_id` matching
    `/^[a-z][a-z0-9_]*$/`; otherwise validation reports
    `<label>: theme-setting target requires setting_id`. Its optional `css_var`
    must match `/^--[a-z0-9-]+$/`.
  - `setting_value` is optional and must be a string. Use it when the setting
    receives a named or nearest option instead of the literal token value.
  - `css-custom-property` requires a `css_var` matching
    `/^--[a-z0-9-]+$/`;
    otherwise validation reports
    `<label>: css-custom-property target requires css_var`.
  - `one-off` and `unmapped` require no additional target fields.
- `figma_node_ids` is an optional array of strings.
- `notes` is an optional string.

For a Spark package, the pinned Style settings are the ones below, taken from
Spark's `configs/settings_schema.json` and the `:root` block in
`layouts/base.html` after the Style settings landed. An existing select setting
must receive one of its options. The effective value is `target.setting_value`
when present and the token `value` otherwise. Brand primary and accent are not
theme settings: `store.branding.primary_color` and `store.branding.accent_color`
from the dashboard Branding panel write `--primary-color` and `--accent-color`.

| Spark setting | Type | Options | Default | Custom property |
|---|---|---|---|---|
| `font_body` | text | — | `""` | `--font-body` |
| `font_header` | text | — | `""` | `--font-header` |
| `body_text_color` | color | — | `""` | `--body-text-color` |
| `body_header_color` | color | — | `""` | `--body-header-color` |
| `body_link_color` | color | — | `""` | `--body-link-color` |
| `body_bg_color` | color | — | `""` | `--body-bg-color` |
| `border_color` | color | — | `""` | `--border-color` |
| `radius_control` | select | `0`, `4px`, `8px`, `12px`, `16px` | `4px` | `--control-radius` |
| `radius_card` | select | `0`, `4px`, `8px`, `12px`, `16px` | `0` | `--card-radius` |
| `section_padding` | select | `compact`, `default`, `roomy` | `default` | `--section-padding-y` (2rem / 3rem / 5rem) |
| `content_gap` | select | `tight`, `default`, `loose` | `default` | `--content-gap` (1rem / 1.5rem / 2rem) |
| `container_max_width` | select | `1120px`, `1280px`, `1440px` | `1280px` | `--container-max` |
| `heading_scale` | select | `small`, `default`, `large` | `default` | `--heading-scale` (0.875 / 1 / 1.125) |
| `body_size` | select | `15px`, `16px`, `17px`, `18px` | `16px` | `--body-size` |

Spark's base layout already turns each select into its custom property, so a
token that lands on an existing Spark id needs only the setting seed; the
implementation adds no second `:root` line for it.

An invalid effective value reports:

`<label>: "<v>" is not an option of Spark setting <setting_id> (<opts joined by ', '>); set target.setting_value to the nearest option or map to css-custom-property`

Unknown setting IDs are allowed because implementation may add a setting.

## Canonical Namespace And Aliases

Matching is exact and case-sensitive after surrounding whitespace is trimmed.
The validator uses this table only to classify each `figma_name` as canonical,
alias, or unknown for the names segment of the PASS line. It never rewrites
`figma_name`. Unknown names remain valid and count as `unknown`.

| Canonical `figma_name` | Aliases | Type | Spark target |
|---|---|---|---|
| `color/brand/primary` | `brand/primary` | color | `css-custom-property` `--primary-color`; dashboard Branding wins |
| `color/brand/secondary` | `brand/secondary` | color | `one-off` or `css-custom-property`; no setting |
| `color/brand/accent` | `brand/accent` | color | `css-custom-property` `--accent-color`; dashboard Branding wins |
| `color/brand/whitespace` | `surface/background`, `surface/bg` | color | `theme-setting` `body_bg_color` |
| `color/text/primary` | `text/primary` | color | `theme-setting` `body_text_color` |
| `color/text/secondary` | `text/secondary` | color | `css-custom-property`; no setting |
| `color/text/inverse` | `text/inverse` | color | `css-custom-property`; no setting |
| `color/border/default` | `border/default` | color | `theme-setting` `border_color` |
| `color/state/success` | `state/success` | color | `css-custom-property` |
| `color/state/warning` | `state/warning` | color | `css-custom-property` |
| `color/state/error` | `state/error` | color | `css-custom-property` |
| `spacing/sectionpadding-small` | — | dimension | `theme-setting` `section_padding`: `compact` |
| `spacing/sectionpadding-medium` | — | dimension | `theme-setting` `section_padding`: `default` |
| `spacing/sectionpadding-big` | — | dimension | `theme-setting` `section_padding`: `roomy` |
| `spacing/contentgap-tiny` | — | dimension | `css-custom-property` |
| `spacing/contentgap-small` | — | dimension | `theme-setting` `content_gap`: `tight` |
| `spacing/contentgap-medium` | — | dimension | `theme-setting` `content_gap`: `default` |
| `spacing/contentgap-big` | — | dimension | `theme-setting` `content_gap`: `loose` |
| `radius/radius-small` | `radius/small` | radius | `theme-setting` `radius_control` or `radius_card` when valid; otherwise `css-custom-property` |
| `radius/radius-medium` | `radius/medium` | radius | `theme-setting` `radius_control` or `radius_card` when valid; otherwise `css-custom-property` |
| `radius/radius-big` | `radius/big` | radius | `theme-setting` `radius_control` or `radius_card` when valid; otherwise `css-custom-property` |
| `font/size-heading1` | — | font-size | `theme-setting` `heading_scale`: `small`, `default`, or `large`; or `css-custom-property` |
| `font/size-heading2` | — | font-size | `theme-setting` `heading_scale`: `small`, `default`, or `large`; or `css-custom-property` |
| `font/size-heading3` | — | font-size | `theme-setting` `heading_scale`: `small`, `default`, or `large`; or `css-custom-property` |
| `font/size-p-small` | — | font-size | `css-custom-property` |
| `font/size-p` | — | font-size | `theme-setting` `body_size` when valid |
| `font/size-p-big` | — | font-size | `css-custom-property` |
| `font/family-heading` | — | font-family | `theme-setting` `font_header` |
| `font/family-body` | — | font-family | `theme-setting` `font_body` |
| `maxw/container` | — | dimension | `theme-setting` `container_max_width` when valid; otherwise `css-custom-property` |
| `maxw/cta` | — | dimension | `css-custom-property` |

The `font/family-*` variables are planned on the design side, so their absence
is valid. The Spark target column is guidance for implementation, not a
validator-enforced mapping. Brand primary and accent stay on the dashboard Branding panel, as noted above.

## Value Parsing

- `color` accepts `#RGB`, `#RRGGBB`, or `#RRGGBBAA` hexadecimal notation,
  case-insensitively, and `rgb()`, `rgba()`, `hsl()`, or `hsla()` functional
  notation with three or four numeric components (percentages allowed; `deg`
  on the hue). `rgb(bogus)` and `rgb()` do not parse.
- `dimension`, `radius`, and `font-size` accept a finite number followed by
  `px`, `rem`, `em`, `%`, `vw`, or `vh`, plus unitless `0`. A bare non-zero
  number is invalid.
- `font-family` accepts any non-empty string.

Conflict comparison trims and lowercases values and expands three-digit hex
colors from `#RGB` to `#RRGGBB`.

## Two-Source Extraction

1. Call `get_variable_defs` on a representative section node and record the
   variable definitions and values.
2. Call `get_design_context` on each relevant node to collect the values used
   at every available breakpoint.
3. Put both results in `observed[]`, using `variable_defs` or
   `design_context` as the source and retaining the node ID when available.
4. If the sources disagree, preserve both observations. Never choose one
   silently, average values, or drop a value.

## Conflicts

A conflict exists when two token entries share a `figma_name` but have
different normalized `value`s, or when an entry's `observed[]` values disagree
with one another or with its `value`. Conflicts are hard errors in strict and
non-strict validation and produce this exact text shape:

`tokens.json: designer-input-needed: <figma_name> carries two values (<v1> from <where1>, <v2> from <where2>); the designer must pick one, the manifest never averages or drops a value`

For two token entries, `<where>` is each `token_id`. When an observation
disagrees with the entry's own `value`, the manifest side is labelled `value`
and the other side by its `observed[].source`, for example
`(#0F172A from value, #FFFFFF from design_context)`. When two observations
disagree with each other, both sides are labelled by source. Two entries with the same `figma_name` and identical
normalized values instead report `<label>: duplicate figma_name "<name>"`.

## Handoff Pointer And Summary

`figma-handoff.json` names this file as `manifests.tokens: "tokens.json"`.
Implementation handoff packages that omit the file fail with
`missing tokens.json: implementation-handoff packages must carry a Figma variables manifest`.
Other modes warn with
`tokens.json not present; add one before promoting this package to implementation-handoff`.
If the pointer is present but the file is missing, validation reports
`figma-handoff.json: manifests.tokens names a missing tokens.json`. If the file
is present under any other pointer value, it reports
`figma-handoff.json: manifests.tokens must be "tokens.json"`.

When the manifest is present and parsed, validation appends this segment to the
PASS line, after roster counts when those are present:

`tokens: <N> total, <n> theme-setting, <n> css-custom-property, <n> one-off, <n> unmapped; names: <n> canonical, <n> alias, <n> unknown`

The names counts classify each `figma_name` against the canonical namespace
table above: an exact canonical match, an exact alias match, or unknown.
