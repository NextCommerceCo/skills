# Developer Workflow

Use this reference to turn Figma source into an implementation-ready handoff package.

## Work One Route Or Section Group At A Time

Do not try to resolve an entire storefront in one fuzzy pass. For each route:

1. Capture route-level frames and available viewports.
2. List section order.
3. Inspect each section's layer tree.
4. Classify sections and assets.
5. Record platform divergences.
6. Capture reference screenshots.
7. Validate the handoff package.

## Fetch Before Deciding

For a section/page with multiple breakpoints, inspect all available breakpoint frames before making layout or asset decisions. Mobile/tablet often reveal stacking order, hidden content, different crops, or controls that desktop does not show.

Do not repeatedly re-fetch Figma during refinement unless the file changed or the saved source is insufficient. Figma API render calls have rate limits; saved screenshots and node IDs should carry the loop.

## Section Classification

Allowed classes:

- `semantic-rebuild`: live HTML/CSS/DTL with real text, links, forms, FAQ, grids, tables, nav/footer, etc.
- `composed-asset`: an intentional `img-group:` export.
- `background-asset`: a `bg:` image behind live content.
- `live-commerce-component`: theme/platform commerce component or live data surface.
- `platform-app-hook`: app/widget/hook slot that must remain available.
- `screenshot-fallback`: static bitmap fallback with explicit approval.

For each section, include:

- Section ID.
- Route.
- Viewport frame names/node IDs.
- Classification and rationale.
- Target theme files/partials if known.
- Asset IDs.
- Platform divergence IDs.
- Behavior notes.
- Responsive notes.
- Unresolved gaps.

## Semantic Rebuild Rules

Use semantic rebuild for:

- Headings, paragraphs, links, CTAs.
- FAQ/accordion copy.
- Tables and comparison grids.
- Forms, inputs, variant controls, add-to-cart.
- Product titles, prices, cards, and collections.
- Header, footer, nav, trust badges with links.

Preserve Figma grouping where it controls spacing. Record actual typography, spacing, and alignment, but leave theme implementation to `next-theme-dev`.

## Asset Rules

Use exported assets for:

- Logos, icons, product art, illustrations, lifestyle photography.
- Intentional composed groups.
- Background fills.

Avoid assets for:

- Text that can render live.
- Buttons and controls.
- Prices or availability.
- Product cards where backend data should drive content.
- Whole pages/sections, unless approved as `screenshot-fallback`.

## Visual Verification Loop

Preflight screenshot capability before implementation handoff. Use this
fallback ladder for each required Figma reference and storefront preview:

1. Reuse a screenshot or browser capability already available to the current
   agent or connected environment.
2. Use the operator's existing local browser, browser developer tools,
   operating system screenshot command, or normal QA utility.
3. Give the operator the exact source/preview URLs and target viewports, then
   ask them to capture and save or attach the images.
4. If none is available, stop or record an explicit accepted gap with owner,
   affected routes, viewports, and rationale.

Do not install or bundle Playwright, Chromium, or another browser automation
package solely for this gate. Do not create or depend on a managed screenshot
service unless the user explicitly requests one. Record the viewport, source
URL or Figma node, timestamp, capture tool or manual owner, and saved image path
for each result. DOM metrics alone are not visual QA.

Capture a real Figma reference PNG and a real storefront preview PNG at a
matching width. Wait for fonts and lazy media before recording the preview.

For every route/section:

1. Save Figma refs for each available viewport.
2. Capture existing preview screenshots if there is an existing theme.
3. Compare Figma versus preview/build at matching widths.
4. Record mismatches by section and viewport.
5. Decide status: `fix-now`, `platform-divergence`, `designer-input-needed`, or `accepted-gap`.
6. Update manifests.
7. Re-check changed viewports.

The handoff package should tell the implementation agent exactly what to build and what not to chase.

## Handoff Into `next-theme-dev`

Only after the package is coherent:

- Load `next-theme-dev`.
- Read the handoff package.
- Implement in the target theme project folder.
- Preserve the recorded theme-family runtime behavior and push only changed files.
- Use the reference screenshots and mismatch list as the QA checklist.

Do not let `next-theme-figma` drift into ntk pushes, DTL rewrites, CSS builds, or commerce behavior implementation. That belongs to `next-theme-dev`.
