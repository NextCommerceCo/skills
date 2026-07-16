# Theme Figma Handoff

Prepares a Figma storefront design for implementation on Next Commerce. It
treats the Figma file as a precise source of truth, not loose inspiration:
every section of every page is inspected, classified, and documented, so the
implementation that follows is faithful to the design instead of a best guess.

This skill does **not** write theme code. It sits before implementation:
run it first, then hand its output to [Theme Development](../next-theme-dev/),
which does the actual building.

What you get — a complete handoff package containing:

- A page-by-page map of the design, with every section classified: rebuild it
  properly in code, export it as an image, use a live platform component, and
  so on.
- A list of every image and graphic to export, with sizes and formats decided.
- A record of every place the real storefront should intentionally differ from
  the Figma picture — live product galleries, variant pickers, cart state, and
  app-injected content that a static design can't show.
- Reference screenshots of the design at desktop, tablet, and phone sizes.
- A build-order recommendation, a list of unresolved design questions, and a
  completed quality checklist.

## What You Need

- **The Figma design link**, with permission to view it.
- **Figma access for your assistant** — via a Figma connector or the Figma
  API — when it needs to inspect the design up close or export images.
- **Node.js installed** — for the skill's bundled helper tool; your assistant
  runs it for you.
- **Context on the target** — which store and theme this is for, and a preview
  link if you're comparing against something already built.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Use next-theme-figma to prepare this Spark storefront Figma design for
> implementation. Here's the link.

The workflow: check the design is complete enough to start → map every page
and section → classify each section → decide every asset export → record every
intentional difference from the design → capture reference screenshots →
assemble and validate the package. The finished package is what
[Theme Development](../next-theme-dev/) builds from.

## Safety

- Looks at Figma and your storefront without changing either — the only output
  is the handoff package saved to your computer.
- Hard stop on screenshot-heavy pages: a real storefront is never built out of
  big flat images of the design without your explicit approval, because text,
  links, buttons, search visibility, and live product data would all be lost.
- The package is checked strictly before it's considered done — placeholder or
  unfinished entries fail the check until resolved or explicitly signed off.
