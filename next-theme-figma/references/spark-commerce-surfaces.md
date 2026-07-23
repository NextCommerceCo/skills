# Spark Commerce Surfaces

Use this whenever the Figma design touches live commerce behavior. Spark/platform contracts often win over literal Figma pixels.

## Divergence Principle

Do not implement Figma literally where it would break storefront behavior. Record the intended divergence, the Spark/platform owner, and the implementation guardrail.

Allowed divergence statuses:

- `spark-wins`: keep Spark/platform behavior and adapt visual styling around it.
- `figma-wins-with-guardrails`: implement the design while preserving required Spark contracts.
- `needs-approval`: user/designer must choose.
- `blocked`: missing source, product data, app config, or platform capability.

## PDP Gallery And Media

Preserve:

- Real product media data.
- Gallery/carousel behavior.
- Selected variant media updates.
- Product image aspect-ratio and no-image fallbacks.

Figma product compositions may be references only. Do not replace PDP gallery behavior with a static image unless approved as a prototype.

## Variant Picker

Preserve:

- Real controls named `attr_<code>`.
- Values from `variant_form`.
- Selected variant state wiring.
- Labels and availability/disabled states.

Custom visual swatches/buttons must update the underlying real controls. Do not rename input names to match Figma copy.

## Price And Availability

Preserve:

- Live price bindings.
- Compare-at/retail price nodes.
- Availability/sold-out states.
- Currency formatting and locale behavior.

Static Figma prices are visual references unless the task is only a mockup.

## Add To Cart

Preserve:

- Real add-to-cart form.
- CSRF token.
- Quantity input or Spark quantity component.
- Submit action and selected variant payload.
- Loading/error/disabled states.

A Figma CTA can style the real submit button; it should not duplicate cart logic.

## Cart Drawer And Header State

Full-page caching means per-user state belongs client-side:

- Cart count.
- Cart line items and totals.
- Account/login state.
- Wishlists/saved items.

Do not put visitor-specific state into cached DTL markup. Use Spark Web Components/GraphQL patterns from `next-theme-dev`.

## Subscriptions And Memberships

Preserve subscription selectors, interval choices, selling-plan hooks, and membership/app surfaces when present. Treat Figma subscription UI as styling guidance around live controls, not as a replacement.

## Reviews, Ratings, And App Hooks

Preserve app hook locations for:

- Rating summary.
- Product reviews.
- Review CTA.
- Product info/footer hooks.
- View/add-to-cart analytics hooks.
- Loyalty, subscription, membership, or other app widgets.

If Figma shows static review stars or review cards where an app should render live content, record a divergence and choose whether Spark/app behavior or static editorial content owns that section.

## Product Cards And Backend Media

Product cards often need:

- Product title, price, sale state, image, URL, availability.
- Backend product images rather than exported Figma screenshots.
- Theme settings or dashboard collections/products.

Classify product card sections as `live-spark-component` unless they are explicitly editorial/static cards.

## Common Ledger Entries

Use entries like:

```json
{
  "surface": "PDP variant picker",
  "pages": ["/products/example"],
  "figma_expectation": "Large color swatch pills with marketing labels",
  "spark_platform_behavior": "Variant controls must submit attr_<code> values from variant_form",
  "decision": "figma-wins-with-guardrails",
  "implementation_guardrail": "Style labels around real radio inputs; do not rename input names",
  "status": "open"
}
```
