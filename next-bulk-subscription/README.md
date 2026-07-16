# Bulk Subscription Actions

Applies the same subscription action or partial update to a list of Next Commerce
subscription IDs from a flat file. Uses the official action endpoints where they
exist (pause, cancel) and the `subscriptionsPartialUpdate` endpoint for field
edits.

Common operations:

- Pause a cohort until a date (or indefinitely) via the official pause endpoint.
- Cancel a cohort with a recorded cancellation reason.
- Set explicit `next_renewal_date` values while leaving status active.
- Change billing cadence (`interval` / `interval_count`).
- Migrate subscriptions off a retired payment gateway.
- Bulk-fix shipping/billing addresses.

## Requirements

- **Python 3** — the bundled executor uses only the standard library.
- **A Next Commerce store** and its subdomain.
- **Admin API token** with `subscriptions:read` and `subscriptions:write` scopes,
  created at **Dashboard > Settings > API Access**.
- The token must be set in the environment as `NEXT_ADMIN_API_TOKEN`. Never paste
  it into chat, CLI arguments, or files.
- **Input CSV** with a subscription ID column. Optional per-row columns
  (`Pause Until`, `Next Renewal Date`) override the shared payload. XLSX must be
  converted to CSV first.

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-bulk-subscription
```

## How to Use

Ask your AI tool something like:

> Run /next-bulk-subscription — pause these subscriptions on `mystore` until
> 2026-08-01. Here's the CSV of subscription IDs.

The skill walks through:

1. **Setup** — store subdomain, token validation, CSV ingestion.
2. **Update mode** — pick the action (pause, cancel, set renewal date, change
   interval, update gateway, replace address, or freeform partial update) and
   confirm the exact request body before anything runs.
3. **Dry run** (default) — computes every request without sending it and reports
   per-row status.
4. **Live run** — only after confirmation, with `--execute`.
5. **Verify & report** — spot-checks a few updated subscriptions with a live GET,
   then summarizes results.

The executor lives at [`scripts/bulk_subscription.py`](scripts/bulk_subscription.py):

```bash
python3 -u next-bulk-subscription/scripts/bulk_subscription.py \
  --store mystore --input subscriptions.csv --action pause \
  --payload '{"pause_until":"2026-08-01"}' --results results.csv   # dry run
# add --resume results.csv --execute for the live run
```

## Safety

- **Dry-run is the default.** Mutations happen only with `--execute` after you confirm.
- Actions and payload fields are allowlisted in the executor; anything else is refused.
- Dates must be supplied as final, explicit values — the executor never computes
  offsets, fetches baselines, or guesses timezones.
- Pausing uses the official pause endpoint, never a raw `status` patch. Note:
  indefinite pauses auto-cancel after 6 months if not resumed.
- Resume matches rows by a one-way payload fingerprint, so a changed payload
  never silently skips rows.
- Rate-limited to stay under the Admin API's 4 requests/sec.
- Results output contains operational fields only — no addresses, payment data,
  names, or emails.
