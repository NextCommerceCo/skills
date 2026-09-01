# Bulk Fulfillment Order Move

Moves batches of orders from one warehouse or fulfillment provider to another
in your Next Commerce store. Typical use: you're switching fulfillment
providers, and all the open orders sitting at the old provider need to be
reassigned to the new one before it can start shipping.

You can point it at either:

- **A spreadsheet of order numbers** — it moves exactly those orders.
- **A list of products or SKUs** — it finds every order at the old location
  containing those items and moves them, no spreadsheet needed.

Orders the old provider has already started working on are handled properly:
the skill asks the old location to release each one, waits for the release to
be accepted, and only then moves it.

## What You Need

- **Your store's web address** — for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store** — created in your store admin under
  **Dashboard > Settings > API Access**. It needs permission to read and write
  fulfillment orders and to read locations. Your assistant checks it works
  before doing anything.
- **The list of what to move** — a spreadsheet (CSV) of order numbers, or just
  the product names/SKUs. If your file is an Excel file, your assistant will
  help convert it first.

You never type or paste the API key into the chat. Your assistant creates a
private settings file on your computer, you paste the key into that file with
a normal text editor, and the assistant reads it from there. The key is saved
per store and remembered for next time.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-bulk-move — move these orders from Provider A to Provider B on
> mystore. Here's the file of order numbers.

It then walks you through, step by step:

1. **Setup** — confirms your store, checks the API key, shows you the list of
   warehouse locations, and asks you to pick the "from" and "to" locations.
2. **Practice run** — first it only classifies every order: ready to move,
   needs the old provider to release it first, already moved, already shipped,
   not found, or needs a human decision. Nothing changes in your store yet.
3. **Live run** — only after you say go.
4. **Results** — a summary plus a results file you can keep for your records,
   which also lets an interrupted run pick up where it left off.

## Safety

- **Nothing changes in your store until you approve the live run.** The
  practice run is always first.
- Before moving any order, it double-checks that the destination can actually
  take it.
- It re-checks each order's current state right before touching it — stale
  information is never acted on.
- It works at a polite pace the store's system allows.
- It only ever talks to your Next Commerce store — no other websites — unless
  you explicitly approve a custom admin domain.
- Results contain order details only — never customer names, addresses, or
  payment information.
