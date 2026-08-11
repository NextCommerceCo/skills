# Intro Bootstrap Commerce Surfaces

Use this adapter when a handoff records `theme_family: intro-bootstrap`. Intro Bootstrap's live contract is Bootstrap 5 styling around a jQuery-driven theme runtime, with platform JavaScript loaded after the theme scripts (Intro Bootstrap 1.2.0, `layouts/base.html:253-276`).

## Divergence Principle

Do not implement Figma literally where it would break the contracts below. Record the intended difference in `platform-divergence-ledger.json`, describe the live contract in `platform_behavior`, and choose the appropriate decision, commonly `platform-wins` or `figma-wins-with-guardrails`.

## Buy Box Preservation Contract

- Preserve the price write targets `data-product-price` and `data-product-price-retail`; product variant changes write formatted live purchase values into those nodes (Intro Bootstrap 1.2.0, `templates/catalogue/product.html:88-100`; Intro Bootstrap 1.2.0, `assets/js/theme.js:128-146`).
- Preserve variant controls named with the `attr_*` prefix and their numeric attribute-value IDs. The controls live outside the cart form, and the runtime finds, parses, and matches them against the product variant data (Intro Bootstrap 1.2.0, `templates/catalogue/product.html:102-112`; Intro Bootstrap 1.2.0, `assets/js/theme.js:88-127`).
- Preserve the single `#add-to-cart` form, its POST behavior, and an action containing `cart/add/<pk>`. Variant selection rewrites that action to the selected variant ID, and the runtime manages the form's single submit button state (Intro Bootstrap 1.2.0, `templates/catalogue/product.html:156-174`; Intro Bootstrap 1.2.0, `assets/js/theme.js:147-162`).
- Preserve the `product-data` JSON payload and its variant structure, purchase information, availability, and image IDs; the product runtime reads and parses that payload before binding variant behavior (Intro Bootstrap 1.2.0, `templates/catalogue/product.html:299-311`; Intro Bootstrap 1.2.0, `assets/js/theme.js:77-127`).
- Preserve subscription option IDs `#product-one-time`, `#product-subscribe`, `#product-subscribe-options`, and `#product-subscribe-options-select`, plus the interval inputs and hidden `#id_interval` and `#id_interval_count` writers (Intro Bootstrap 1.2.0, `templates/catalogue/product.html:116-154`; Intro Bootstrap 1.2.0, `assets/js/theme.js:200-227`).

Classify add-to-cart, price, variant-picker, and subscription-option sections as `live-commerce-component`. Figma may guide their visual treatment, but the live controls and bindings remain in place.

## Side-Cart Drawer

The drawer's create, update, and remove GraphQL operations all request line `properties{key value}`; keep those query selections aligned across all operations (Intro Bootstrap 1.2.0, `assets/js/side_cart.js:4-222`).

The drawer stores its cart identity in the `storefront_cart_id` cookie, injects returned cart data into the drawer, and uses one delegated `change` handler on `#cart-modal` for quantity and subscription updates (Intro Bootstrap 1.2.0, `assets/js/side_cart.js:613-713`). Theme code does not bind the remove buttons or call the initial repopulation path; those behaviors are supplied by `{% core_js %}`, so removing or reordering that platform bundle breaks removal and drawer population (Intro Bootstrap 1.2.0, `assets/js/side_cart.js:634-642`; Intro Bootstrap 1.2.0, `assets/js/side_cart.js:716-739`; Intro Bootstrap 1.2.0, `layouts/base.html:275-276`).

Classify the cart drawer as `live-commerce-component`. Preserve its cookie, event delegation, GraphQL property shape, and platform-owned remove/repopulate behavior.

## Script Order

Keep jQuery first. The layout then loads Bootstrap, `theme.js`, theme initialization, extra scripts, the side-cart scripts and initialization, and finally `{% core_js %}`; the layout itself also emits a jQuery-ready wrapper (Intro Bootstrap 1.2.0, `layouts/base.html:253-276`; Intro Bootstrap 1.2.0, `partials/side_cart.html:12-51`). Do not defer jQuery past theme or inline initialization, and do not move `{% core_js %}` ahead of the theme-side setup.

## Settings-Driven Typography

The family does not hardcode a storefront font. `font_script`, `font_body`, and `font_header` are theme settings; when the body setting is blank the schema specifies a native stack, and when the header setting is blank headings inherit the body choice (Intro Bootstrap 1.2.0, `configs/settings_schema.json:6-24`). The base layout conditionally embeds `font_script`, maps `font_body` into the Bootstrap sans-serif variable, and adds a heading-family rule only when `font_header` is present (Intro Bootstrap 1.2.0, `layouts/base.html:28-45`; Intro Bootstrap 1.2.0, `layouts/base.html:79-83`). Record the derived theme settings in the handoff instead of inferring or hardcoding a family.

## Selector-Leak Surfaces

- `.content-body` applies element-level list, heading, paragraph, blockquote, figure, and image rules to injected rich text, so semantic rebuilds must check nested content against that scope (Intro Bootstrap 1.2.0, `sass/_custom.scss:99-213`).
- `.sidecart a` recolors descendant links, while `.sidecart button` removes padding, background, border, and native appearance from every descendant button (Intro Bootstrap 1.2.0, `sass/_cart.scss:186-207`).
- The base layout emits an inline `a {}` rule tied to the body link-color setting, which can override equal-specificity stylesheet rules across injected markup (Intro Bootstrap 1.2.0, `layouts/base.html:93-108`).

These selectors do not turn static editorial content into commerce, but they are implementation guardrails. Commerce surfaces affected by them still use `live-commerce-component`; record selector leakage in `platform_behavior` or the implementation guardrail when it explains an intentional visual divergence.
