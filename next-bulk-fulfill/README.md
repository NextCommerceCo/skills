# Bulk Fulfillment Tracking Sync

Fixes orders that are stuck showing **Processing** in your Next Commerce store
even though they already shipped. You give your AI assistant a spreadsheet of
order numbers and tracking numbers, and it marks each order as fulfilled,
attaches the tracking number, and (if you want) sends the customer their
shipping notification.

Use it when your fulfillment provider shipped the orders but the tracking
numbers never made it back into your store.

## What You Need

- **Your store's web address** — for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store** — created in your store admin under
  **Dashboard > Settings > API Access**. It needs permission to read and write
  fulfillment orders. Your assistant checks this for you and tells you if the
  key doesn't work.
- **The spreadsheet** (CSV file) from your fulfillment provider, with a column
  of order numbers, a column of tracking numbers, and ideally a column naming
  the shipping carrier for each package. Column names don't have to match
  exactly — your assistant figures them out.

You never type or paste the API key into the chat. Your assistant creates a
private settings file on your computer, you paste the key into that file with
a normal text editor, and the assistant reads it from there. The key stays on
your machine, is saved per store, and is remembered for next time.

### Example spreadsheet

| Order Number | Tracking Number | Carrier |
|--------------|-----------------|---------|
| 596338 | 9200190312345678901234 | usps |
| 596105 | UUS67F7550336739650 | uniuni |
| 595913 | 1Z999AA10123456784 | ups |

The carrier names must be ones Next Commerce supports — your assistant checks
the current list ([here it is](https://developers.nextcommerce.com/docs/admin-api/reference/fulfillment/fulfillmentsCreate)
if you're curious) and flags anything it doesn't recognize.

> [!IMPORTANT]
> **Carriers matter.** The carrier is what gives your customer a working
> "track my package" link and powers delivery statuses, notifications, and
> reporting. Ask your fulfillment provider to include a carrier column in the
> export — their records are the truth. Without that column, the only fallback
> is having the AI guess the carrier from the tracking number's format, which
> is not reliable.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-bulk-fulfill — orders on mystore are stuck in Processing and I have
> a spreadsheet of tracking numbers from our fulfillment provider.

It then walks you through, step by step:

1. **Setup** — confirms your store, checks the API key works, and reads your
   spreadsheet.
2. **Carriers** — if your file has a carrier column, it uses that. If not, it
   stops and recommends getting a corrected file from your provider before
   offering the unreliable guess-from-the-tracking-number fallback. Nothing is
   ever sent based on a guess you haven't approved.
3. **Practice run** — first it only looks orders up and reports what would
   happen: how many are ready to update, how many were already fulfilled or
   canceled, and how many need a human to look at them. Nothing changes in
   your store yet.
4. **Live run** — only after you say go. Customers get their shipping
   notification email by default; you can ask to turn notifications off.
5. **Results** — a summary of what was updated, plus a short list of any
   orders that need manual attention in the store admin, and a results file
   you can keep for your records.

## Safety

- **Nothing changes in your store until you approve the live run.** The
  practice run is always first.
- It works at a polite pace the store's system allows, so big files just take
  a little longer instead of causing errors.
- If an order is split across warehouses, it's flagged for a human instead of
  guessed at.
- Results contain order and tracking details only — never customer names,
  addresses, or payment information.
- If the run is interrupted, it can pick up where it left off without
  double-updating anything.
