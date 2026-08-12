# Intro Bootstrap Preservation Contract

Use this reference before redesigning or extracting Intro Bootstrap templates.
It describes Intro Bootstrap 1.2.0; verify the installed theme version and
store-local changes before applying it. Preserve these contracts while changing
markup or styling, and treat a family migration as separate owner-approved
work.

## SCSS Build and Compiled CSS

- The source entrypoint is `sass/main.scss`; local `ntk watch` compilation
  writes the result to `assets/main.css` (Intro Bootstrap 1.2.0,
  `CLAUDE.md:23,198,351`).
- `sass/main.scss` imports Bootstrap functions and variables first, then user
  Bootstrap variables, mixins, utilities, and components; its theme layer comes
  last in the order Bootstrap theme overrides, custom, navigation, cart,
  basket, flag, pay, and slick (Intro Bootstrap 1.2.0,
  `sass/main.scss:1-65`).
- The base layout links one compiled theme stylesheet, `main.css` (Intro
  Bootstrap 1.2.0, `layouts/base.html:27-31`).
- The compiled CSS is itself a DTL template: SCSS contains embedded
  `asset_url` filters for the loader and carousel arrow assets, and those
  expressions must survive compilation for storefront rendering (Intro
  Bootstrap 1.2.0, `sass/_custom.scss:355,433-454`). Never pass
  `assets/main.css` through an external plain-CSS minifier or CDN rewrite that
  can alter those template expressions.

Keep the existing import order and build path. Make source changes in `sass/`,
compile locally, inspect the single generated artifact, and upload that artifact
with the source changes.

## Script Order Is a Runtime Contract

The base layout loads scripts in this order: jQuery, `bootstrap.bundle`,
`theme.js`, and `theme.init()`; then extra scripts and the unconditional
jQuery-dependent DOM-ready shell; then the side-cart include; then
`{% core_js %}` last (Intro Bootstrap 1.2.0,
`layouts/base.html:253-276`). The unblockable
`{% include 'partials/side_cart.html' %}` loads the cookie helper, `cart.js`,
and `side_cart.js`, then initializes the drawer before the platform bundle runs
(Intro Bootstrap 1.2.0, `partials/side_cart.html:12-51`; Intro Bootstrap 1.2.0,
`layouts/base.html:275-276`).

The DOM-ready wrapper calls `$()` unconditionally, so the layout itself depends
on jQuery even when a page adds no custom scripts (Intro Bootstrap 1.2.0,
`layouts/base.html:268-272`). The side-cart include and `{% core_js %}` sit
outside overridable template blocks (Intro Bootstrap 1.2.0,
`layouts/base.html:265-278`). Never defer or reorder jQuery, never move
`{% core_js %}` earlier, and never remove either surface as a visual cleanup.

## Cart Drawer: Theme and Platform Responsibilities

- `assets/js/cart.js` owns the drawer shell behavior. It exposes show/hide
  actions and emits `show.cart` and `hide.cart` while managing the drawer and
  body state (Intro Bootstrap 1.2.0, `assets/js/cart.js:2-42`).
- `assets/js/side_cart.js` owns create, update, and remove GraphQL operations.
  The three cart-line selections must move together in any schema edit (Intro
  Bootstrap 1.2.0, `assets/js/side_cart.js:4-222`). For the family-neutral
  schema rule, see `### Cart and User State (Client-Side State Required)` in
  `../SKILL.md`.
- Cart identity is the `storefront_cart_id` cookie managed through
  `PrimeCookies` (Intro Bootstrap 1.2.0,
  `assets/js/side_cart.js:628-633`; Intro Bootstrap 1.2.0,
  `assets/js/jscookie.js:21`).
- Quantity and subscription changes use a single delegated `change` listener on
  `#cart-modal` (Intro Bootstrap 1.2.0,
  `assets/js/side_cart.js:613-713`). Do not add competing per-row listeners.
- Theme code defines the remove method and drawer reload path but does not bind
  remove buttons or invoke the initial population path. That wiring lives in
  `{% core_js %}`; stripping the platform bundle breaks removal and drawer
  population (Intro Bootstrap 1.2.0,
  `assets/js/side_cart.js:634-642,716-739`; Intro Bootstrap 1.2.0,
  `layouts/base.html:275-276`).

## `cart:add` Form and Buy-Box Contract

- The PDP contains one `#add-to-cart` form. It POSTs to
  `cart/add/<pk>`, and the client rewrites the numeric PK in that URL with
  `/(cart\/add\/)\d+/` when a variant changes (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:156-174`; Intro Bootstrap 1.2.0,
  `assets/js/theme.js:147-162`). It is a server form submission, not a Spark
  Web Component or an Ajax add flow.
- Preserve `{% csrf_token %}` in this form. It is the sanctioned exception for
  the cached custom-template warning because the platform runtime refreshes the
  token (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:160-163`; Intro Bootstrap 1.2.0,
  `CLAUDE.md:150-156`).
- Preserve the `cart_form` output, including hidden `interval` and
  `interval_count` fields and the runtime writers `#id_interval` and
  `#id_interval_count` (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:156-174`; Intro Bootstrap 1.2.0,
  `partials/form_fields.html:1-2`; Intro Bootstrap 1.2.0,
  `assets/js/theme.js:214-227`).
- Keep exactly one submit button in the form because the runtime finds and
  rewrites that button's state and label (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:164-171`; Intro Bootstrap 1.2.0,
  `assets/js/theme.js:147-162`).
- Variant `attr_*` controls stay outside the form and retain numeric
  attribute-value IDs, which the runtime parses and compares with the product
  variant payload (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:102-112`; Intro Bootstrap 1.2.0,
  `assets/js/theme.js:88-127`).
- Preserve `{{ product.data|json_script:"product-data" }}`. Its JSON supplies
  `structure`, `children[].variant_attribute_values[].code/id`, formatted
  purchase information, availability, and image IDs used by variant selection
  and repricing (Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:299-311`; Intro Bootstrap 1.2.0,
  `assets/js/theme.js:77-189`).

Extract and preserve this working commerce core verbatim when changing the
visual buy-box shell. Do not translate it into Spark custom elements as part of
a redesign.

## Settings Conventions

- Templates read settings through `{{ settings.<name> }}`, including base and
  product layouts (Intro Bootstrap 1.2.0,
  `layouts/base.html:12,28,41-71,102-125`; Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:28-54,105,176`).
- `configs/settings_schema.json` uses a group to section to field-array shape;
  fields carry keys such as name, label, type, help text, options, and defaults
  (Intro Bootstrap 1.2.0, `configs/settings_schema.json:1-1024`). Extend that
  shape instead of introducing an ad hoc settings map.
- `home_page_css` and `product_page_css` are deliberate merchant raw-CSS escape
  hatches (Intro Bootstrap 1.2.0,
  `configs/settings_schema.json:638-640,811-813`; Intro Bootstrap 1.2.0,
  `templates/catalogue/product.html:43-45`). Preserve them and account for their
  cascade during QA.
- Merchant-editable storefront copy belongs in theme settings, following the
  existing text/html field conventions rather than being hardcoded into
  templates (Intro Bootstrap 1.2.0,
  `configs/settings_schema.json:1-1024`).

## Typography Inheritance

Intro Bootstrap hardcodes no theme font. Its base Sass font override is absent,
the heading family is null, and Bootstrap's native sans-serif stack therefore
flows into the body and headings (Intro Bootstrap 1.2.0,
`sass/_user-bootstrap-variables.scss:44-46`; Intro Bootstrap 1.2.0,
`sass/bootstrap/_root.scss:13`; Intro Bootstrap 1.2.0,
`sass/bootstrap/_reboot.scss:48,87`).

Runtime typography comes from `font_script`, `font_body`, and `font_header`
settings. Blank body means the native stack and blank header means headings
inherit the body (Intro Bootstrap 1.2.0,
`configs/settings_schema.json:6-24`; Intro Bootstrap 1.2.0,
`configs/settings_data.json:2-4`). The base layout conditionally embeds
`font_script`, maps `font_body` to `--bs-font-sans-serif`, and maps
`font_header` to `--font-header` (Intro Bootstrap 1.2.0,
`layouts/base.html:28-45`). It emits the heading rule only when the header
setting is present, and that rule covers only `h1` through `h6` and `.h1`
through `.h6` (Intro Bootstrap 1.2.0, `layouts/base.html:79-83`).

Before styling, read the store's current `font_*` settings and inspect the
derived base styles. Do not "fix" typography by adding a font in SCSS; that
would silently diverge from the Theme Editor contract.

## Selector-Leak Preflight

Audit redesigned and injected markup against these family surfaces:

- `.content-body` applies element-level list, heading, paragraph, blockquote,
  figure, and image rules to rich text (Intro Bootstrap 1.2.0,
  `sass/_custom.scss:99-213`).
- `.sidecart a` recolors descendant links, and `.sidecart button` resets
  padding, background, border, native appearance, and font inheritance for
  every descendant button (Intro Bootstrap 1.2.0,
  `sass/_cart.scss:186-207`).
- At the mobile cart breakpoint, table cells use `td::before` with
  `content: attr(data-label)`, so every injected cell must carry the expected
  label (Intro Bootstrap 1.2.0, `sass/_cart.scss:85-95`).
- The base layout emits an inline `a {}` link-color rule that can beat an
  equal-specificity stylesheet declaration (Intro Bootstrap 1.2.0,
  `layouts/base.html:93-108`).
- `label.error` is a bare validation selector and can affect new forms (Intro
  Bootstrap 1.2.0, `sass/_custom.scss:41-47`).
- The body class is emitted as
  `class="{% block body_class %}{% endblock %}main"` with no inserted space.
  A `body_class` override without its own trailing space merges into `main` and
  loses the `body.main` flex layout (Intro Bootstrap 1.2.0,
  `layouts/base.html:119`; Intro Bootstrap 1.2.0,
  `sass/_custom.scss:5-9`).

## Overridable and Unblockable Layout Surfaces

The 22 overridable blocks in `layouts/base.html`, in source order, are:

1. `html_class`
2. `title`
3. `viewport`
4. `site_index`
5. `seo`
6. `favicon`
7. `styles`
8. `extrastyles`
9. `extrahead`
10. `tracking`
11. `layout`
12. `announcement_bar`
13. `nav_header`
14. `mini_cart`
15. `content_wrapper`
16. `breadcrumbs`
17. `content`
18. `footer`
19. `modals`
20. `scripts`
21. `extrascripts`
22. `onbodyload`

This order and block inventory come from Intro Bootstrap 1.2.0,
`layouts/base.html:2-270`. Prefer these native override seams when they preserve
the required commerce core.

The global-header and global-footer app hooks, the `#content_inner` wrapper,
the unconditional DOM-ready shell, the side-cart include, and `{% core_js %}`
are rendered outside those blocks and cannot be suppressed through ordinary
block overrides (Intro Bootstrap 1.2.0,
`layouts/base.html:116,237-241,268-278`). Preserve them unless an explicitly
approved runtime migration replaces their responsibilities.

The announcement-bar include declares a nested second `extrastyles` block,
duplicating the base layout's block name (Intro Bootstrap 1.2.0,
`partials/announcement_bar.html:1-8`; Intro Bootstrap 1.2.0,
`layouts/base.html:112,122`). Inspect both declarations before overriding
announcement styles so block resolution does not hide one of the intended
style surfaces.
