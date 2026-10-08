# Campaign Provisioning

Creates a campaign in your store's Campaigns App, with its packages, shipping
and offers, from a plan you approve first, and changes a campaign you already
have. Your AI assistant looks at what your store already has, suggests how the
campaign should be set up, and shows you every change it will make and the
prices your customers will pay. Nothing is created or changed until you say go.
At the end you get the campaign key and the package IDs your funnel pages need.

Use it when a new product or offer needs a campaign to exist on your store
before the funnel is built, and later when a price, an offer or a setting on
that campaign has to change. A quick change to a campaign it built, such as a
price, an offer's percentage, which products an offer covers, pausing an offer
or adding one, it makes in place. For a campaign someone else built, or for the
campaign's own settings, its shipping prices, or adding and removing packages,
it takes the campaign over first and then works from a reviewed list of changes.

## What You Need

- **Your store's web address**: for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store**: created in your store admin under
  Dashboard > Settings > API Access. It needs seven permissions: read the
  store settings, read and write campaigns, read the catalogue, read gateways,
  and read and write metadata. You don't need to give it full access. If the
  key doesn't work, your assistant tells you which permission is missing. A key
  that is missing a permission has to be created again with all seven; trying
  the same key again won't help. Changing a campaign that already exists uses
  the same key and only its campaign permissions, so there is nothing extra to
  create for that.
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
| Quantity Buy 1/2/3, buy-X-get-Y, or gift-with-purchase | How that maps onto percentage offers the Campaigns App actually has |
| Low or high cost-to-consumer | The quantity-tier offers (Buy 1/2/3 stays 50/55/60 unless you change it) |
| The anchor price the discounts come off | The exit voucher |
| For buy-X-get-Y: paid and free quantities | The single count threshold and percentage (labeled as an approximation) |
| For a gift: which variant, its price, silent-add vs customer pick | The gift package and the 100% offer scoped only to it; funnel auto-add is a later handoff |
| Shipping price and countries | The campaign settings |
| Whether discounted prices end in .95, .97, .99 or whole dollars, or are left unrounded | Applying it to every discount offer except the free gift and free shipping |
| Any add-on or upsell products, and their prices | One upsell discount per product covering all its variants, and the order the requests go in |

Cost-to-consumer is what your customer pays, not what the product costs you.
A low-priced product gets Buy 1, Buy 2 and Buy 3 offers, with a bigger
discount the more units someone buys. A high-priced product sells one unit at
a time, with add-ons at checkout and upsells after the order. The anchor price
is the full price the discounts are taken from, and it isn't always the price
in your catalogue.

A campaign uses each product variant in one package and each of your store's
shipping methods once. If you want the same product or the same shipping
method at a different price, that difference comes from a discount, not from a
second copy. For example, offering your main product again after the order at
a lower price is done with a discount code on the package you already have.
Shipping works the same way: cheaper or free shipping on bigger orders is a
shipping discount on the one method, not a second version of it.

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

> Run next-campaigns-create for mystore: create a campaign for the Photo
> Bracelet at 49.95, low cost-to-consumer, standard shipping 6.95.

For a campaign you already have, ask for the change instead, for example "change
the Buy 2 price on the Photo Bracelet campaign to 21.95". That follows the
shorter path in "Changing a campaign you already have" below.

For a new campaign it walks you through, step by step:

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
   IDs and offer codes your funnel needs (short codes such as `ALWAYSNEAR10`:
   the product's distinctive name plus the discount), and lists the dashboard steps left to
   do: allowed domains, PayPal account linking and the Map Builder.

### Example prices

With a 49.95 anchor price, the standard tiers and no price rounding, the
product price before shipping comes out like this:

| Offer | Units | Discount off 49.95 | Price each | Total |
|-------|-------|--------------------|------------|-------|
| Buy 1 | 1 | 50 percent | 24.97 | 24.97 |
| Buy 2 | 2 | 55 percent | 22.48 | 44.96 |
| Buy 3 | 3 | 60 percent | 19.98 | 59.94 |

The 6.95 shipping charge is added at checkout. The exit voucher takes a further
10 percent off the product price.

Buy 2 get 1 free is not a free unit. The Campaigns App only has percentages, so
that deal is one offer at 33.33% off every unit once the cart has 3. At 3 units
of 49.95 the payable is 99.90 (pay for two). That match holds for this price
with no rounding; other prices can land a few cents off, and rounding to .95
would make it 101.85. A fourth unit still gets 33.33%
off, which is more discount than a repeating BOGO. A gift with purchase is a
separate gift package with a 100% offer that applies whenever that package is
in the cart, not when the hero quantity or spend is met. The assistant will
say so before you approve.

> [!NOTE]
> Combining Buy 1 at 50% with buy 2 get 1 free on the same products does not
> work: the engine keeps the larger percentage, so three units would take 50%.
> Buy 1 at the list price plus the BOGO offer is the supported mix.

## Changing a campaign you already have

Ask in plain words for what you want different. Your assistant works out which
settings that means, shows you the exact list before anything is sent, and waits
for your yes.

| You ask for | What changes on the campaign |
|-------------|------------------------------|
| "Make Buy 2 a bit cheaper" | the discount on that offer |
| "Drop the price of the main package to 22.95" | that package's price |
| "Shipping is 7.95 now" | the shipping price |
| "Stop the exit discount from running" | that offer is switched off, but kept, so you can switch it back on |
| "Add the warranty add-on at 9.95" | a new package, and the funnel page gets a new ID to use |
| "Add a discount on the add-on" | a new offer |
| "We also ship to Canada now" | the shipping countries |
| "Rename the campaign" | the campaign name |
| "Remove the Buy 3 offer for good" | that offer is deleted, which needs a separate yes |

Four things to know before you ask:

- **You see every change first.** Your assistant shows you a numbered list of
  exactly what will be sent, and what each value is now versus what it becomes.
  Nothing goes to your store until you approve that list.
- **Deleting needs its own yes.** Changing a price and deleting an offer are not
  the same kind of decision. Anything that removes a package, a shipping method
  or an offer is called out on its own and needs a second, explicit approval.
  Deleted things do not come back, and anything recreated afterwards gets a new
  ID, so your funnel pages have to be repointed.
- **Changes you made in the dashboard are kept.** If you changed something
  yourself since the last time the assistant looked, that value stays as you set
  it and is listed for you as kept. The only thing that stops the run is you and
  the assistant having changed the same setting to two different values, which
  it reports rather than picking a winner.
- **The campaign key never changes.** Updating a campaign does not change the
  key your funnel pages use.

> [!IMPORTANT]
> A campaign built in the Campaigns App dashboard has to be taken over before it
> can be changed this way. Your assistant lists the campaigns on your store and
> asks you to confirm which one, by its ID number, its name and the date it was
> created. Taking it over reads the campaign and writes a local record of it;
> nothing on your store changes. If the campaign is in a shape the skill cannot describe safely,
> such as two offers with the same name, it stops and tells you what to fix in
> the dashboard first.

> [!IMPORTANT]
> One thing cannot be changed at all: the campaign's currency. That is fixed
> when the campaign is created, so a different currency needs a new campaign.
> A product variant also cannot be swapped on an existing package, and a
> shipping method cannot be moved to a different shipping code: in both cases
> the assistant adds the new one and removes the old one, which needs the
> delete approval.

## Safety

- **Nothing is created or changed until you approve the exact plan.** The
  approval is tied to the fingerprint of the file you reviewed, so a changed
  plan or a changed list of requests needs a new approval.
- **It only touches the campaign you pointed it at.** It keeps its own record of
  the campaign it created or that you handed over, and it refuses to touch
  anything that record does not cover, including anything added to the campaign
  from elsewhere. An offer you added yourself in the dashboard is listed and
  left exactly as it is.
- **Changes are approved the same way.** Before changing a campaign it shows
  each value before and after, and the prices your customers will pay before and
  after. A quick in-place change also asks whether shoppers are on the campaign
  right now, and keeps a record of the old values so it can be undone.
- **The campaign keeps its key and its IDs**, so your funnel pages keep working.
  A few things cannot be changed at all, such as the currency or which product a
  package sells. For those it tells you which one, and that the campaign would
  have to be removed and created again.
- **Deletions are a separate approval**, and the campaign itself is never
  deleted by a change. An in-place change never deletes anything: an offer is
  paused instead.
- **An interrupted run can be recovered.** It can pick up where it left off
  without creating anything twice, or remove only what it created. An update
  that stopped halfway checks the campaign against what it had already done
  before it carries on, and stops if someone changed something in the dashboard
  in the meantime.
- **It checks your store again just before sending an update**, and refuses if
  anything moved since you read the list. You get a fresh list to approve.
- It paces its requests to stay within your store's request limit.
- The campaign key is saved in a protected file on your computer and is never
  shown in the chat.
- The plan and the results stay on your machine.

## Updating this skill

Ask your assistant which version you have. It can read it from the skill
itself.

Version 0.8.0 added in-place edits: a price, an offer's percentage, an offer's
condition or scope, pausing an offer or adding one, on a campaign this skill
built, with a record of the old values so the change can be undone.

Version 1.0.0 builds on that and adds the ability to change a campaign the skill
did not build: taking over a campaign made in the dashboard, showing you a
reviewed list of changes, and applying it behind its own approval. In-place
edits are unchanged and are still the quick route for a campaign this skill
built. Everything the earlier versions did is unchanged too, and an existing
campaign created by this skill keeps working with the files already on your
machine. It is a major version because the skill can now change and delete
things on a campaign someone else created, which is a change to what it is
allowed to do rather than a new convenience.

If you installed from a copy of the skills repository, update that copy and run
the installer's status check. It marks the skill as out of date when a newer
version exists, and the installer's install command brings it up to date. If
you installed without a copy, the skills command-line tool has an update
command that refreshes it. Your assistant can run either one for you.

This skill used to be called next-create-campaign. If you installed it under
that name, install next-campaigns-create and then remove the old copy, because
the installer will not update it. A copy under the old name can't find itself
in the catalog any more, so its update check will say it couldn't check. Your
assistant can do the reinstall for you.

Release notes, including any change to how the skill works, are in the pull
request that shipped each version. You can browse the
[merged pull requests from version 0.6.0 on](https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-campaigns-create)
and the
[merged pull requests for earlier versions](https://github.com/NextCommerceCo/skills/pulls?q=is%3Apr+is%3Amerged+next-create-campaign),
which carry the old name.

From version 0.5.0, the skill tells your assistant to check for a newer
version before it starts work. If one exists, your assistant shows you which version you have, which is
newest, and the command that updates your copy. You can keep working on the
version you have. The skill never updates itself: you run the update, and it
replaces the skill's folder, so copy out anything you changed in it first.

The check makes one small request to GitHub at most once a day. If you are
offline it says it couldn't check and carries on. Copies older than 0.5.0 don't
have the check, so update those once by hand.
