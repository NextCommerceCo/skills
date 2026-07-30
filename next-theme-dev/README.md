# Theme Development

Gives your AI assistant deep, working knowledge of Next Commerce storefront
themes — the Spark theme, Intro Bootstrap, and custom themes — so it can
build, change, and debug your storefront the way an experienced platform
developer would.

It knows:

- How the platform's page templates work and fit together.
- How theme settings are structured so merchants get a clean Theme Editor.
- The theme toolkit (ntk) workflow for syncing theme files with your store.
- How to publish a small, reviewed change to an active theme with a file
  manifest, rollback source, and storefront verification.
- What must be rendered on the server versus loaded live in the browser
  (cart contents, login state) because of the platform's page caching.
- The Spark theme's styling pipeline and its non-obvious build steps.
- How to debug stubborn CSS by checking computed styles, ancestor opacity,
  generated content, and selector scope before adding another override.
- How to collect desktop and mobile visual evidence without requiring a new
  browser automation dependency.
- Ready-made recipes: brand colors and fonts, custom pages, product page
  redesigns, side cart changes, navigation, translations.
- The hard-won gotchas that silently break themes.

**Starting from a Figma design?** Run
[Theme Figma Handoff](../next-theme-figma/) first. It turns the design into a
precise implementation package; this skill then does the building. Using the
two together avoids the assistant guessing at what the design intends.

## What You Need

- **Python 3.10 or newer** and **NEXT Theme Kit 1.2.0**. Your assistant checks
  the actual `ntk` executable in use, which avoids confusing a pipx or uv
  installation with a separate system Python package.
- **A Next Commerce store** with an API key that has theme permissions,
  created as an OAuth app under **Storefront admin > Settings > API Access**.
- **The theme's folder on your computer**, with its connection settings file
  pointing at your store and theme. Your assistant sets this up if it's
  missing, and can look up the theme ID for you.
- For the Spark theme, one extra styling tool is needed — the theme's own
  setup command installs it, and your assistant handles that.
- For visual changes, use a browser or screenshot tool you already have, or
  capture the exact URLs and viewports manually when the assistant asks. The
  skill does not install Playwright or create a screenshot service unless you
  explicitly request one.

See the public
[Theme Kit guide](https://developers.nextcommerce.com/docs/storefront/themes/theme-kit)
for installation, OAuth app setup, and the current command reference.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Work inside the theme's folder and just describe what you want:

> Add a free-shipping progress bar to the side cart on my Spark theme.

> The product page price stopped updating when I select a variant — debug it.

> Update this active theme, but show me the exact files, rollback source, and
> verification plan before pushing anything.

The skill first identifies which theme family you're on and keeps every change
consistent with how that theme is built.

## Safety

- `ntk push` and `ntk watch` change the selected remote theme, whether it is an
  unpublished preview or the active storefront theme. The skill shows you the
  store, environment, theme ID, status, and exact file scope before asking for
  approval.
- Active-theme changes use an additional publish checklist. It requires a
  rollback source, compares the printed upload count with the approved file
  manifest, and verifies the served theme revision plus affected and
  representative unaffected routes.
- Your saved Theme Editor settings are treated as merchant-owned state — they
  are only touched when the task genuinely requires it.
- Verification happens on the store's direct address, sidestepping the
  5-minute page cache, so you always see the real current state.
- Visual changes require real desktop and mobile screenshots. Existing local
  tools or manual captures are valid; DOM measurements do not replace images,
  and any missing coverage must be recorded explicitly.
