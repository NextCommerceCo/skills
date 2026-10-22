# Campaign Setup and Continuation

Start from a funnel URL, design, existing campaign repository, or saved Map.
Your assistant follows the public [agent setup procedure](https://developers.nextcommerce.com/agent-setup/prompt.md),
records what is known and what still needs deciding, and selects the next tool.
For a new Page Kit project, this skill scaffolds and configures the campaign.
For an existing build, it preserves the builder and deployment workflow.

You can begin without a store API key. The first result is a brief, a proposed
page flow, a list of commerce dependencies, and a separate local visual concept.
It includes enough context for another agent session to continue.

> [!IMPORTANT]
> Page Kit 0.3.0's no-key scaffold can retain demo commerce and tracking settings.
> The skill uses a separate static concept until it verifies a released inert
> preview feature. The concept cannot take orders and does not prove that
> checkout, prices, shipping, payment or tracking work.

## What You Need

- A source URL, design, repository path, saved Map, or existing handoff.
- A local working folder. An existing repository keeps its own conventions.
- Any known decisions about the product, page flow, brand and offers. Your
  assistant asks only for choices the supplied inputs leave open.
- Later, for commerce integration: the selected NEXT campaign, its Campaign
  Cart API key and verified product/package references. Store Admin API access
  is separate; no credentials are needed for the first visual concept.

New projects use a template from the current public catalog. Apollo is the
current recommendation for most single-step campaigns; Apollo Multi-variant
is the recommendation for variant products. Your chosen behavior determines
whether a different family fits better.

## Install

See the [repo README](https://github.com/NextCommerceCo/skills#quick-start) for
installation. Load only the skills needed for the current step.

## How to Use

Ask your assistant:

> Use this funnel URL to propose a NEXT campaign and a local visual concept.
> Keep the store untouched and leave a handoff another session can continue.

Or:

> Continue this campaign repository. Preserve its build and deploy workflow,
> inspect the existing brief and evidence, and tell me the next safe step.

A cloned repository keeps the source campaign's configuration and evidence.
The assistant checks those separately; copying a repository does not create
a new Campaigns App campaign or transfer its QA result.

## Where This Skill Stops

[Campaign Provisioning](https://github.com/NextCommerceCo/skills/tree/main/next-campaigns-create)
creates, adopts, edits, updates or clones Campaigns App configuration from a
reviewed plan. It reads existing catalogue products and variants; product
creation or import is a separate prerequisite.

[Campaigns OS](https://github.com/NextCommerceCo/campaigns-os) owns CampaignSpec,
build, Polish and QA. Once a Build Packet exists, follow its matching bundled
skills, including `next-campaigns-os-setup`, instead of starting setup again.
Existing builds do not have to move to Page Kit just to record their next step.

## Safety

- Existing campaign folders are never overwritten by scaffolding.
- A no-write task stays local: no campaign changes, cart/order probes,
  published records or deployment.
- Configuration files can hold campaign credentials. Do not paste them into
  chat or include them in a public handoff.
- Analytics are enabled only for declared providers and real IDs. Copied demo
  values are removed before a configured campaign is opened.
- The handoff separates observed source content, proposed behavior and tested
  behavior. Unverified commerce remains explicit.
