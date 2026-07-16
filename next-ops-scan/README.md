# Daily Ops Risk Scan

A **look-but-don't-touch** daily check of one Next Commerce store. It finds the
orders most likely to turn into customer complaints, missed refunds, or
payment disputes:

- **Incomplete orders** that probably need a refund review.
- **Rejected orders** that need someone to fix order data or fulfillment setup.
- **Shipments that look stuck or failed** (when the Delivery Tracking app is
  installed).

You get two files on your computer: an easy-to-read summary with recommended
next steps, and a spreadsheet version of the same list for filtering or
sharing. The scan never refunds, cancels, fulfills, edits, or messages anyone
— it hands your team a to-do queue, and people make the decisions.

## What You Need

- **Your store's web address** — for example, `mystore` if your store is at
  mystore.29next.store.
- **An API key for your store** — created in your store admin under
  **Dashboard > Settings > API Access**. For this scan it only needs permission
  to read orders — nothing else. Keep it private, and replace it if it's ever
  exposed.

You never type or paste the API key into the chat. Your assistant creates a
private settings file on your computer, you paste the key into that file with
a normal text editor, and the assistant reads it from there. The key is saved
per store and remembered for next time.

Shipment-related findings only appear if the Delivery Tracking app is
installed on your store.

## Install

See the [repo README](../README.md) for installation. If you're not sure how,
ask whoever set up your AI assistant — or ask the assistant itself.

## How to Use

Ask your AI assistant something like:

> Run next-ops-scan for my store and help me review today's risky order queues.

The assistant runs the scan, then walks you through the findings: what each
queue means, which orders look most urgent, and what the recommended manual
next step is for each. You can adjust how far back it looks and how many days
count as "stuck" — just say so in plain words.

Many teams run this every weekday morning and skim the summary with coffee.

## Safety

- **Read-only.** Nothing in your store is ever changed; the only thing written
  is the two report files on your computer.
- It's a queue, not a verdict — refund, reship, and escalation decisions stay
  with your team.
- The API key stays in its private file — never in the chat, never in the
  reports, never in shared docs.
