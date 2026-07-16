# New Campaign Setup

Sets up a brand-new campaign-page-kit (CPK) campaign from nothing to
ready-to-preview in one pass. It creates the brand folder from the official
starter tooling, downloads the starter template you choose, and then fills in
all the configuration: the Campaign Cart API key, store name and web address,
optional phone and policy/support links, and any analytics you want (Google
Tag Manager, Facebook Pixel).

**Where this skill stops:** it covers creating and first-configuring the
campaign only. Building the actual pages from a design, and everything later
in a campaign's life, belongs to the separate
[campaigns-os](https://github.com/NextCommerceCo/campaigns-os) package and its
own skills. If you're working inside a Campaigns OS flow, follow that
package's handoff — treat this skill as the setup step, not a second rulebook.

## What You Need

- **Node.js installed** — the standard tooling the campaign kit runs on. If
  you're not sure, your assistant can check for you.
- **Your campaigns project folder** — where campaign brands live on your
  computer. Your assistant asks for it if it isn't already configured.
- **The Campaign Cart API key** for the store this campaign sells from.
- **Store details** — store name and web address; a support phone number and
  policy page links are optional but recommended.
- **Three decisions**: the brand name, the public web address path for the
  campaign, and which starter template to begin from.

| You choose | Example |
|-----------|---------|
| Brand name (lowercase, hyphens) | acme |
| Public route (the path in the campaign's web address) | grounding-mat |
| Starter template | olympus, demeter, limos, landing, and several shop/multi-step variants |

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-campaigns-setup — new campaign for brand acme, route
> grounding-mat, based on the olympus template.

It gathers the three decisions first, then collects all the configuration
details in one message, sets everything up, and finishes with a report and
next steps — typically starting the local preview so you can see the campaign
page load.

## Safety

- It refuses to overwrite a campaign folder that already exists.
- The API key and store details end up in the campaign's local configuration
  files as plain text — that's how the kit works — so review what you share if
  that project is pushed somewhere public.
- If the scaffolding tool fails, the skill reports exactly what failed and
  stops — it never improvises a partial setup.
- Analytics are only wired up when you provide real tracking IDs — no
  placeholder tracking is ever added.
