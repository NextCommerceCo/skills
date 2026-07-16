# New Campaign Setup

End-to-end setup for a new campaign-page-kit (CPK) campaign. In one pass it:

1. **Scaffolds** — runs `campaign-init --non-interactive` to create the brand
   folder, download a starter template, seed `_data/campaigns.json`, and install
   the AI context file.
2. **Configures** — wires up `assets/config.js` and `campaigns.json` with the
   Campaign Cart API key, store details, optional policy/support links, and any
   declared analytics (GTM, Facebook Pixel).

Scope boundary: this skill covers repo bootstrap and first configuration only.
Page wiring from a spec/design and campaign lifecycle work belong to the
[`campaigns-os`](https://github.com/NextCommerceCo/campaigns-os) package skills.

## Requirements

- **Node.js + npm** — `campaign-init` ships with the `next-campaign-page-kit`
  npm package (v0.1.1+).
- **A CPK project root** — set the `CPK_ROOT` environment variable or provide the
  path when asked.
- **Campaign Cart API key** for the target store.
- **Store details** — store name and URL; phone and policy/support URLs are
  optional but recommended.

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-campaigns-setup
```

## How to Use

Ask your AI tool something like:

> Run /next-campaigns-setup — new campaign for brand `acme`, route slug
> `grounding-mat`, based on the `olympus` template.

You'll be asked for three things up front:

| Input | Example |
|-------|---------|
| Brand name (lowercase, hyphens) | `acme` |
| Public route slug | `grounding-mat` |
| Starter template | `demeter`, `limos`, `olympus`, `olympus-mv-single-step`, `olympus-mv-two-step`, `shop-single-step`, `shop-three-step`, or `landing` |

Then the config inputs (API key, store name/URL, optional phone, policy links,
and tracking IDs) are gathered in a single message and written in one pass.

The skill finishes with a report and next steps — typically `npm run dev` to
verify the campaign page loads and the SDK initialises.

## Safety

- Refuses to overwrite an existing campaign folder.
- Writes the API key and store details into local plaintext config
  (`config.js` / `campaigns.json`) — review before committing that repo.
- If `campaign-init` fails, the skill reports the exact exit code and stops; it
  never falls back to manual patching.
- Tracking IDs are only enabled when real values are provided — no placeholder
  analytics wiring.
