# Campaign Provisioning

Creates a new campaign in your store's Campaigns App, with its packages,
shipping and offers, from a plan you approve first. Your AI assistant looks at
what your store already has, suggests how the campaign should be set up, and
shows you every change it will make and the prices your customers will pay.
Nothing is created until you say go. At the end you get the campaign key and
the package IDs your funnel pages need.

Use it when a new product or offer needs a campaign to exist on your store
before the funnel is built. It only creates new campaigns. Changes to a
campaign you already have are made in the Campaigns App dashboard.

## What You Need

- **Your store's web address**: for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store**: created in your store admin under
  Dashboard > Settings > API Access. It needs six permissions: read and write
  campaigns, read the catalogue, read gateways, and read and write metadata.
  Your assistant tells you if the key doesn't work. A key that is missing a
  permission has to be created again with all six; trying the same key again
  won't help.
- **The Campaigns App** installed on your store.
- **A computer with Python 3.9 or newer and a bash shell.** On Windows, use
  WSL, or ask your assistant to run the Python program directly.

You never type or paste the API key into the chat. Your assistant creates a
private settings file on your computer, you paste the key into that file with
a normal text editor, and the skill's program reads it from there. The key
stays on your machine, is saved per store, and is remembered for next time.

### Decisions only you can make

Some choices are commercial calls, so your assistant asks you for them and
never guesses. It works out the rest from your answers.

| You decide | The assistant works out |
|------------|-------------------------|
| The hero product the campaign sells | The package list |
| Low or high cost-to-consumer | The tier offers |
| The anchor price the discounts come off | The exit voucher |
| Shipping price and countries | The campaign settings |
| Any add-on or upsell products, and their prices | The order the requests go in |

Cost-to-consumer is what your customer pays, not what the product costs you.
A low-priced product gets Buy 1, Buy 2 and Buy 3 offers, with a bigger
discount the more units someone buys. A high-priced product sells one unit at
a time, with add-ons at checkout and upsells after the order. The anchor price
is the full price the discounts are taken from, and it isn't always the price
in your catalogue.

> [!IMPORTANT]
> The first campaign on a store needs eleven metadata definitions, created
> once. Orders still go through without them, but you can't filter, export or
> report on which campaign each order came from. Your assistant checks for them
> and asks you before creating any, and your API key
> needs its metadata permissions for that step. Later campaigns on the same
> store reuse them.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant, or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-create-campaign for mystore: create a campaign for the Photo
> Bracelet at 49.95, low cost-to-consumer, standard shipping 6.95.

It then walks you through, step by step:

1. **Setup**: confirms your store, sets up the private file for your API key,
   and checks the key works.
2. **Discovery**: reads what your store already has, such as products, payment
   settings, shipping methods and existing campaigns. Nothing changes in your
   store.
3. **Your decisions**: asks for any choice from the table above that your
   request didn't already answer.
4. **Plan review**: shows you every request it will send and the prices
   customers will pay, before anything is created. You can change anything and
   get a fresh plan.
5. **Live run**: only after you say go. It creates the campaign, then its
   packages, shipping methods and offers.
6. **Verification**: reads everything back from your store and prices test
   carts to the cent, including each tier, a cart with mixed variants, the exit
   voucher and shipping.
7. **Hand-off**: tells you where the campaign key is saved, lists the package
   IDs and offer codes your funnel needs, and lists the dashboard steps left to
   do: allowed domains, PayPal account linking and the Map Builder.

### Example prices

With a 49.95 anchor price and the standard tiers, the product price before
shipping comes out like this:

| Offer | Units | Discount off 49.95 | Price each | Total |
|-------|-------|--------------------|------------|-------|
| Buy 1 | 1 | 50 percent | 24.97 | 24.97 |
| Buy 2 | 2 | 55 percent | 22.48 | 44.96 |
| Buy 3 | 3 | 60 percent | 19.98 | 59.94 |

The 6.95 shipping charge is added at checkout. The exit voucher takes a further
10 percent off the product price.

## Safety

- **Nothing is created until you approve the exact plan.** The approval is tied
  to the plan file's fingerprint, so a changed plan needs a new approval.
- **It only ever creates a new campaign.** It never edits a campaign that
  already exists.
- **An interrupted run can be recovered.** It can pick up where it left off
  without creating anything twice, or remove only what it created.
- It paces its requests to stay within your store's request limit.
- The campaign key is saved in a protected file on your computer and is never
  shown in the chat.
- The plan and the results stay on your machine.

## Updating this skill

Ask your assistant which version you have. It can read it from the skill
itself.

If you installed from a copy of the skills repository, update that copy and run
the installer's status check. It marks the skill as out of date when a newer
version exists, and the installer's install command brings it up to date. If
you installed without a copy, the skills command-line tool has an update
command that refreshes it. Your assistant can run either one for you.

Release notes, including any change to how the skill works, are in the pull
request that shipped each version. You can browse the
[merged pull requests for this skill](https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-create-campaign).

Nothing notifies you when a new version comes out, so check before you start a
new campaign.
