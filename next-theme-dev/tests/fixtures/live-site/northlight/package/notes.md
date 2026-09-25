# Northlight fixture handoff notes

## Summary

Synthetic one-page storefront used to test the live-site handoff format. The
source page, its media, and this package are made up for the test suite.
Captured with the next-theme-design capture script in headless Chromium
through Playwright at 1440x900 (DPR 1), 768x1024 (DPR 1), and 390x844 (DPR 2).
Capture records were filed with next-theme-design's design-package.py.

## Evidence origin

Every geometry box, copy string, style value, and extracted behavior comes
from the capture records in captures.json (fresh extraction). Behaviors marked
observed record their method. Target-side facts in the divergence ledger are
the fixture operator's statements.

## Inferred entries

- `style:hero-note-shadow-desktop`: see its reason in the package.

## Blocked sections and next actions

None: every intended section is ready to build.

## Limits of automated capture

- Headless Chromium blocks autoplay by default; the runner allowed it so the
  motion record shows playback.
- The capture script measures the page; it does not read text drawn inside
  video frames. Frame claims come from a media-frame capture and are marked
  observed.

## Handoff to next-theme-dev

Validate with `design-handoff.py validate`, build ready sections, and list
blocked ones in the handback.
