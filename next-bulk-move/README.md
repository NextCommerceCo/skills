# Bulk Fulfillment Order Move

Moves fulfillment orders (FOs) between warehouse locations in bulk. Typical use:
a merchant is switching fulfillment providers and a batch of open/processing
orders needs reassigning to the new location before the new provider can start.

Two input modes:

- **Order-number file** — a CSV of order numbers to move.
- **Product ID / SKU list** — move every FO at the source location containing
  the given items, without pre-computing an order list.

Processing FOs are handled with the full cancellation flow: request cancellation,
poll until the location accepts it, then move the same FO.

## Requirements

- **Python 3** — the bundled executor uses only the standard library.
- **A Next Commerce store** and its subdomain.
- **Admin API token** with `fulfillment_orders:read`, `fulfillment_orders:write`,
  and `locations:read` scopes, created at **Dashboard > Settings > API Access**.
- The token must be set in the environment as `NEXT_ADMIN_API_TOKEN`. Never paste
  it into chat, CLI arguments, or files.
- XLSX inputs must be exported to CSV first (the executor is stdlib-only).

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-bulk-move
```

## How to Use

Ask your AI tool something like:

> Run /next-bulk-move — move these orders from Provider A to Provider B on
> `mystore`. Here's the CSV of order numbers.

The skill walks through:

1. **Setup** — store subdomain, token validation, location discovery (pick source
   and destination location IDs), target ingestion.
2. **Dry run** (default) — classifies every order: `READY`, `NEEDS_CANCEL`,
   `ALREADY_MOVED`, `ALREADY_FULFILLED`, `NOT_FOUND`, `MULTIPLE` (manual review),
   `WRONG_LOCATION`.
3. **Execute** — only after confirmation, with `--execute`. Open FOs move
   directly; processing FOs go through cancel → poll → move.
4. **Results** — an append-only results CSV usable for audit and `--resume`.

The executor lives at [`scripts/bulk_move.py`](scripts/bulk_move.py):

```bash
python3 next-bulk-move/scripts/bulk_move.py \
  --store mystore --input orders.csv --source 10 --destination 20 \
  --results bulk-move-results.csv            # dry run
# add --resume bulk-move-results.csv --execute for the live run
```

## Safety

- **Dry-run is the default.** Mutations happen only with `--execute` after you confirm.
- Destination availability is verified per-FO before any cancel or move.
- Every mutation re-fetches the FO first; stale or mismatched API responses are
  recorded as retryable errors, never acted on.
- Rate-limited to stay under the Admin API's 4 requests/sec.
- The executor only talks to `*.29next.store` hosts unless you explicitly pass
  `--allow-host` for a custom admin domain.
- Results output contains operational fields only — no customer data or API
  response bodies.
