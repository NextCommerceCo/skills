# Theme Development

Gives your AI assistant deep, working knowledge of Next Commerce storefront
themes — the Spark theme, Intro Bootstrap, and custom themes — so it can
build, change, and debug your storefront the way an experienced platform
developer would.

It knows:

- How the platform's page templates work and fit together.
- How theme settings are structured so merchants get a clean Theme Editor.
- The theme toolkit (ntk) workflow for syncing theme files with your store.
- What must be rendered on the server versus loaded live in the browser
  (cart contents, login state) because of the platform's page caching.
- The Spark theme's styling pipeline and its non-obvious build steps.
- Ready-made recipes: brand colors and fonts, custom pages, product page
  redesigns, side cart changes, navigation, translations.
- The hard-won gotchas that silently break themes.

**Starting from a Figma design?** Run
[Theme Figma Handoff](../next-theme-figma/) first. It turns the design into a
precise implementation package; this skill then does the building. Using the
two together avoids the assistant guessing at what the design intends.

## What You Need

- **Python 3 installed**, and the theme toolkit **ntk** — your assistant can
  install and check both for you.
- **A Next Commerce store** with an API key that has theme permissions,
  created in your store admin under **Dashboard > Settings > API Keys**.
- **The theme's folder on your computer**, with its connection settings file
  pointing at your store and theme. Your assistant sets this up if it's
  missing, and can look up the theme ID for you.
- For the Spark theme, one extra styling tool is needed — the theme's own
  setup command installs it, and your assistant handles that.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Work inside the theme's folder and just describe what you want:

> Add a free-shipping progress bar to the side cart on my Spark theme.

> The product page price stopped updating when I select a variant — debug it.

The skill first identifies which theme family you're on and keeps every change
consistent with how that theme is built.

## Safety

- **Syncing changes to the store affects your live storefront.** The skill
  always asks for your explicit go-ahead before pushing anything, and pushes
  only the files that changed.
- Your saved Theme Editor settings are treated as merchant-owned state — they
  are only touched when the task genuinely requires it.
- Verification happens on the store's direct address, sidestepping the
  5-minute page cache, so you always see the real current state.
