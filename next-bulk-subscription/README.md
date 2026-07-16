# Bulk Subscription Actions

Applies the same change to a whole list of subscriptions in your Next Commerce
store at once. You give your AI assistant a spreadsheet of subscription IDs
and say what should happen to them.

Common jobs:

- Pause a group of subscriptions until a date (or indefinitely).
- Cancel a group, with the cancellation reason recorded.
- Move renewal dates to specific new dates.
- Change how often subscriptions bill (for example, monthly to every 3 months).
- Move subscriptions off a retired payment gateway.
- Fix shipping or billing addresses in bulk.

## What You Need

- **Your store's web address** — for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store** — created in your store admin under
  **Dashboard > Settings > API Access**. It needs permission to read and write
  subscriptions. Your assistant checks it works before doing anything.
- **The spreadsheet** (CSV file) with a column of subscription IDs. It can also
  carry per-row values — for example a different pause-until date per
  subscription. If your file is an Excel file, your assistant will help convert
  it first.

You never type or paste the API key into the chat. Your assistant creates a
private settings file on your computer, you paste the key into that file with
a normal text editor, and the assistant reads it from there. The key is saved
per store and remembered for next time.

> [!IMPORTANT]
> **Pausing indefinitely isn't forever.** Subscriptions paused without an end
> date are automatically canceled by the platform after 6 months if nobody
> resumes them. If you want them back, set a resume date or diary a check-in.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-bulk-subscription — pause these subscriptions on mystore until
> August 1st. Here's the file of subscription IDs.

It then walks you through, step by step:

1. **Setup** — confirms your store, checks the API key, and reads your file.
2. **What to change** — you pick the action (pause, cancel, new renewal date,
   billing frequency, and so on) and see exactly what will be applied before
   anything runs.
3. **Practice run** — it works out every change without sending any of them,
   and reports what would happen row by row. Nothing changes yet.
4. **Live run** — only after you say go.
5. **Check and report** — it spot-checks a few updated subscriptions against
   the live store and gives you a summary plus a results file for your records.

## Safety

- **Nothing changes in your store until you approve the live run.** The
  practice run is always first.
- Only recognized, approved kinds of changes can be sent — anything unexpected
  is refused rather than guessed at.
- Dates are used exactly as you supply them — it never computes "30 days from
  now" on its own or guesses timezones.
- Pausing uses the platform's official pause feature, not a shortcut.
- If a run is interrupted and restarted, already-completed rows are skipped
  safely — and if you changed what's being applied, nothing is skipped by
  mistake.
- It works at a polite pace the store's system allows.
- Results contain subscription IDs and outcomes only — never addresses,
  payment details, names, or emails.
