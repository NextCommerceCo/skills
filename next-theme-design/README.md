# Theme Live-Site Handoff

Prepares an existing web page, such as a merchant's landing page, to be
rebuilt as a Next Commerce storefront. It records the page as it really looks
and behaves at desktop, tablet, and phone widths, and writes a handoff package
that [Theme Development](../next-theme-dev/) checks and builds from.

This skill does **not** write theme code, and it does not change your store.
It sits before implementation. If your design is a Figma file rather than a
live page, use [Theme Figma Handoff](../next-theme-figma/) instead.

What you get, in one package folder:

- Screenshots of every page at 1440, 768, and 390 pixels wide, each with a
  record of exactly how and when it was taken.
- Every section of the page, in order, with a decision about how to rebuild
  it and, for the Spark theme, which Spark section it maps to.
- Measured positions and sizes, fonts, colors, and spacing, so the rebuilt
  page can be checked against the original instead of judged by eye.
- Every piece of text on the page, with a decision for each: keep it, replace
  it with approved wording, leave it out, or hold it until someone decides.
- How the page moves and responds: animation timing, videos, sticky bars,
  what changes on a phone, and where each button leads.
- A list of everything the page promises that the store must actually
  support, such as prices, bundles, shipping offers, coupon codes, and claims
  that need proof, kept apart from the text so none of it is copied over by
  accident.

| Example finding | What the package records |
|---|---|
| A banner promises a delivery or shipping offer | A difference to settle: the store's real shipping setup decides the wording |
| A video shows a claim in one frame | The video, the playback time, a screenshot of that frame, and the claim held until it has proof |
| A heading sits 40 pixels lower on phones | The measured position at each width |

> [!IMPORTANT]
> The page's owner and whether you may reuse its images and text are recorded
> as **your** statement. If you say nothing, the package keeps links and
> screenshots only and downloads no images or videos from the page.

## What You Need

- **The page address** (or the page's HTML files with their images).
- **Which store, theme, and theme folder** the rebuild is for.
- **Who owns the page and whether its images and text may be reused.**
- **A browser your assistant can drive** that can set an exact window size and
  save full-size screenshots. The skill includes a small script that runs in
  that browser; it does not install one.
- **Python 3.10 or newer.**

## Install

> [!IMPORTANT]
> Theme Figma Handoff, Theme Live-Site Handoff, and Theme Development work as
> a set: the two handoff skills prepare a design package, and Theme
> Development checks it and builds the theme. Install all three. The installer
> adds one named skill at a time, so run it once for each:
>
> - `./skills.sh install <target> next-theme-figma`
> - `./skills.sh install <target> next-theme-design`
> - `./skills.sh install <target> next-theme-dev`
>
> Replace `<target>` with `claude`, `codex`, `agents`, or `all`. The
> [repo README](../README.md) covers other ways to install. If one of the three
> is missing, the assistant stops and tells you which one to install.

## How to Use

Ask your AI assistant something like:

> Use next-theme-design to prepare this landing page for a Spark rebuild on my
> store. Here's the link. I own the page and its images may be reused.

The workflow: record your intake answers → capture the page at the three
widths → map its sections → record measurements, text decisions, styles,
media, and behavior → list what the store must support → check the package.
The check reports whether the package is complete and which sections are
ready to build. Anything still waiting on a decision is listed with what
needs to happen next.

## Safety

- Only looks at the page. It never fills in forms, adds to a cart, starts a
  checkout, or gets around a block. Anything it could not see is recorded as
  a gap.
- Never changes your store, its offers, coupons, or shipping, and never
  uploads or publishes a theme.
- Prices, product details, and checkout links always come from your store,
  not from the page being copied.
- A gap never turns into made-up content: the builder either gets a decision
  or leaves that part out.
