---
name: next-theme-dev
version: 1.0.0
description: |
  NEXT Commerce theme development — build, modify, and debug storefront themes
  using Django Template Language, ntk CLI, and the NEXT Commerce platform.
  Use this skill when working in a NEXT Commerce theme directory (look for
  manifest.json, config.yml, or the standard theme directory structure with
  assets/, configs/, layouts/, templates/, partials/). Also trigger when the
  user mentions ntk, theme templates, storefront customization, or NEXT Commerce
  themes. Proactively suggest when you detect DTL template files (.html with
  {% extends %}, {% block %}, {% include %}) alongside a configs/ directory.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Grep
  - Glob
---

# NEXT Commerce Theme Development

## Preamble — Environment Check

Run these checks at the start of every theme task to understand the working context:

```bash
# Check ntk installation
which ntk 2>/dev/null && ntk --version || echo "ntk not installed — install via: pip install 29next-theme-kit"

# Check Python
python3 --version 2>/dev/null || python --version 2>/dev/null || echo "Python not found"

# Check for theme config
[ -f config.yml ] && echo "config.yml found" || echo "No config.yml — run 'ntk init' or create one"

# Identify theme
[ -f manifest.json ] && cat manifest.json || echo "No manifest.json"
```

If `config.yml` is missing, the developer needs to create one:
```yaml
development:
  apikey: <api_key>
  store: <store_subdomain>.29next.store
  theme_id: <theme_id>
```

Get the API key from Dashboard > Settings > API Keys. Get the theme_id from `ntk list`.

---

## Architecture

### Directory Structure

Every NEXT Commerce theme follows this structure:

```
theme/
├── assets/          # CSS, JS, images, fonts (served via CDN)
├── checkout/        # Checkout template overrides
├── configs/
│   ├── settings_schema.json   # Theme editor field definitions
│   └── settings_data.json     # Stored setting values
├── layouts/
│   └── base.html    # Main layout (all pages extend this)
├── locales/         # Translation JSON files (en.json, fr.json, etc.)
├── partials/        # Reusable template fragments
├── sass/ or css/    # Source stylesheets (sass/ for SCSS, css/ for Tailwind)
└── templates/       # Page templates (index.html, catalogue/, blog/, etc.)
```

### Template Language

Templates use Django Template Language (DTL):

- **Inheritance:** `{% extends "layouts/base.html" %}` + `{% block content %}...{% endblock %}`
- **Includes:** `{% include "partials/header.html" %}`
- **Variables:** `{{ product.get_title }}`, `{{ settings.primary_color }}`
- **Tags:** `{% for item in products %}...{% endfor %}`, `{% if condition %}...{% endif %}`
- **Filters:** `{{ price|currency:currency_code }}`, `{{ text|truncatewords:10 }}`

### Settings System

Theme settings let merchants customize their store without code:

- `settings_schema.json` defines the editor UI — groups of fields with types like `text`, `color`, `image_picker`, `select`, `menu`, `checkbox`
- `settings_data.json` stores the current values
- Templates access values via `{{ settings.field_name }}`

**Settings Information Architecture:**
- **Organize by merchant mental model**, not developer taxonomy — use "Side Cart" not "Advanced > Cart Configuration"
- **5+ settings = own top-level section** — don't bury 14 cart settings inside a generic "Advanced" group
- **Use merchant-friendly labels** — "Suggested Products" not "Upsells", "Cart Title" not "cart_header_title"
- **Keep dev-only values out of the schema** — implementation details (e.g., `upsell_fallback_slots`) belong in `settings_data.json` defaults, not in the editor UI
- **Currency thresholds:** Ship USD-only thresholds initially. Use DTL `{% if %}`/`{% elif %}` branches on `request.CURRENCY_CODE` to select the right settings per currency. Developers can extend by adding `eur_goal_1`, `gbp_goal_1`, etc.
- **Group ordering gotcha:** Display order is determined by first-seen in `settings_schema.json`, not JSON key order. Renaming a group key makes it appear last in the editor

---

## Reference Documentation

For detailed reference on template tags, objects, filters, and settings types, read these files from the developer-docs repository. These are the source of truth — always consult them before writing template code.

| Topic | File Path |
|-------|-----------|
| Template tags | `developer-docs/content/docs/storefront/themes/templates/tags.mdx` |
| Template filters | `developer-docs/content/docs/storefront/themes/templates/filters.md` |
| Template objects | `developer-docs/content/docs/storefront/themes/templates/objects.mdx` |
| URLs & template paths | `developer-docs/content/docs/storefront/themes/templates/urls-and-template-paths.mdx` |
| Settings field types | `developer-docs/content/docs/storefront/themes/settings.mdx` |
| Translations / i18n | `developer-docs/content/docs/storefront/themes/translations.mdx` |
| Storefront GraphQL API | `developer-docs/content/docs/storefront/graphql/index.mdx` |

The developer-docs repo is typically at `/Users/devin/Developer/developer-docs/`. If it's not there, search for it or ask the user.

When you need to know what variables a template has access to, the objects reference includes a **Template Contexts** table that maps every template path to its available view-specific variables, plus a **Dashboard Cross-Reference** showing where variable data is configured in the admin.

---

## Critical Warnings

These will silently break things if ignored:

### Full-Page Caching: The Server vs. Client Boundary

This is the most important architectural constraint in NEXT Commerce themes. All storefront pages are **fully cached for 5 minutes** on mapped domains. The cache is keyed by URL + language + currency combination — so each locale variant (EN+USD, FR+EUR, etc.) has its own cached page, and all visitors with that same locale see the same cached HTML.

This means product pricing is safe in templates (it varies by currency, and the cache handles that). But per-user data — cart, authentication, wishlists — is unique to each individual and fundamentally incompatible with page caching.

**Server-side templates (DTL) are for public content (same for all visitors in a locale):**
- Product details, pricing, categories, blog posts, pages
- Store info, settings-driven UI, navigation menus
- SEO metadata, structured data

**Client-side JavaScript (via GraphQL API) is required for per-user content:**
- Cart state (item count, totals, line items)
- User authentication state (logged in/out, username)
- Wishlists, saved items, order history

Never put `{{ cart.num_items }}` or `{{ user.is_authenticated }}` in a cached template — one user's data gets served to everyone in that locale. This is why NEXT Commerce themes don't show a cart item counter in the nav by default — displaying it requires a client-side GraphQL call on every page load, which is an intentional performance tradeoff.

```
SAFE in templates (cached per locale)    REQUIRES GraphQL (per-user, client-side JS)
─────────────────────────────────────    ──────────────────────────────────────────
{{ product.get_title }}                  Cart contents, count, totals
{{ session.price.price|currency:... }}   User login state / profile
{{ settings.* }}                         Wishlists, saved items
{{ store.name }}, {{ menus.*.items }}    Order history
Product prices, categories, filters      Checkout state
```

The `/cart/`, `/checkout/`, and `/accounts/` paths are excluded from full-page caching, but any content in shared layouts (headers, footers via `layouts/base.html`) renders on every cached page.

### Tailwind + DTL: No Dynamic Class Construction

Never build Tailwind class names with template variables:
```django
{# BAD — Tailwind's purge scanner can't see runtime values #}
<div class="bg-{{ settings.primary_color }}">

{# GOOD — use CSS custom properties #}
<style>:root { --primary-color: {{ settings.primary_color|default:"#1E293B" }}; }</style>
<div class="bg-[var(--primary-color)]">
```

Tailwind scans source files at build time to determine which classes to include. Template variables only resolve at runtime on the server, so dynamically constructed class names get purged from the CSS output.

### No Non-ASCII in JavaScript Files

The platform processes all theme files through the DTL engine, including `.js` files. Non-ASCII characters (curly quotes, em dashes, emoji) in JS files cause encoding errors. Stick to plain ASCII in all JavaScript.

### ntk Push: Only Changed Files

Always push specific files, never the entire theme:
```bash
# Good
ntk push templates/index.html
ntk push assets/main.css configs/settings_schema.json

# Bad — pushes everything, slow, unnecessary
ntk push
```

### jQuery Before core_js

The platform's `{% core_js %}` tag depends on jQuery. If your theme uses jQuery, load it before `{% core_js %}` in the base layout:
```django
<script src="{{ 'jquery.min.js'|asset_url }}"></script>
{% core_js %}
```

### CDN Caching

CloudFront aggressively caches assets and full pages (5 min on mapped domains):
- **Always develop on the `.29next.store` network domain** — it bypasses full-page caching
- Append `?skip_cache` to a URL for edge cases
- Template changes via ntk automatically bust the template cache
- Asset changes (CSS/JS) may take a moment to propagate on CDN

### DTL Comments: Single-Line Only

Django template comments must be single-line. Multi-line comment blocks render as visible text:
```django
{# This is correct — single line #}

{# This is WRONG —
   it will render as visible text on the page #}
```

### Anti-Slop Design Rules

When building or modifying theme UI, enforce these rules to avoid generic "AI-generated" aesthetics:
- No 3-column icon grids (the default lazy layout)
- No centered-everything layouts
- No generic hero text ("Welcome to our store")
- No uniform rounded corners on everything
- No default blue buttons
- No decorative gradient blobs or abstract shapes
- No emoji in UI elements
- Default `text-sm` (14px) feels too small for marketing storefronts — prefer `text-base` (16px) for body copy

### Text Sizing

For marketing-forward storefronts, `text-sm` (14px) as the body default feels cramped. Use `text-base` (16px) minimum for body copy. Headlines should have clear hierarchy — don't let Tailwind defaults flatten your type scale.

---

## Known Gotchas

Hard-won lessons from building Spark. These will silently break things if you don't know about them.

| Gotcha | Details |
|--------|---------|
| **Product picker returns parent PK** | `settings.gift_product` gives the parent product PK. For cart operations (addCartLines), use `.children.first.pk` to get the variant ID |
| **Settings group ordering** | Group display order = first-seen in `settings_schema.json`, not JSON key order. Renaming a group makes it appear last |
| **manifest.json can't be pushed** | ntk excludes `manifest.json` from push/watch. Version is set at `ntk init` only |
| **Shadow DOM ≠ slotted styles** | Shadow DOM styles don't apply to slotted (light DOM) content. Fix: inject a `<style>` tag into `document.head` with a guard flag to prevent duplicates |
| **connectedCallback fires early** | `connectedCallback` fires before child elements are parsed. Use a lazy `_ensureRefs()` pattern called from methods that need refs, plus `requestAnimationFrame` for initial updates |
| **Concurrent mutation guard** | Cart mutations must use an `_isMutating` flag to prevent race conditions from rapid clicks (e.g., quantity +/+ before first response returns) |
| **sass-compat is required** | Every Tailwind build must run through `sass-compat.py`. Platform SCSS compiler rejects: `oklch()`, `color-mix()`, `@layer`, `@property`, `:is()`/`:where()`, logical properties, media range syntax |
| **Preview URL** | `https://{store}/?preview_theme={theme_id}` — useful for testing unpublished theme changes |
| **ntk accepted directories** | Only these are recognized: assets, checkout, configs, layouts, partials, templates, locales, sass. Files outside these are silently ignored |

---

## Dashboard-Theme Bridge

Some theme features require **both** theme settings AND dashboard configuration to work correctly. The theme handles the UI, but the actual discount/shipping logic lives in Dashboard > Marketing > Offers.

| Feature | Theme Side | Dashboard Side |
|---------|-----------|----------------|
| Free shipping progress bar | Settings: threshold amount, progress messages, bar UI | Requires: Conditional free shipping Offer matching the threshold |
| Free gift auto-add | Settings: gift product picker, threshold, auto-add JS | Requires: Conditional discount Offer that makes the gift $0 |
| Upsell products | Settings: product pickers, display logic | No Offer needed (products added at full price) |
| Discount codes | Coupon input UI in cart | Requires: Discount code created in Dashboard |

**Without the matching Offer, the theme UI will promise something it can't deliver** — e.g., the progress bar says "Free shipping at $50!" but checkout still charges shipping.

When adding these settings to `settings_schema.json`, include help text pointing merchants to the relevant dashboard setup. A link to documentation is the minimum viable approach.

---

## Design to Theme Workflow

Step-by-step process for assembling a working theme from a design file. This is the primary workflow for the marketer persona.

### Step 1: Design Intake

Accept any of: Figma link, screenshot, PDF, or verbal description. Extract:
- **Color palette** — primary, secondary, accent, neutrals, backgrounds
- **Typography** — font families, size scale, weight usage, line heights
- **Spacing system** — section padding, component gaps, content margins
- **Component inventory** — header, footer, hero, product cards, CTAs, nav, cart drawer
- **Static vs dynamic split** — which elements show the same content for all visitors (DTL) vs per-user content (GraphQL)

If a `DESIGN.md` exists in the project, it is the **source of truth** for all visual decisions. Read it before making any UI choices.

### Step 2: Asset Preparation

This is the **#1 time bottleneck** — design assets are merchant-specific and can't be templated.

- **Fonts:** Convert to `.woff2`, create `@font-face` declarations in CSS, add font files to `assets/`
- **Images:** Hero images, product photography, lifestyle shots — these must come from the merchant. Use placeholder images during development
- **Icons:** Prefer inline SVG (smallest payload, style-able) or an icon font. Avoid individual image files for icons
- **Optimization:** All assets serve via CDN. Keep images under 200KB, use WebP/AVIF where supported

### Step 3: Settings Schema Design

Map design tokens to merchant-configurable settings in `configs/settings_schema.json`:

1. **Colors** → `color` type fields (primary, secondary, accent, background)
2. **Fonts** → `text` type fields for font family names
3. **Feature toggles** → `checkbox` type fields (show/hide sections)
4. **Content** → `text` fields for headlines, CTAs, placeholder copy
5. **Navigation** → `menu` type fields linked to dashboard menus
6. **Products** → `product` type fields for featured/upsell products

Follow the Settings IA principles: organize by merchant mental model, 5+ settings get their own section, use merchant-friendly labels.

### Step 4: Template Assembly

Build order:
1. **`layouts/base.html`** — CSS custom properties from settings, global head/scripts, header/footer includes
2. **Partials** — One per design component (`partials/header.html`, `partials/footer.html`, `partials/product_card.html`, etc.)
3. **Page templates** — `templates/index.html`, `templates/catalogue/product.html`, etc. using `{% extends %}` and `{% block %}`
4. **Cart/user features** — Client-side only via GraphQL + Web Components (see Side Cart recipe)

### Step 5: Styling

- Configure Tailwind with design tokens (colors, fonts, spacing)
- Wire CSS custom properties from theme settings in `layouts/base.html`:
  ```django
  <style>:root { --primary: {{ settings.primary_color|default:"#1E293B" }}; }</style>
  ```
- **Run sass-compat.py before every push** (required — platform rejects modern CSS)
- Test responsive breakpoints: mobile (375px), tablet (768px), desktop (1280px+)

### Step 6: Client-Side Features

For per-user content (cart, auth, wishlists):
- Use GraphQL API at `/api/graphql/` with CSRF token
- Build as Web Components (Shadow DOM + light DOM hybrid)
- Dispatch events on `document` for cross-component communication
- Store cart ID in `sessionStorage` + cookie

### Step 7: Verify & Deploy

1. Preview unpublished changes: `https://{store}/?preview_theme={theme_id}`
2. Always test on `.29next.store` domain (bypasses CDN full-page cache)
3. Push only changed files: `ntk push templates/index.html partials/header.html`
4. Check dashboard-side requirements: free shipping/gift features need matching Offers (see Dashboard-Theme Bridge)
5. Verify cart operations work end-to-end (add, remove, quantity change, checkout)

---

## Task Recipes

### Change Colors or Fonts

1. Add fields to `configs/settings_schema.json`:
```json
{
  "Theme Styles": {
    "primary_color": { "type": "color", "label": "Primary Color", "default": "#1E293B" },
    "body_font": { "type": "text", "label": "Body Font Family", "default": "system-ui, sans-serif" }
  }
}
```

2. Wire into `layouts/base.html` via CSS custom properties:
```django
<style>
:root {
    --primary-color: {{ settings.primary_color|default:"#1E293B" }};
    --body-font: {{ settings.body_font|default:"system-ui, sans-serif" }};
}
</style>
```

3. Push both files: `ntk push configs/settings_schema.json layouts/base.html`

### Add a Custom Page Template

1. Create `templates/pages/{name}.html`:
```django
{% extends "layouts/base.html" %}
{% block content %}
<div class="container">
    <h1>{{ page.title }}</h1>
    <div>{{ page.content|safe }}</div>
</div>
{% endblock %}
```

2. Push: `ntk push templates/pages/{name}.html`
3. In the dashboard, create a Page and select the custom template

### Add a Custom Product Template

1. Create `templates/catalogue/product.{slug}.html` (the slug must match the product's URL slug):
```django
{% extends "layouts/base.html" %}
{% load core_tags %}
{% block content %}
    {# Custom product layout #}
{% endblock %}
```

2. Push: `ntk push templates/catalogue/product.{slug}.html`

### Add a Partial

1. Create `partials/{name}.html` with the fragment
2. Include it: `{% include "partials/{name}.html" %}`
3. Pass context if needed: `{% include "partials/product_card.html" with product=item %}`
4. Push both files

### Modify Navigation

Navigation menus are managed in Dashboard > Storefront > Navigation. The theme accesses them via the `menus` global object and a `menu` settings field:

1. Add a menu field to `configs/settings_schema.json`:
```json
{
  "Navigation": {
    "main_menu": { "type": "menu", "label": "Main Navigation", "default": "main_menu" }
  }
}
```

2. Render in the template:
```django
{% for item in settings.main_menu.items %}
    <a href="{{ item.url }}" class="{% if item.current %}active{% endif %}">
        {{ item.name }}
    </a>
    {% if item.level > 0 %}
        {# Has children — render dropdown #}
        {% for child in item.items %}
            <a href="{{ child.url }}">{{ child.name }}</a>
        {% endfor %}
    {% endif %}
{% endfor %}
```

Menus support up to 3 levels of nesting. Read the objects reference for all `item` properties.

### Add Translations

1. Add keys to each locale file in `locales/`:
```json
{
  "cart": {
    "empty_message": "Your cart is empty",
    "checkout_button": "Proceed to Checkout"
  }
}
```

2. Use in templates: `{% t "cart.empty_message" %}`
3. With variables: `{% t "cart.item_count" with count=cart.num_items %}`
4. Push all changed locale files

### Cart and User State (GraphQL — Required)

Because of full-page caching, all cart and user interactions must go through the Storefront GraphQL API at `/api/graphql/`. This is not optional — server-side template variables for cart/user data will be cached and show stale or wrong data to visitors.

Use GraphQL for:
- Cart operations: `createCart`, `addCartLines`, `updateCartLines`, `removeCartLines`
- Reading cart state: `cart` query (item count, totals, line items)
- User state: `me` query (authentication, profile)
- Any data that varies per visitor

Include CSRF token in all requests:
```javascript
function getCookie(name) {
    const value = `; ${document.cookie}`;
    const parts = value.split(`; ${name}=`);
    if (parts.length === 2) return parts.pop().split(';').shift();
}

fetch('/api/graphql/', {
    method: 'POST',
    headers: {
        'Content-Type': 'application/json',
        'X-CSRFToken': getCookie('csrftoken')
    },
    body: JSON.stringify({
        query: `{ cart { totalQuantity totalAmount { amount currency } } }`
    })
})
```

Read the GraphQL reference in developer-docs for the full schema, all available queries/mutations, and example payloads.

### Side Cart Customization

Side carts (cart drawers) are one of the most common theme customization requests. They combine server-rendered product data with client-side GraphQL for cart operations. The architecture uses a hybrid approach: DTL templates render the initial shell and product details (cache-safe), while JavaScript Web Components handle all cart mutations and live updates.

**Architecture pattern:**

1. **GraphQL API layer** — A single JS file (`assets/js/cart-drawer.js`) containing:
   - `graphqlFetch()` helper with CSRF authentication
   - Shared `CART_FIELDS` fragment (all fields needed across queries/mutations)
   - All cart mutations: `createCart`, `addCartLines`, `updateCartLines`, `removeCartLines`
   - Cart ID management via `sessionStorage` + cookie fallback

2. **Web Components** — Custom elements that encapsulate cart UI behavior:
   - `<cart-drawer>` — Main container, manages open/close state, orchestrates cart operations
   - `<cart-item>` — Individual line item with quantity controls and remove action
   - `<upsell-item>` — Upsell product row with variant selector and add button
   - `<custom-progress-bar>` — Multi-step incentive bar (e.g., free shipping → free gift)

3. **Server-rendered shell** — DTL partial (`partials/side_cart.html`) renders:
   - Initial cart state via `data-*` attributes on Web Components
   - Product images/titles/prices using `purchase_info_for_product` (cache-safe)
   - Theme settings for all configurable text, thresholds, and product pickers
   - Progress bar initial state with currency-specific thresholds

4. **Theme Settings** — Full merchant control via `configs/settings_schema.json`:

```json
{
  "Side Cart": {
    "cart_header_title": { "type": "text", "label": "Cart Title", "default": "Your Cart" },
    "enable_progress_bar": { "type": "checkbox", "label": "Enable Progress Bar", "default": true },
    "enable_upsells": { "type": "checkbox", "label": "Enable Upsells", "default": true },
    "upsell_section_title": { "type": "text", "label": "Upsell Section Title", "default": "Often Bought Together" },
    "upsell_product_1": { "type": "product", "label": "Upsell Product 1" },
    "upsell_product_2": { "type": "product", "label": "Upsell Product 2" },
    "gift_product": { "type": "product", "label": "Free Gift Product" },
    "usd_goal_1": { "type": "number", "label": "Free Shipping Threshold (USD)", "default": 50 },
    "usd_goal_2": { "type": "number", "label": "Free Gift Threshold (USD)", "default": 70 },
    "step_1_message": { "type": "text", "label": "Shipping Message", "default": "You are {amount} away from FREE shipping" },
    "step_2_message": { "type": "text", "label": "Gift Message", "default": "You are {amount} away from a FREE gift!" },
    "cart_pay_icons": { "type": "select", "multi-select": true, "label": "Payment Icons", "options": ["visa","mc","ppal","amex"] }
  }
}
```

**Key implementation patterns:**

- **Event bus**: Cart components communicate via custom events on `document`: `spark:cart:updated` (after any mutation), `spark:cart:added` (item added), `spark:cart:toggle` (open/close drawer). Other components listen on `document` for these events — no direct component coupling.

- **Cart persistence**: Cart ID stored in both `sessionStorage` (fast access) and a 30-day cookie (cross-tab persistence). On page load, check `sessionStorage` first, fall back to cookie. Always sync both after cart creation.

- **Mutation guard**: All cart mutations must check/set an `_isMutating` flag to prevent race conditions from rapid clicks (e.g., quantity +/+ before first response returns). Reset the flag in `finally` block.

- **Success validation**: Check `result.success` not `result.cart.numItems > 0`. The latter fails on empty cart after removing last item. Surface `result.errors` array for descriptive messages instead of generic "Could not add to cart".

- **Product picker PK gotcha**: `settings.gift_product` returns the parent product PK. For cart operations (`addCartLines`), use `.children.first.pk` to get the actual variant ID.

- **Progress bar currency handling**: Thresholds vary by currency. Server-renders the correct threshold set using `request.CURRENCY_CODE` to select settings (e.g., `settings.usd_goal_1` vs `settings.eur_goal_1`). The `<custom-progress-bar>` element receives thresholds via `data-*` attributes and JS updates the fill/messages on cart change.

- **Upsell slot visibility**: Products have metadata (`cart_upsell_slots`) defining which upsell slots to show. When the cart changes, JS checks each cart product's metadata and shows/hides upsell rows accordingly. Products already in the cart have their upsell slots hidden.

- **Free gift auto-add/remove**: When cart total crosses the gift threshold, JS automatically adds the gift product via `addCartLines` with `isUpsell: true`. When it drops below, JS removes the gift line. The gift product is configured via a `product` type theme setting. **Note:** This only adds the product to cart — making it actually free requires a matching Offer in the dashboard (see Dashboard-Theme Bridge section).

- **Cart count badge**: Updated via JS after every cart operation by selecting all `[data-cart-count]` elements in the DOM and setting their text content. This is the only way to show cart count — never use `{{ cart.num_items }}` in cached templates.

- **Web Component timing**: Load component scripts BEFORE template inclusion. Use `_ensureRefs()` lazy pattern since `connectedCallback` fires before children parse. For slotted content styling, inject `<style>` into `document.head` with a guard flag (Shadow DOM styles don't reach slotted elements).

- **Design from Figma**: When a design is provided (Figma, screenshot, or description), map visual elements to this component architecture. The CSS lives in the theme's SCSS/CSS, the behavior in the Web Components, and merchant-configurable values in theme settings.

**Files to create/modify:**

| File | Purpose |
|------|---------|
| `assets/js/cart-drawer.js` | GraphQL API layer + Web Component definitions |
| `partials/side_cart.html` | Main cart drawer partial (included in `layouts/base.html`) |
| `partials/block_cart_progress_bar.html` | Progress bar with step indicators |
| `partials/block_cart_upsell.html` | Individual upsell product row |
| `configs/settings_schema.json` | Add Side Cart settings section |
| `sass/components/_sidecart.scss` or `css/sidecart.css` | Side cart styling |

**Important constraints:**
- No non-ASCII characters in JS files (platform processes through DTL engine)
- All cart mutations require CSRF token (`X-CSRFToken` header from `csrftoken` cookie)
- Cart ID stored in both `sessionStorage` and cookie for cross-tab persistence
- Use `Intl.NumberFormat` for client-side currency formatting (matches browser locale)
- Only push changed files with ntk, never the entire theme

---

## Deployment Workflow

### Standard (SCSS themes)

```bash
ntk watch    # Watches for changes and auto-pushes
```

ntk watch handles Sass compilation automatically — `.scss` files in `sass/` are compiled to `assets/` before pushing.

### Tailwind Themes

Tailwind themes orchestrate both the CSS compiler and ntk via `package.json` scripts — one command runs everything:

```json
{
  "scripts": {
    "dev": "npm run tailwind:watch & ntk watch",
    "build": "npm run tailwind:build",
    "tailwind:watch": "npx @tailwindcss/cli -i css/input.css -o assets/main.css --watch",
    "tailwind:build": "npx @tailwindcss/cli -i css/input.css -o assets/main.css --minify"
  }
}
```

```bash
npm run dev    # Runs Tailwind watcher + ntk watch concurrently
```

ntk watch detects the Tailwind CSS output (`assets/main.css`) changes and pushes automatically. If you see partial CSS on the store (rare race condition), manually push after Tailwind finishes:
```bash
ntk push assets/main.css
```

### CSS Compatibility Pipeline (Required for Tailwind themes)

The platform's SCSS compiler rejects modern CSS features. **Every Tailwind build must run through `sass-compat.py`** — this is not optional. The script strips/converts: `@property` rules, `oklch()` → hex, `color-mix()`, `@layer`, logical properties (`margin-inline`), `:is()`/`:where()` pseudo-classes, and media range syntax (`width >= 768px`).

Add the compat step to the build pipeline:

```json
{
  "scripts": {
    "dev": "npm run tailwind:watch & ntk watch",
    "build": "npm run tailwind:build && npm run compat",
    "tailwind:watch": "npx @tailwindcss/cli -i css/input.css -o assets/main.css --watch",
    "tailwind:build": "npx @tailwindcss/cli -i css/input.css -o assets/main.css --minify",
    "compat": "python3 scripts/sass-compat.py"
  }
}
```

The compat script strips `@property` rules, converts `oklch()` to hex, and replaces `color-mix()` — ensuring broad browser support on the NEXT Commerce platform.

---

## Debugging

### Common ntk Errors

| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` | Bad API key or expired | Regenerate key in Dashboard > Settings > API Keys |
| `404 Not Found` | Wrong theme_id or store URL | Run `ntk list` to verify, check `config.yml` |
| `File not in valid path` | File outside the 8 recognized directories | Check file is in assets/, checkout/, configs/, layouts/, locales/, partials/, sass/, or templates/ |
| `Connection refused` | Store URL wrong or store offline | Verify `store` value in config.yml uses the `.29next.store` domain |

### Template Syntax Errors

Template errors show as 500 pages on the storefront. Common causes:
- Unclosed tags (`{% if %}` without `{% endif %}`)
- Wrong variable paths (`product.title` vs `product.get_title`)
- Missing `{% load %}` tags for custom template tag libraries
- Using `{% url 'name' %}` with wrong URL name — check the URLs reference

To debug: check the store's `.29next.store` domain (bypasses caching), look at the browser's network tab for 500 responses, and read the error message in the response body.

### GraphQL Issues

- **403 Forbidden:** Missing or invalid CSRF token. Ensure you're reading the `csrftoken` cookie and sending it as `X-CSRFToken` header.
- **Queries return null:** Check field names against the GraphQL schema. Use the interactive explorer at `https://{store}.29next.store/api/graphql/` (GraphiQL).

### Cache Issues

If changes aren't appearing:
1. Are you on the `.29next.store` domain? (Mapped domains cache for 5 min)
2. Try appending `?skip_cache` or a unique query string
3. For asset changes, hard-refresh the browser (Cmd+Shift+R)
4. Template changes pushed via ntk should bust the cache automatically — if not, wait ~30 seconds and retry

---

## Supported File Types

ntk recognizes these extensions:

- **Templates:** `.html`
- **Config:** `.json`
- **Styles:** `.css`, `.scss`
- **Scripts:** `.js`
- **Media:** `.woff2`, `.gif`, `.ico`, `.png`, `.jpg`, `.jpeg`, `.svg`, `.eot`, `.ttf`, `.woff`, `.webp`, `.mp4`, `.webm`, `.mp3`, `.pdf`

Files with other extensions are silently ignored by ntk push/watch.
