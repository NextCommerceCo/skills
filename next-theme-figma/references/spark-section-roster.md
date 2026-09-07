# Spark Section Roster

The JSON roster is canonical; this file is generated. Regenerate it with `node <skill-dir>/scripts/theme-figma.js render-roster --write`.

| Design family | Spark section | Status | Tier | Usual classification | Alternates | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| hero | section_hero | shipped | 0 | semantic-rebuild |  |  |
| promo | section_promo_banner | shipped | 0 | semantic-rebuild |  |  |
| sticky | section_promo_banner | shipped | 0 | semantic-rebuild |  | sticky bar |
| features | section_value_props | unshipped | 1 | semantic-rebuild | section_benefit_grid |  |
| benefits | section_value_props | unshipped | 1 | semantic-rebuild | section_benefit_grid |  |
| icons | section_value_props | unshipped | 1 | semantic-rebuild | section_benefit_grid |  |
| howto | section_process_steps | unshipped | 1 | semantic-rebuild |  |  |
| compare | section_comparison_table | unshipped | 1 | semantic-rebuild |  |  |
| faq | section_faq | unshipped | 1 | semantic-rebuild |  |  |
| reviews | section_testimonials | unshipped | 1 | semantic-rebuild |  |  |
| testimonials | section_testimonials | unshipped | 1 | semantic-rebuild |  |  |
| ugc | section_testimonials | unshipped | 1 | composed-asset |  | UGC grids usually export as a composed asset |
| media | section_press_logos | unshipped | 1 | composed-asset |  | press/logo strips; semantic-rebuild when logos are live links |
| guarantee | section_cta_band | unshipped | 1 | semantic-rebuild |  | Spark's roster has no guarantee banner; cta_band is the nearest roster entry |
| bottomcta | section_cta_band | unshipped | 1 | semantic-rebuild |  |  |
| results | section_image_text | unshipped | 1 | semantic-rebuild |  |  |
| beforeafter | section_image_text | unshipped | 1 | composed-asset |  | before/after pairs usually export as one composed asset |
| science | section_image_text | unshipped | 1 | semantic-rebuild |  |  |
| ingredients | section_image_text | unshipped | 1 | semantic-rebuild |  |  |
| problemsolution | section_image_text | unshipped | 1 | semantic-rebuild |  |  |
| nav | header | chrome | 0 | live-commerce-component |  |  |
| footer | footer | chrome | 0 | live-commerce-component |  |  |
| featured | section_featured_products | shipped | 0 | live-commerce-component | section_featured_product, section_featured_categories, section_on_sale |  |

## Resolution rules

- Unlisted families resolve to `unmapped` (never a guess).
- When a Spark name is listed only as an alternate, `family` reports the first roster row in file order that lists it.
- The table is advisory for the choice of target: a package author may pick a different roster section (for example an alternate) or leave a section `unmapped`, but `roster_status` must always agree with the roster's status for the chosen `spark_section`; the validator rejects contradictions.
- Both frame naming forms are accepted: `{family}{number}-{breakpoint}` and Spark's own `section_<name>-{breakpoint}`.
