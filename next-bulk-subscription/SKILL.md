---
name: next-bulk-subscription
version: 1.0.0
description: |
  Bulk-update Next Commerce subscriptions from a flat file (CSV/XLSX) using the
  Admin API subscriptionsPartialUpdate endpoint. Handles status changes, renewal
  date shifts, interval changes, address/payment updates, and any other field
  accepted by the partial-update endpoint.

  Walks through store identification, API key validation, CSV ingestion, update
  mode selection, dry-run validation, live execution, and results reporting.

  Use when: "bulk pause subscriptions", "bulk cancel subscriptions", "shift renewal
  dates", "move renewals out by N days", "bulk update subscription interval", "bulk
  subscription edit", or when a merchant provides a list of subscription IDs that
  need the same partial update applied.
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

# /next-bulk-subscription: Bulk Subscription Partial Update (v1)

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-bulk-subscription/` (see repo README). Invoke with `/next-bulk-subscription`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-bulk-subscription/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. The instructions are tool-agnostic markdown. |

---

Applies the same partial update (PATCH) to a list of subscription IDs. The partial
update endpoint accepts any subset of subscription fields and is the correct tool
for almost every "bulk change subscriptions" request a merchant brings.

**Common bulk operations:**

- **Stop recurring billing temporarily** — shift `next_renewal_date` out by N days (preferred over `status: paused` until the platform team ships official pause support — see Gotchas).
- **Change renewal cadence** — update `interval` + `interval_count` (e.g., monthly → every 60 days).
- **Cancel a cohort** — set `status: canceled` with a reason.
- **Correct a gateway migration** — update `payment_details.gateway` for subs on a decommissioned gateway.
- **Fix bad shipping addresses** — bulk-patch `shipping_address` / `billing_address`.

---

## Phase 1: Gather Requirements

Collect the required inputs in order. Do NOT proceed until the current input is confirmed.

### Step 1: Store Subdomain

Ask the user:

> Which store needs the bulk subscription update? Provide the subdomain (e.g., `mystore` for `mystore.29next.store`).

Validate the store responds:
```bash
curl -s -o /dev/null -w "%{http_code}" https://{subdomain}.29next.store/
```

Store base URL: `https://{subdomain}.29next.store/api/admin`.

### Step 2: API Key

Ask the user:

> Provide an API key for {subdomain}.29next.store.
>
> Required scope:
> - `subscriptions:write` (implies `subscriptions:read`)
>
> Create one at **{subdomain}.29next.store Dashboard > Settings > API Access**.

Validate with a single GET against a known-good list endpoint:
```bash
curl -s -w "\n%{http_code}" \
  -H "Authorization: Bearer {api_key}" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://{subdomain}.29next.store/api/admin/subscriptions/?limit=1"
```
- `200` = key works
- `401` / `403` = bad key or missing scope

### Step 3: Input File

Ask the user:

> Provide the path to the CSV/XLSX with subscription IDs.
>
> Required column:
> - **Subscription ID** (accepted header names: `Subscription ID`, `subscription_id`, `id`, `Sub ID`)
>
> Optional columns that may be used by some update modes:
> - `Status` — current status (useful for filtering which rows apply)
> - `Next Renewal Date` — current renewal timestamp (used by the CSV-baseline mode of `shift_renewal_date`)

Detect the subscription ID column case-insensitively. If the column can't be auto-detected, show the headers and ask the user to specify.

Report:
> Loaded **{N}** subscriptions from `{filename}`.

### Step 4: Update Mode

Ask the user what to apply. Offer these common modes; any combination is valid (they can be sent in a single PATCH body):

| Mode | Body field(s) | Notes |
|------|--------------|-------|
| **Shift renewal date** | `next_renewal_date` | Pick baseline: CSV value, live-API value, or today. Add days offset. |
| **Set status** | `status` | Valid values: `active`, `past_due`, `canceled`, `retrying`, `paused`. See Gotchas for `paused`. |
| **Change interval** | `interval`, `interval_count` | e.g., `{"interval": "month", "interval_count": 2}` |
| **Update gateway** | `payment_details.gateway` (object `{id}`) | Used for bankcard migration off a retired gateway. |
| **Replace address** | `shipping_address` / `billing_address` | Full address object — see API docs. |
| **Other** | Any field accepted by `subscriptionsPartialUpdate` | Freeform JSON. |

**For the `shift_renewal_date` mode**, ask the baseline source:

> Which baseline should "+N days" be calculated from?
> - **(A) Live API value** — GET each sub first, add N, PATCH. Safest (canonical) but 2× the API calls.
> - **(B) CSV value** — Parse the CSV's `Next Renewal Date` column, add N, PATCH. ~1 call per sub. Stale if CSV is old.
> - **(C) Today + N** — Uniform renewal date = `today + N` for every sub. Loses per-sub timing.

Confirm the final PATCH body template with the user before writing the script. Example:

> I'll PATCH each subscription with:
> ```json
> {"next_renewal_date": "{csv_value + 45d, ISO 8601 with tz offset}"}
> ```
> Skipping {M} subscription IDs per your exclusion list. Ready?

---

## Phase 2: Write the Bulk Script

Generate a Python script at `{working_dir}/{subdomain}_bulk_subscription.py`.

### The API Pattern

```
PATCH /api/admin/subscriptions/{id}/
Content-Type: application/json
{
  "{field}": "{value}",
  ...
}
```

Returns HTTP **200** with the full updated subscription object. The response echoes the canonical stored value — compare to what was sent to verify.

### Script Requirements

- **Rate limiting**: 4 requests/sec max. Sleep **0.26s** between subs (1 PATCH per sub). For baseline mode that GETs first, sleep 0.5s total across both calls.
- **Auth headers**: `Authorization: Bearer {api_key}` + `X-29next-API-Version: 2024-04-01`
- **Dry-run mode**: `--dry-run` flag that computes the body but does not send the PATCH. Still reports one row per subscription.
- **Limit flag**: `--limit N` to process only the first N rows (for testing).
- **Skip list**: Hard-code or pass a set of `SKIP_IDS` (e.g., a test record already updated, or records known to be in a non-normal state).
- **Output**: Write results to `{subdomain}_bulk_subscription_results.csv` with columns:
  `subscription_id, status, detail`
- **Status values**: `OK`, `DRY_RUN`, `SKIPPED`, `PARSE_ERROR`, `HTTP_<code>`, `EXC`
- **Use `python3 -u`** for unbuffered output (live progress).
- **Only stdlib + requests** (no pandas, no openpyxl — for XLSX support, convert upstream).

### Timezone Handling for Date Fields

The API returns ISO 8601 timestamps with the store's timezone offset (e.g., `2026-07-03T10:09:01.263144-04:00`). When writing a new `next_renewal_date`:

- **If using live-API baseline**: parse the returned timestamp, add offset, format with `datetime.isoformat()`. Preserves tz.
- **If using CSV baseline**: CSV typically loses tz (format: `2026-07-03 10:09 AM`). Assume the store's offset (observe it from a single GET at the start of the run) and apply it consistently. Do NOT assume UTC — the platform evaluates renewals in store-local time.

### Gotchas (encode these in the script)

- **`status: paused` via API is currently broken** on the platform side. Even though the API returns 200 and the subscription shows `status: paused`, the underlying record can be corrupted. Until the platform team ships official pause support, achieve "stop recurring billing" by **shifting `next_renewal_date` out by N days** (45 days is the established default for a typical pause). Leave `status` as-is.
- **Empty / far-future `next_renewal_date`**: Some subscriptions have dates in the year 3966 (effectively already paused). The CSV may render this as an empty cell. Treat blank `Next Renewal Date` as "already paused — skip."
- **Full entity response**: PATCH returns the full subscription object (~3–8 KB per call). If processing thousands of subs, write responses to disk rather than holding in memory.
- **`HTTP 200 not 201`** — PATCH returns 200 on success. Don't treat 200 as an error.
- **Rate limit = 4 req/s**. At 0.26s sleep you're at ~3.8 req/s — safely under. For GET+PATCH baseline mode, use 0.5s.

### Execution Flow

**2a. Dry-run:**
```bash
python3 -u {subdomain}_bulk_subscription.py --dry-run
```

Report counts by status (OK-candidate / SKIPPED / PARSE_ERROR) and show any parse errors so the user can fix source data before a live run.

**2b. Confirm and run live:**

Ask the user:
> Dry run: {OK_candidates} subs ready to PATCH, {skipped} skipped, {errors} errors. Proceed with live PATCH?
> - A) Yes
> - B) No, let me review the dry-run output first
> - C) Limit to first N for a smaller test

Run live:
```bash
python3 -u {subdomain}_bulk_subscription.py
```

---

## Phase 3: Verify & Report

### Spot-Check Verification

After the live run, pick 1-3 random subscription IDs that reported `OK` and re-GET them to confirm the field took effect. The PATCH response can be trusted in principle, but a live GET catches any downstream state issues.

```bash
curl -s -H "Authorization: Bearer {api_key}" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://{subdomain}.29next.store/api/admin/subscriptions/{id}/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['status'], d['next_renewal_date'])"
```

### Report Results

> **Bulk subscription update complete:**
> - **OK**: {N} patched successfully
> - **SKIPPED**: {N} (excluded / already in target state)
> - **PARSE_ERROR**: {N} (bad source data — listed below)
> - **HTTP errors**: {N} (non-2xx responses)
> - **Exceptions**: {N} (network / timeout)
>
> Results CSV: `{results_path}`
> Duration: {elapsed} seconds

If any errors, list the affected subscription IDs with the error detail so the user can remediate manually.

### Clean Up

Offer to keep or delete the generated script:
> Script at `{script_path}`. Keep for re-runs, or delete?

---

## Reference: API Endpoints

| Operation | Method | Endpoint | Scope |
|-----------|--------|----------|-------|
| List subscriptions | GET | `/api/admin/subscriptions/` | `subscriptions:read` |
| Get subscription | GET | `/api/admin/subscriptions/{id}/` | `subscriptions:read` |
| Partial update | PATCH | `/api/admin/subscriptions/{id}/` | `subscriptions:write` |
| Trigger renewal | POST | `/api/admin/subscriptions/{id}/renew/` | `subscriptions:write` |
| Trigger retry | POST | `/api/admin/subscriptions/{id}/retry/` | `subscriptions:write` |

**API version**: `2024-04-01`

**Docs**: [Subscription Management Guide](https://developers.nextcommerce.com/docs/admin-api/guides/subscription-management) · [subscriptionsPartialUpdate reference](https://developers.nextcommerce.com/docs/admin-api/reference/subscriptions/subscriptionsPartialUpdate)

---

## Valid `status` Values

`active`, `past_due`, `canceled`, `retrying`, `paused`

**`paused` caveat**: see Gotchas — avoid until platform support lands. Use renewal-date shift instead.

## Example PATCH Bodies

```json
// Shift next renewal by 45 days (typical "soft pause")
{"next_renewal_date": "2026-08-17T10:09:01-04:00"}

// Change to every-2-months
{"interval": "month", "interval_count": 2}

// Cancel with reason
{"status": "canceled", "cancel_reason": "customer_request"}

// Move to a new gateway
{"payment_details": {"gateway": {"id": 42}}}
```
