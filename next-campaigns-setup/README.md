# Campaign Setup and Continuation

Starts or continues a campaign from whatever you have: a funnel or store web
address, a design, an existing campaign repository, or a saved Map. Intake
follows the public
[agent setup procedure](https://developers.nextcommerce.com/agent-setup/prompt.md),
so any AI coding tool reads the same steps. When a new campaign-page-kit (CPK)
project is the right next step, this skill creates the brand folder from the
official starter tooling, downloads the starter template you choose, and fills
in the configuration: store name and web address, optional phone and
policy/support links, any analytics you want (Google Tag Manager, Facebook
Pixel), and the Campaign Cart API key when you have one.

**Where this skill stops:** it covers intake and first configuration only.
Building the actual pages from a design, and everything later in a campaign's
life, belongs to the separate
[campaigns-os](https://github.com/NextCommerceCo/campaigns-os) package and its
own skills. A project that already has a Campaigns OS Build Packet uses that
package's own setup skill instead of this one.

## What You Need

- **Node.js installed** — the standard tooling the campaign kit runs on. If
  you're not sure, your assistant can check for you.
- **Your campaigns project folder** — where campaign brands live on your
  computer. Your assistant asks for it if it isn't already configured.
- **Store details** — store name and web address; a support phone number and
  policy page links are optional but recommended.
- **Three decisions**: the brand name, the public web address path for the
  campaign, and which starter template to begin from.
- **The Campaign Cart API key** for the campaign, when it exists. Without it
  the preview runs against the starter templates' demo campaign, which is fine
  for reviewing layout; the real key is bound later.

| You choose | Example |
|-----------|---------|
| Brand name (lowercase, hyphens) | acme |
| Public route (the path in the campaign's web address) | grounding-mat |
| Starter template | a family from the live template catalog; apollo for most campaigns |

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-campaigns-setup — new campaign for brand acme, route
> grounding-mat, based on the apollo template.

Or, to start from something that already exists:

> Run next-campaigns-setup — build a campaign from https://example.com/offer.

For a new project it gathers the three decisions first, then collects all the configuration
details in one message, sets everything up, and finishes with a report and
next steps — typically starting the local preview so you can see the campaign
page load.

## Safety

- It refuses to overwrite a campaign folder that already exists.
- The API key and store details end up in the campaign's local configuration
  files as plain text — that's how the kit works — so review what you share if
  that project is pushed somewhere public.
- A preview made without your key shows the demo store's prices and checkout.
  Use it to look at layout, not as proof of your campaign, and don't place
  orders on it.
- If the scaffolding tool fails, the skill reports exactly what failed and
  stops — it never improvises a partial setup.
- Analytics are only wired up when you provide real tracking IDs — no
  placeholder tracking is ever added.
