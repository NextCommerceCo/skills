# Geometry, Copy, And Readback Gates

Read this before running a fidelity round or pushing to a theme. It covers the
three deterministic gates this skill ships and the frozen-surface test pattern
that protects work already shipped.

## Why these gates exist

A fidelity loop scored on **mean per-section pixel mismatch** plateaus at a
"close enough" that is not. A text block indented 40px too far costs roughly
0.3% of a section's pixels and reads as plainly wrong to anyone looking at the
page. Rounds spent chasing a percentage down converge on nothing, while the
misses a human notices in one second survive to acceptance.

So the ordering is fixed:

1. **Geometry assertion** — per-element boxes, in pixels, pass or fail.
2. **Copy lint** — every built string is in the handoff inventory.
3. **Post-push readback** — the store actually serves what was pushed.
4. **Pixel scoring** — telemetry. It never gates a round on its own.

A fix round is authorized by a geometry failure, and its card cites the crop
file **and** the manifest numbers for that element. The artifact wins over
anyone's recollection of the frame.

## 1. Geometry assertion

`scripts/assert-geometry.mjs` compares rendered DOM boxes against the
`geometry.json` boxes extracted from Figma metadata. It reports per-element
deltas: position within the section, size, shared-edge alignment, and sibling
gaps.

It does not launch a browser. `probe` emits a snippet to evaluate with whatever
browser capability the environment already has; `compare` reads the resulting
JSON. This keeps the skill free of a browser-automation dependency, and makes
every check runnable offline against saved measurements.

```bash
SKILL=<next-theme-dev skill dir>
PKG=<handoff package dir>

# 1. What does the manifest map?
node "$SKILL/scripts/assert-geometry.mjs" selectors \
  --manifest "$PKG/geometry.json" --route product --viewport desktop

# 2. Emit the measurement snippet.
node "$SKILL/scripts/assert-geometry.mjs" probe \
  --manifest "$PKG/geometry.json" --route product --viewport desktop \
  --out ./qa-output/geometry-probe.js

# 3. Load the route at the frame width, evaluate the snippet, save its JSON
#    output to ./qa-output/geometry-boxes.json.

# 4. Compare.
node "$SKILL/scripts/assert-geometry.mjs" compare \
  --manifest "$PKG/geometry.json" --route product --viewport desktop \
  --boxes ./qa-output/geometry-boxes.json \
  --report ./qa-output/geometry-product-desktop.json
```

Default tolerances: 8px position and size at desktop and tablet, 6px at mobile,
4px for shared-edge alignment and sibling gaps. Alignment is deliberately
tighter than position: elements can each sit inside the position tolerance and
still form a visibly ragged edge. Override per run with
`--position-tolerance`, `--size-tolerance`, `--alignment-tolerance`,
`--gap-tolerance`, or per element with `tolerance_px` in the manifest.

`tolerance_px` relaxes **position and size only**. Alignment and gap keep the
run's tolerances, because an element being elastic does not license it to break
a shared edge or a designed gap, and those are the contracts a loosened element
is most likely to break. If an element genuinely should leave a shared edge,
take it out of the alignment group rather than raising its tolerance.

An element can also narrow what is asserted. `assert` names the subset of
`position-x`, `position-y`, `width`, and `height` the extraction supports, and
`align_anchor` (`left`, `center`, `right`) selects the point the horizontal
position check compares. Both exist because a Figma text layer's box is its
text frame: a hug-width layer measures its glyphs, and a centered layer has a
different width from its DOM counterpart but the same center. Those are
extraction facts, recorded in the manifest, not tolerances to be loosened.

Measure at the frame width. A width mismatch is refused unless
`--scale-mode fit` is passed, which scales the manifest by
`measured_width / frame_width`.

Failures name the element, the check, the expected and measured pixel values,
and the delta. A selector that matches zero or several nodes is a failure, not
a skip.

## 2. Copy lint

`next-theme-figma/scripts/copy-lint.py` diffs the built templates against
`copy.json`. Run it in the builder gate and again in any repair gate, before
the work is reported done.

```bash
python3 <next-theme-figma skill dir>/scripts/copy-lint.py \
  --package "$PKG" --templates partials --templates templates \
  --report ./qa-output/copy-lint.json
```

It ignores template expressions, comments, script and style bodies, URLs, and
asset paths, and normalizes smart quotes, dashes, entities, and whitespace. It
checks rendered text, DTL `|default:` literals, and the `alt`, `title`,
`placeholder`, and `aria-label` attributes. Copy that is genuinely not from the
design goes in the manifest's `allowed_deviations` with a reason, never into a
lint exclusion. Add `--require-coverage` to also fail on manifest strings that
no template built.

## 3. Post-push readback

`scripts/readback-assert.py` runs immediately after every `ntk push` and
answers, in seconds, what a scoring round would otherwise discover much later:
do the routes return 200, is the served `assets/main.css` byte-identical to the
committed file, did the mapped sections render, and is the page a plausible
height rather than a collapsed shell.

```bash
python3 "$SKILL/scripts/readback-assert.py" \
  --expect ./qa-output/readback-expectations.json \
  --repo-root . --report ./qa-output/readback.json
```

Expectations file, schema `next-theme-dev/readback-expectations/v1`:

```json
{
  "schema_version": "next-theme-dev/readback-expectations/v1",
  "theme_id": "42",
  "routes": [
    {
      "route_id": "product",
      "url": "https://example.29next.store/products/example/?preview_theme=42",
      "expect_status": 200,
      "expect_section_count": 2,
      "expect_content_length": 5200,
      "section_markers": [
        { "section_id": "product-main", "marker": "data-section=\"product-main\"" },
        { "section_id": "reviews", "marker": "data-section=\"reviews\"" }
      ]
    }
  ],
  "assets": [
    { "served_url": "https://cdn.example.test/assets/main.css",
      "committed_path": "assets/main.css" }
  ]
}
```

`expect_content_length` is the rendered text length of a known-good capture,
not a layout measurement. It is a proxy for "the page has content", and it is
what separates a fully rendered route from one that returns 200 with every
section empty. Take the baseline from a good push and keep it in the repo.
`--offline-dir` replays captured responses, which is how the gate is tested
without a store.

### Push the theme's settings from the first push

The preview theme receives the theme's own `configs/settings_data.json` from
the **first** push of a run — merged over the live store's settings, unpublished
target only, never the active theme. A preview rendered without it exercises the
`{% else %}` / `|default:` fallback branch of every settings-driven template,
so the settings path itself is never tested. In a real run that hid a
settings-path defect for two entire phases: a `split:"\n"` filter argument that
Django never unescapes (template string literals only unescape `\"` and `\\`),
which surfaced as a collapsed list only when the settings file was finally
pushed after closure. A settings path that is never rendered is a path that was
never tested — and the readback gate above can only check what the preview
actually renders.

## 4. Frozen-surface tests

When a run touches a theme that already has shipped surfaces, the risk is not
the new work: it is a seat reusing the nearest existing selector and silently
restyling a finished page. That happened in a real run, was caught by a human
reviewer rather than a gate, and cost a repair round.

Pin the shipped surfaces with a sha-256 test:

```python
"""Frozen surfaces: templates and CSS blocks that shipped in an earlier phase.

A change here is either a regression or a deliberate, carded decision. Either
way it must be seen, so the recorded hash is updated in the same commit that
changes the surface, never separately.
"""

import hashlib
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FROZEN_FILES = {
    "partials/header.html": "b2c3...",
}

FROZEN_CSS_BLOCKS = {
    # Selector prefix -> sha256 of every rule that starts with it.
    ".site-header": "9f10...",
}


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FrozenSurfaceTest(unittest.TestCase):
    def test_frozen_files_are_unchanged(self):
        for path, expected in FROZEN_FILES.items():
            with self.subTest(path=path):
                actual = sha256_text((ROOT / path).read_text(encoding="utf-8"))
                self.assertEqual(
                    actual, expected,
                    f"{path} is a frozen surface from an earlier phase. If this "
                    f"change is intended, name the surface in the card and update "
                    f"the hash in this commit.",
                )

    def test_frozen_css_blocks_are_unchanged(self):
        css = (ROOT / "css" / "input.css").read_text(encoding="utf-8")
        for prefix, expected in FROZEN_CSS_BLOCKS.items():
            with self.subTest(prefix=prefix):
                rules = re.findall(
                    r"^\s*" + re.escape(prefix) + r"[^{]*\{[^}]*\}",
                    css,
                    re.MULTILINE,
                )
                self.assertTrue(rules, f"no rules found for {prefix}")
                self.assertEqual(sha256_text("\n".join(rules)), expected)
```

Rules for the pattern:

- **Freeze the surface, not the file, when the file is shared.** A header
  partial is a file; a shared CSS rule that several pages depend on is a block.
  Freeze whichever unit a seat could plausibly reach for.
- **The card names the frozen surfaces and the gate that protects them.** A
  seat that knows a surface is frozen scopes its new selector instead of
  editing the shared one.
- **The hash moves in the commit that changes the surface.** A separate
  "update the hashes" commit turns the gate into paperwork.
- **Record the commit each hash was taken at** in a comment, so a later reader
  can diff the surface against the phase that shipped it.
