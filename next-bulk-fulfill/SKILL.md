---
name: next-bulk-fulfill
version: 1.4.0
description: |
  Bulk fulfillment tracking sync — update orders to Fulfilled status with tracking
  numbers from a CSV when the fulfillment provider's automation fails to sync back.

  Walks through store identification, API key validation, CSV ingestion, carrier
  detection, dry-run validation, live execution with customer notifications, and
  results reporting.

  Use when: "bulk fulfill", "sync tracking numbers", "tracking sync", "fulfill orders
  from CSV", "orders stuck in processing", "tracking numbers didn't sync", or when a
  merchant reports shipped orders still showing as Processing.
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - TodoWrite
---

# /next-bulk-fulfill: Bulk Fulfillment Tracking Sync (v1)

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-bulk-fulfill` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Updates orders stuck in Processing (or Open) status with tracking numbers from a CSV,
triggering fulfillment completion and customer shipping notifications.

**When this happens:** A fulfillment provider ships orders but fails to POST tracking
numbers back to Next Commerce. Orders get stuck in "Processing" status. The merchant
provides a CSV of order numbers + tracking numbers, and this skill handles the bulk update.

---

## Phase 1: Gather Requirements

Collect the three required inputs in order. Do NOT proceed to the next input until the
current one is confirmed.

## Admin API Conventions

Before making any store request, use the public Admin API conventions from
https://developers.nextcommerce.com/docs/admin-api:

- Base URL: `https://{subdomain}.29next.store/api/admin/`
- Auth header: `Authorization: Bearer $NEXT_ADMIN_API_TOKEN`
- Version header: `X-29next-API-Version: 2024-04-01`

Do not use `/api/v1/...` paths or `Authorization: Token ...`; those are not the
documented NEXT Admin API convention and commonly return storefront HTML 404
pages instead of JSON.

### Step 1: Store Subdomain

Ask the user:

> Which store needs fulfillment sync? Provide the subdomain (e.g., `mystore` for mystore.29next.store).

Validate the store exists:
```bash
curl -s -o /dev/null -w "%{http_code}" https://{subdomain}.29next.store/
```

If the store returns a non-200 status, warn the user and ask them to confirm the subdomain.

Store the base URL: `https://{subdomain}.29next.store/api/admin/`

### Step 2: Admin API Access Token

The executor reads the token only from `NEXT_ADMIN_API_TOKEN`. Scopes:
`fulfillment_orders:read` and `fulfillment_orders:write` (separate
permissions). The token never enters conversation, CLI arguments, echoes,
scripts, or results files.

If the store's variable (naming below) or `NEXT_ADMIN_API_TOKEN` is already in
the environment, use it. Otherwise tokens live in `.env` in the current
working directory (the user's project, not the skill checkout) — one line per
store, named `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` (caps, hyphens → underscores;
`herz` → `HERZ_NEXT_ADMIN_API_TOKEN`). The user pastes the token in with a
text editor, so it never touches the chat.

1. In a git repository, `.env` must pass `git check-ignore .env` first — add
   it to that repo's `.gitignore` if needed. Don't create the file until it
   does.
2. Create the file (or append the store's line), then lock it so only this
   user can read it (`chmod 600 .env`):

   ```
   # Next Commerce Admin API tokens — one line per store.
   HERZ_NEXT_ADMIN_API_TOKEN=
   ```

3. If the value is empty: have the user open `.env` in a text editor (offer to
   open it — the file is hidden in file managers), paste the token after `=`,
   save, and reply "saved".
4. Load only that line — never `source` the file (it executes content and
   exports unrelated secrets) — in the same command as the validation curl or
   executor run, since shell state may not persist between tool commands:

   ```bash
   NEXT_ADMIN_API_TOKEN="$(grep -m1 '^HERZ_NEXT_ADMIN_API_TOKEN=' .env | cut -d'=' -f2-)"
   [ -n "$NEXT_ADMIN_API_TOKEN" ] || { echo "no token for herz in .env" >&2; exit 1; }
   export NEXT_ADMIN_API_TOKEN
   ```

Validate the key works:
```bash
curl -s -w "\n%{http_code}" \
  -H "Authorization: Bearer ${NEXT_ADMIN_API_TOKEN:?NEXT_ADMIN_API_TOKEN is required}" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://{subdomain}.29next.store/api/admin/fulfillment-orders/?limit=1"
```

- `200` = key works, proceed
- `401` / `403` = bad key or missing scopes, ask user to check
- `404` with `<!DOCTYPE html>` or "Page not found" = wrong URL convention;
  re-check `/api/admin/`, `Authorization: Bearer ...`, and the store domain

### Step 3: CSV File

Ask the user:

> Provide the path to the CSV file with order numbers, tracking numbers, and
> carriers.
>
> Expected columns (header names are flexible — I'll detect them):
> - **Order number** (e.g., `ORDER NUMBER`, `order_number`, `Order #`)
> - **Tracking number** (e.g., `TRACKING NUMBER`, `tracking_no`, `Tracking #`)
> - **Carrier** (e.g., `carrier`, `Carrier`) — **strongly recommended.** Ask the
>   fulfillment provider to include it in the export; their carrier data is
>   authoritative. Without it, the only fallback is AI pattern matching, which
>   is not reliable.
>
Do not include customer names, emails, addresses, or payment data in the executor
input or results.

Read the CSV and detect columns. Look for headers matching these patterns:
- Order number: contains `order` AND (`number` or `#` or `num` or `id`)
- Tracking number: contains `tracking` AND (`number` or `#` or `num` or `code`)

If column detection fails, show the headers found and ask the user to specify which
columns to use.

Report what was loaded:
> Loaded **{N}** orders from `{filename}`. Detected columns:
> - Order number: `{column_name}`
> - Tracking number: `{column_name}`

---

## Phase 2: Carrier Detection

**Carrier source gate — run BEFORE any inference or research.** If the CSV has
an explicit `carrier` column, skip inference entirely and validate those slugs.
If it does not, stop and warn the user, then ask how to proceed:

> Your file has order and tracking numbers but no carrier column. Carrier slugs
> matter: they drive the customer-facing tracking link and Delivery Tracking.
>
> - A) **Recommended: start over with a corrected file.** Ask the fulfillment
>   provider to re-export with a `carrier` column — their data is authoritative.
>   We restart from the CSV step when you have it.
> - B) Last resort: AI-attempted matches. I infer carriers from tracking-number
>   patterns (researching unknown prefixes if needed). This is NOT reliable —
>   a wrong match sends customers a broken tracking link — and every mapping
>   requires your explicit confirmation before anything is sent.

Lead with A. Only continue to detection below if the user explicitly accepts
the reliability risk and picks B.

Detect the shipping carrier from tracking number prefixes. Use this mapping:

| Tracking Pattern | Carrier Slug | Carrier Name |
|-----------------|-------------|-------------|
| Starts with `YT` | `yunexpress` | Yunexpress |
| Starts with `4PX` | `4px` | 4PX |
| Starts with `92`, length >= 20 | `usps` | USPS |
| Starts with `1Z` | `ups` | UPS |
| 12-15 digits, no letter prefix | `fedex` | FedEx |
| Starts with `JD` or 10 digits starting with `0` | `dhl` | DHL |
| None of the above | `other` | Other |

**Valid carrier slugs** live in a hardcoded known-good list in the bundled
executor (`VALID_CARRIERS` in `scripts/bulk_fulfill.py`), taken from the
`TrackingInfo.carrier` enum in the published 2024-04-01 Admin API spec, rendered
at [fulfillmentsCreate](https://developers.nextcommerce.com/docs/admin-api/reference/fulfillment/fulfillmentsCreate).
The executor validates explicit CSV carriers and `--carrier-map` values against
this list; unknown slugs error as `INVALID_CARRIER` instead of being sent. When
the platform adds carriers, update the list and release a new skill version.

After detection, show a carrier summary:
> **Carrier detection:**
> - usps: 145 orders
> - yunexpress: 4 orders
> - other: 1 order
>
> Orders with carrier `other` will still sync, but the carrier slug does real work:
> the platform maps it to the carrier's tracking link template (the customer-facing
> tracking link), and Delivery Tracking uses it to follow carrier events for
> delivery statuses and notifications. `other` stores the tracking number and
> loses both. Use it only when the carrier genuinely isn't supported.

**Warn before syncing `other` rows — a real carrier is strongly recommended.**
If any rows resolve to carrier `other` (or a pattern is unmatched), warn the user
explicitly before the live run: those orders will have no customer-facing tracking
link and no Delivery Tracking carrier events — no delivery statuses, delivery
notifications, or delivery reporting. Ask whether the fulfillment provider can
supply the actual carrier for those shipments (an explicit `carrier` CSV column)
before proceeding. Only sync `other` rows after the user accepts these downsides.

Inference is a proposal, never authorization. Group every distinct inferred
pattern (for example `prefix:1Z` or `digits:12-15`) and show its proposed carrier.
Require confirmation for every distinct pattern, including `unmatched`, using an
explicit `carrier` input column or `--carrier-map` JSON such as
`'{"prefix:1Z":"ups","unmatched":"other"}'`. Unconfirmed rows are flagged and
never sent.

---

## Phase 3: Run the Bundled Executor

Use `next-bulk-fulfill/scripts/bulk_fulfill.py`. It is stdlib-only, dry-run by
default, reads the token only from `NEXT_ADMIN_API_TOKEN`, continues after
individual failures, rate-limits requests, and supports safe resume.

### The API Pattern

**Step 1 — Find the fulfillable order:**
```
GET /api/admin/fulfillment-orders/?order_number={number}&status=processing
```
If no results, retry with `status=open` (unfulfilled orders).

Returns paginated results. Use `results[0]["id"]` as the fulfillment order ID.

**Step 2 — Create the fulfillment with tracking:**
```
POST /api/admin/fulfillment-orders/{id}/fulfillments/
{
  "tracking_info": [{"tracking_code": "{tracking_number}", "carrier": "{carrier_slug}"}],
  "notify": true
}
```

### Executor Guarantees

- **Rate limiting**: 4 requests/sec max. At 2 calls per order, sleep 0.6s between orders.
- **Carrier validation**: explicit CSV carriers and `--carrier-map` values are
  validated against the executor's hardcoded known-good list (the spec's
  `TrackingInfo.carrier` enum); rows with unknown slugs error as
  `INVALID_CARRIER` instead of being sent.
- **Auth headers**: token comes only from `NEXT_ADMIN_API_TOKEN`
- **Dry-run mode**: default; runs lookup but skips POST
- **Notify control**: `--no-notify` flag to suppress customer notifications
- **Limit flag**: `--limit N` to process only the first N orders (for testing)
- **Output**: only `order_number, fulfillment_id, tracking_code, carrier, action, status`;
  never API bodies, customer data, addresses, or payment data
- **Use `python3 -u`** for unbuffered output (real-time progress)
- **Only Python stdlib**

### Gotchas (encode these in the script)

- The request body field is `carrier`, NOT `tracking_carrier`. The response uses
  `tracking_carrier` which is confusing — but the request must use `carrier`.
- Success returns HTTP **200**, not 201 (despite being a POST). Accept both.
- `NOT_FOUND` (0 results from GET) = order already fulfilled or canceled. Expected, not an error.
- Multiple fulfillment orders per order = items from different warehouse locations.
  Skip and flag for manual review — don't guess which FO to fulfill.

### Execution Flow

**3a. Run dry-run first:**
```bash
python3 -u next-bulk-fulfill/scripts/bulk_fulfill.py \
  --store {subdomain} --input orders.csv --results bulk-fulfill-results.csv \
  --carrier-map carrier-map.json
```

Report results to the user:
> **Dry-run results:**
> - DRY_RUN_OK: {N} (ready to fulfill)
> - NOT_FOUND: {N} (already fulfilled/canceled)
> - MULTIPLE_FOUND: {N} (needs manual review)
> - API_ERROR: {N}

If NOT_FOUND or MULTIPLE_FOUND counts are unexpectedly high, discuss with the user
before proceeding.

**3b. Confirm and run live:**

Ask the user:
> Ready to fulfill **{N}** orders with customer notifications enabled. Proceed?
>
> - A) Yes, run it (notifications ON)
> - B) Yes, but suppress notifications (--no-notify)
> - C) No, let me review first

Then run:
```bash
python3 -u next-bulk-fulfill/scripts/bulk_fulfill.py \
  --store {subdomain} --input orders.csv --results bulk-fulfill-results.csv \
  --resume bulk-fulfill-results.csv --carrier-map carrier-map.json --execute
# Add --no-notify for option B.
```

---

## Phase 4: Results & Follow-up

### Report Results

Show the final summary:
> **Fulfillment sync complete:**
> - **OK**: {N} orders fulfilled with tracking
> - **NOT_FOUND**: {N} (already fulfilled or canceled — expected)
> - **MULTIPLE_FOUND**: {N} (manual review needed)
> - **API_ERROR**: {N}
>
> Results saved to: `{results_csv_path}`

### Flag Manual Review Items

For any MULTIPLE_FOUND orders, provide the order numbers and FO IDs so the user can
resolve them in the store admin:
> **Orders needing manual review:**
> - Order {number}: FO IDs {id1}, {id2} — has multiple fulfillment
>   orders, needs manual fulfillment in the admin dashboard

For any API_ERROR orders, show the error details from the results CSV.

### Clean Up

Keep the bundled executor. Delete transient carrier-map/input/results artifacts by
default after the outcome is confirmed unless needed for resume or audit. Run
`unset NEXT_ADMIN_API_TOKEN` when finished. Keep the `.env` file — it holds
the per-store tokens for future runs.

---

## Reference: API Endpoints

| Operation | Method | Endpoint | Scopes |
|-----------|--------|----------|--------|
| List fulfillment orders | GET | `/api/admin/fulfillment-orders/` | `fulfillment_orders:read` |
| Create fulfillment | POST | `/api/admin/fulfillment-orders/{id}/fulfillments/` | `fulfillment_orders:write` |

**API version**: `2024-04-01`

**Docs**: [Fulfillment Service Guide](https://developers.nextcommerce.com/docs/apps/guides/fulfillment-service#creating-fulfillments)
