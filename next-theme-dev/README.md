# Theme Development

Deep platform knowledge for building, modifying, and debugging Next Commerce
storefront themes — Spark, Intro Bootstrap, and custom themes. Covers:

- **Django Template Language** — inheritance, partials, template contexts, URL names.
- **Theme Settings** — `settings_schema.json` / `settings_data.json` conventions
  and merchant-friendly information architecture.
- **ntk CLI** — push/pull/watch workflow, accepted file types, common errors.
- **Full-page caching rules** — what belongs in server-side templates vs
  client-side GraphQL (cart, auth, per-user state).
- **Tailwind pipeline** (Spark) — standalone CLI build, the required
  `sass-compat.py` compatibility pass, committed `assets/main.css` artifact.
- **Task recipes** — colors/fonts, custom page and product templates, Spark PDP
  redesigns, side cart customization, navigation, translations, Figma asset export.
- **Hard-won gotchas** — the things that silently break themes.

If the work starts from a Figma design, run
[`next-theme-figma`](../next-theme-figma/) first — it produces the handoff
package this skill consumes.

## Requirements

- **Python 3** — for ntk and the bundled helper scripts. **Pillow** is needed
  only when validating raster assets with `scripts/validate-theme-assets.py`.
- **ntk** — `pip install next-theme-kit` (pipx/uv also work).
- **A Next Commerce store** with an API key holding theme scopes, from
  **Dashboard > Settings > API Keys**.
- A `config.yml` in the theme directory:

```yaml
development:
  apikey: <api_key>
  store: <store_subdomain>.29next.store
  theme_id: <theme_id>
```

Get the `theme_id` from `ntk list`. For Spark/Tailwind themes you'll also need
the Tailwind v4 standalone binary (the theme's `make install-tailwind` handles it).

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-theme-dev
```

## How to Use

Work inside a theme directory (look for `manifest.json`, `config.yml`, and the
standard `assets/`, `configs/`, `layouts/`, `partials/`, `templates/` layout)
and ask for what you need:

> Add a free-shipping progress bar to the side cart on my Spark theme.

> The PDP price stopped updating when I select a variant — debug it.

The skill identifies the theme family first (Spark vs Intro Bootstrap vs custom)
and keeps changes within that stack.

## Safety

- **`ntk push` and `ntk watch` mutate the live store theme.** The skill requires
  explicit operator confirmation before either, and pushes only changed files.
- `configs/settings_data.json` is treated as merchant Theme Editor state — it is
  only pushed when the task explicitly requires changing saved values.
- Develop and verify on the `.29next.store` domain to bypass the 5-minute
  full-page CDN cache.
