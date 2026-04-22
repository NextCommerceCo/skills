---
name: next-bulk-move
version: 1.1.0
description: |
  Move fulfillment orders between locations in bulk. Accepts either a flat file
  (XLSX/CSV) of order numbers, or a list of Product IDs / SKUs to target every
  FO containing those items. Moves all matching fulfillment order line items
  from one location to another.

  Handles the full workflow: location discovery, status checks, cancellation
  requests for processing FOs, move execution, and results reporting.

  Use when: "bulk move orders", "move fulfillment orders", "transfer orders
  to new warehouse", "change fulfillment location", "move all FOs for SKU X",
  or when a flat file of order numbers or a product/SKU list needs location
  reassignment.
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

# /next-bulk-move: Bulk Fulfillment Order Location Move

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-bulk-move/` (see repo README). Invoke with `/next-bulk-move`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-bulk-move/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. The instructions are tool-agnostic markdown. |

---

Moves fulfillment orders between warehouse locations in bulk using a flat file of order numbers.

**When this happens:** A merchant is switching fulfillment providers (e.g., EcommOps → DLX) and has a batch of open/processing orders that need to be reassigned to the new location before the new provider can begin fulfilling them.

---

## The Three-Layer Model

- **Order** — the customer purchase (`/orders/{number}/`)
- **Fulfillment Order (FO)** — items grouped by fulfillment location within an order. One order can have multiple FOs. Statuses: `open`, `processing`, `closed`, `canceled`, etc.
- **Fulfillment** — the shipment record attached to a FO (tracking info). Not relevant to moves.

---

## Phase 1: Setup

### Step 1: Store Configuration

Ask the user (if not already provided):

> "What is the store subdomain? (e.g., `bareearth` for bareearth.29next.store)"

Set:
```
STORE=https://{subdomain}.29next.store
```

### Step 2: API Key

Ask the user for their API access token:

> "Provide an Admin API access token for `{STORE}`. Required scopes: `fulfillment_orders:read`, `fulfillment_orders:write`, `locations:read`."

**Auth headers for all requests:**
```
Authorization: Bearer {api_key}
X-29next-API-Version: 2024-04-01
Content-Type: application/json
```

### Step 3: Discover Locations

**Primary method** — list all locations:
```
GET {STORE}/api/admin/locations/
```

**Fallback** — if `/locations/` returns empty (scope or config issue), discover locations from existing fulfillment orders:
```
GET {STORE}/api/admin/fulfillment-orders/?status=open
```
Extract unique `assigned_location.id` and `assigned_location.name` values from the results. Paginate if needed.

Display the results as a table:

```
ID    | Name
------|------------------
1     | Store Location
2     | EcommOps China
35    | DLX
```

Ask the user to confirm source and destination:

> "Which location are you moving FROM (source)? And which location are you moving TO (destination)?"

Store as `SOURCE_LOCATION_ID` and `DEST_LOCATION_ID`.

### Step 4: Ingest Targets

Two supported input modes. Pick based on what the user provides.

**Mode A — Order number file (XLSX/CSV).** Minimum required column: **Order Number**.

```python
import pandas as pd

# XLSX
df = pd.read_excel('input.xlsx')
# CSV
# df = pd.read_csv('input.csv')

order_numbers = df['Order Number'].dropna().astype(int).tolist()
```

Report:
> "Found **{N}** order numbers in the file."

**Mode B — Product ID / SKU list.** Use when the user wants to move every FO containing specific items (e.g., discontinued SKU, supplier change for one product) without pre-computing an order list.

Query the filter directly — the API now supports `product_id` and `sku` on the fulfillment-orders list (shipped via [oscar-prime#2241](https://github.com/NextCommerceCo/oscar-prime/issues/2241)):

```
GET {STORE}/api/admin/fulfillment-orders/?sku=SKU-A,SKU-B&location_id={SOURCE_LOCATION_ID}
GET {STORE}/api/admin/fulfillment-orders/?product_id=123,456&location_id={SOURCE_LOCATION_ID}
```

Filter server-side by `location_id={SOURCE_LOCATION_ID}` to avoid pulling FOs already at the destination or at unrelated locations. Paginate until exhausted. The resulting FO set becomes the targets — skip the per-order-number lookup in Phase 2 Step 1 and classify these FOs directly.

Report:
> "Found **{N}** fulfillment orders at source matching {sku/product filter}."

---

## Phase 2: Dry Run

Before executing any moves, validate every order. For each order number:

### Step 1: Lookup Fulfillment Orders

```
GET {STORE}/api/admin/fulfillment-orders/?order_number={number}
```

**Do NOT filter by status** — we need to see all FOs for the order to make correct decisions.

### Step 2: Classify Each Order

From the results, classify:

| Condition | Classification | Action |
|-----------|---------------|--------|
| No FOs found | `NOT_FOUND` | Skip — order doesn't exist or has no FOs |
| FO at source location, `status: open` | `READY` | Can move directly |
| FO at source location, `status: processing` | `NEEDS_CANCEL` | Must send cancellation request, then move |
| FO already at destination location | `ALREADY_MOVED` | Skip — already at target |
| FO at source, `status: closed` | `ALREADY_FULFILLED` | Skip — already shipped |
| FO at source, `status: canceled` | `CANCELED` | Skip — already canceled |
| Multiple FOs at source location | `MULTIPLE` | Flag for manual review — unless a SKU/product filter was provided (Mode B), in which case the server-side filter already narrowed to the intended FO |
| FO exists but at neither source nor dest | `WRONG_LOCATION` | Skip — not at expected source |

**Matching logic:** Compare `assigned_location.id` against `SOURCE_LOCATION_ID` and `DEST_LOCATION_ID`.

### Step 3: Report Dry Run Results

```
Dry Run Summary
━━━━━━━━━━━━━━━
Total orders:        {N}
READY (open):        {N} — will move directly
NEEDS_CANCEL:        {N} — will cancel then move
ALREADY_MOVED:       {N} — already at destination
ALREADY_FULFILLED:   {N} — already shipped
NOT_FOUND:           {N} — no fulfillment orders
CANCELED:            {N} — already canceled
MULTIPLE:            {N} — flagged for manual review
WRONG_LOCATION:      {N} — not at source location
```

Ask:
> "Proceed with moving **{READY + NEEDS_CANCEL}** orders from **{source_name}** to **{dest_name}**? (yes/no)"

---

## Phase 3: Execute Moves

Process orders in sequence. **Rate limit: 0.5s sleep between orders** (~2 req/sec effective, well under the 4 req/sec limit).

### For READY Orders (status: open)

Single step — move directly:

```
POST {STORE}/api/admin/fulfillment-orders/{fo_id}/move/
{
  "new_location_id": {DEST_LOCATION_ID}
}
```

Success: HTTP 200 with `moved_fulfillment_order` (new FO at destination) and `original_fulfillment_order` (old FO, now closed).

### For NEEDS_CANCEL Orders (status: processing)

Two steps — cancel then move the **same FO ID**:

**Step A — Send cancellation request:**
```
POST {STORE}/api/admin/fulfillment-orders/{fo_id}/cancellation-request/
{}
```

The FO transitions from `processing` → `canceled` with `request_status: cancel_accepted`. Critically, `move` appears in the FO's `supported_actions` after cancellation.

**Step B — Move the same FO immediately:**
```
POST {STORE}/api/admin/fulfillment-orders/{fo_id}/move/
{
  "new_location_id": {DEST_LOCATION_ID}
}
```

No need to re-fetch — move the same FO ID directly after the cancellation request succeeds. The move creates a new FO at the destination and closes the original.

**If the cancellation request fails** (HTTP error), flag the order as `CANCEL_FAILED` for manual review.

### Progress Logging

Print live progress (flush stdout):
```
[1/94] Order 292738 — CANCEL+MOVED (FO 115969 → new FO 125832 at DLX)
[2/94] Order 292786 — CANCEL+MOVED (FO 115992 → new FO 125833 at DLX)
[3/94] Order 293621 — SKIPPED (already at destination)
...
```

---

## Phase 4: Results Report

After all orders are processed:

```
Bulk Move Complete
━━━━━━━━━━━━━━━━━
Total processed:     {N}
Moved (open):        {N}
Cancel + Moved:      {N}
Cancel failed:       {N} — need manual follow-up
Already at dest:     {N}
Already fulfilled:   {N}
Not found:           {N}
Errors:              {N}
```

If there are failures, list them:
```
Orders needing manual follow-up:
- Order 293621 (FO 12345) — cancellation request failed: HTTP 400 ...
- ...
```

### CSV Export

Export a CSV log of all actions taken for audit/reference. Save to the same directory as the input file with a timestamped name:

```
{store}-bulk-move-{YYYY-MM-DD}.csv
```

Columns:

| Column | Description |
|--------|-------------|
| `order_number` | The order number from the input file |
| `original_fo_id` | The FO ID at the source location before the move |
| `new_fo_id` | The new FO ID created at the destination (blank if not moved) |
| `source_location` | Source location name |
| `dest_location` | Destination location name |
| `action` | What was done: `cancel+moved`, `moved`, `already_moved`, `already_fulfilled`, `not_found`, `multiple_fos`, `error` |
| `status` | `success`, `skipped`, or `error` |
| `detail` | Error message or additional context (blank on success) |

```python
import csv
with open(f'{store}-bulk-move-{date}.csv', 'w', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=[
        'order_number', 'original_fo_id', 'new_fo_id',
        'source_location', 'dest_location', 'action', 'status', 'detail'
    ])
    writer.writeheader()
    writer.writerows(rows)
```

---

## API Reference

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/admin/locations/` | GET | List all fulfillment locations |
| `/api/admin/fulfillment-orders/` | GET | List FOs (filter: `order_number`, `status`, `location_id`, `product_id`, `sku` — comma-separated for multiple values) |
| `/api/admin/fulfillment-orders/{id}/move/` | POST | Move FO to new location |
| `/api/admin/fulfillment-orders/{id}/cancellation-request/` | POST | Request cancellation of processing FO |
| `/api/admin/fulfillment-orders/{id}/available-locations/` | GET | Check which locations have inventory |

**Auth:** `Authorization: Bearer {api_key}` + `X-29next-API-Version: 2024-04-01`

**Rate limit:** 4 req/sec. Use 0.5s sleep between orders.

**Required scopes:** `fulfillment_orders:read`, `fulfillment_orders:write`, `locations:read`

---

## Gotchas

- **Cancellation → canceled, not open:** The cancellation request transitions a processing FO to `canceled` status (not back to `open`). But `move` becomes a supported action on the canceled FO — move it directly using the same FO ID. Do NOT re-fetch looking for an open FO.
- **Move the same FO ID after cancel:** After cancellation, call `/move/` on the same FO ID immediately. The move creates a NEW FO at the destination (with a new ID) and sets the original to `closed`.
- **`/locations/` may return empty:** The locations list endpoint requires `locations:read` scope and may return empty if locations are managed by fulfillment services. Fallback: discover locations from FO data by querying `/fulfillment-orders/` and extracting unique `assigned_location` values.
- **HTTP 200 on move, not 201:** The move endpoint returns 200 despite being a POST. Don't treat 200 as an error.
- **Multiple FOs per order:** An order with items from different warehouses has multiple FOs. Don't guess which one to move — flag for manual review. If the user supplied a SKU/product filter (Mode B), the server-side filter already disambiguates; no manual review needed.
- **Prefer server-side filtering:** Combine `location_id`, `status`, `sku`, and/or `product_id` on the list endpoint to narrow the FO set before iterating. Cheaper and faster than per-order-number lookups when the target criteria is product-based.
- **`new_location_id` is required:** The move endpoint requires the destination location ID (integer). `fulfillment_order_line_items` is optional — if omitted, ALL line items in the FO move (correct default for bulk operations).
- **Response contains both old and new FO:** `moved_fulfillment_order` (new FO at destination, `status: open`) and `original_fulfillment_order` (old FO at source, `status: closed`).
- **Don't filter by status on initial lookup:** Query all FOs for an order number first. Filtering by status could miss FOs that need cancellation before moving.
- **Python stdout buffering:** Use `print(..., flush=True)` or `python3 -u` for live progress when running scripts redirected to a file.
