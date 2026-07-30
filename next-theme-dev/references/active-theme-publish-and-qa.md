# Active Theme Publish And QA

Use this runbook only when a confirmed `ntk` target is the store's active
theme. It supplements the approval and changed-files-only rules in `SKILL.md`.

## Understand The Risk

A targeted push reduces the number of files touched, but it is not an atomic
deployment and is not guaranteed to be collateral-free. Theme Kit uploads
recognized files sequentially. Files completed before a failure may already be
live, and shared layouts, partials, assets, or settings can affect routes beyond
the page being changed.

## Pre-Publish Gate

Complete every item before asking for final approval:

1. Resolve the actual `ntk` executable and read the version it emits. Do not use
   package metadata from an unrelated system Python to identify a pipx or uv
   installation. This skill's command contract targets Theme Kit 1.2.0.
2. Run `ntk list` for the intended environment and confirm the store, theme ID,
   name, and which theme is Active. Treat those values as one deployment target.
3. Build an exact upload manifest. Confirm every path exists and uses an
   accepted theme path and extension. Theme Kit 1.2.0 can omit invalid explicit
   paths from its upload set, so compare the printed upload count with the
   manifest count.
4. Label each manifest entry route-specific or shared. Call out the routes that
   a shared layout, partial, asset, schema, or script could affect. Exclude
   `configs/settings_data.json` unless the approved task explicitly changes the
   store's saved Theme Editor values.
5. Establish a rollback source for every overwritten file, such as the exact
   prior commit or a backup captured in an isolated location. Do not overwrite
   the working tree to create the backup.
6. Show the operator the environment, store, active theme ID, exact file
   manifest, shared-file blast radius, rollback source, and verification plan.
   Obtain explicit approval for that publish only.

## Publish In Dependency Order

Keep the push bounded to the approved manifest. Where dependencies allow it,
publish in this order:

1. Additive assets.
2. New or changed partials and other dependencies.
3. Route entry templates last.

Do not include unrelated cleanup. If any upload fails or the printed upload
count differs from the approved manifest, stop. Assume earlier files may have
completed, inspect the remote result, and choose either a bounded repair or the
prepared rollback with fresh approval.

## Verify The Served Revision

Verification must test what the storefront actually serves:

1. Use the `.29next.store` network domain with `skip_cache=1` and the exact
   active-theme URL. Use a clean browser context or first visit
   `/?deactivate-theme=true` so an old preview cookie does not select another
   theme.
2. Verify every affected route plus at least one representative unaffected
   smoke route when shared files changed.
3. Inspect `X-Theme-Id`, `X-Theme-Revision`, `X-Theme-Template`, and
   `X-Theme-Cache`. Record the expected and observed theme ID/revision and the
   selected template. These development headers may be absent on a mapped
   production domain.
4. For visual changes, inspect real screenshots at desktop 1440px and mobile
   390px after fonts and lazy media load. Exercise affected interactions rather
   than checking only initial paint.

## Screenshot Fallback Ladder

Use the first available option and record which one produced the evidence:

1. A screenshot or browser capability already available to the current agent
   or connected environment.
2. The operator's existing local browser, browser developer tools, operating
   system screenshot command, or normal QA utility.
3. Ask the operator to capture the supplied exact URLs at the supplied
   viewports and save or attach the images.
4. If none is available, record an explicit accepted visual gap with owner,
   affected routes, viewports, and rationale.

Do not install or bundle Playwright, Chromium, or another browser automation
package solely to satisfy this gate. Do not create or depend on a managed
screenshot service unless the user explicitly requests one. Response headers,
HTML inspection, and DOM metrics are useful diagnostics, but never substitutes
for visual evidence.

## Evidence Record

Record enough information for another operator to reproduce the result:

- UTC publish and verification timestamps.
- Environment, store, active theme ID, and exact URLs tested.
- Approved file manifest, printed upload count, and rollback source.
- Expected and observed `X-Theme-Id`, `X-Theme-Revision`,
  `X-Theme-Template`, and `X-Theme-Cache` values.
- Viewport, screenshot path, and capture tool or manual owner for each image.
- Affected-route checks, unaffected smoke-route checks, interaction results,
  and any accepted gaps.

Do not declare the active-theme publish complete until the served revision,
route checks, and required visual evidence agree with the approved change.
