---
name: next-bulk-subscription
version: 1.3.0
description: |
  Bulk-manage Next Commerce subscriptions from a flat file (CSV/XLSX) using the
  Admin API subscription action endpoints and subscriptionsPartialUpdate endpoint.
  Handles official pause actions, cancellation actions, renewal date shifts,
  interval changes, address/payment updates, and any other field accepted by the
  partial-update endpoint.

  Walks through store identification, API key validation, CSV ingestion, update
  mode selection, dry-run validation, live execution, and results reporting.

  Use when: "bulk pause subscriptions", "pause subscriptions until a date", "bulk
  cancel subscriptions", "shift renewal dates", "move renewals out by N days",
  "bulk update subscription interval", "bulk subscription edit", or when a
  merchant provides a list of subscription IDs that need the same subscription
  action or partial update applied.
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

# /next-bulk-subscription: Bulk Subscription Actions (v1.1)

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-bulk-subscription` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Applies the same subscription action or partial update to a list of subscription
IDs. Use the first-class action endpoint when one exists (pause, cancel, renew,
retry). Use the partial-update endpoint for field edits such as renewal dates,
cadence, addresses, and payment details.

**Common bulk operations:**

- **Pause recurring billing temporarily** — call `subscriptionsPauseCreate` with optional `pause_until` (date-only `YYYY-MM-DD`) so the platform sets the subscription lifecycle state correctly.
- **Set a renewal date while leaving status active** — provide the final `next_renewal_date` explicitly in the CSV or shared CLI payload.
- **Change renewal cadence** — update `interval` + `interval_count` (e.g., monthly → every 60 days).
- **Cancel a cohort** — call `subscriptionsCancelCreate` with a reason and notification preference.
- **Correct a gateway migration** — update `payment_details.gateway` for subs on a decommissioned gateway.
- **Fix bad shipping addresses** — bulk-patch `shipping_address` / `billing_address`.

---

## Phase 1: Gather Requirements

Collect the required inputs in order. Do NOT proceed until the current input is confirmed.

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

> Which store needs the bulk subscription update? Provide the subdomain (e.g., `mystore` for `mystore.29next.store`).

Validate the store responds:
```bash
curl -s -o /dev/null -w "%{http_code}" https://{subdomain}.29next.store/
```

Store base URL: `https://{subdomain}.29next.store/api/admin/`.

### Step 2: Admin API Access Token

The executor reads the token only from `NEXT_ADMIN_API_TOKEN`. Scopes:
`subscriptions:read` and `subscriptions:write` (separate permissions). The
token never enters conversation, CLI arguments, echoes, scripts, or results
files.

If the store's variable (naming below) or `NEXT_ADMIN_API_TOKEN` is already in
the environment, use it. Otherwise tokens live in `.env` in the current
working directory (the user's project, not the skill checkout) — one line per
store, named `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` (caps, hyphens → underscores;
`my-store` → `MY_STORE_NEXT_ADMIN_API_TOKEN`). The user pastes the token in
with a text editor, so it never touches the chat.

1. If this directory is a git repository (a `.git` folder is present), make
   sure `.gitignore` has a `.env` line — add it if missing.
2. Create the file (or append the store's line), then lock it so only this
   user can read it (`chmod 600 .env`):

   ```
   # Next Commerce Admin API tokens — one line per store.
   MYSTORE_NEXT_ADMIN_API_TOKEN=
   ```

3. If the value is empty: have the user open `.env` in a text editor (offer to
   open it — the file is hidden in file managers), paste the token after `=`,
   save, and reply "saved".
4. Load only that line — never `source` the file (it executes content and
   exports unrelated secrets) — in the same command as the validation curl or
   executor run, since shell state may not persist between tool commands:

   ```bash
   NEXT_ADMIN_API_TOKEN="$(grep -m1 '^MYSTORE_NEXT_ADMIN_API_TOKEN=' .env | cut -d'=' -f2-)"
   [ -n "$NEXT_ADMIN_API_TOKEN" ] || { echo "no token for mystore in .env" >&2; exit 1; }
   export NEXT_ADMIN_API_TOKEN
   ```

Validate with a single GET against a known-good list endpoint:
```bash
curl -s -w "\n%{http_code}" \
  -H "Authorization: Bearer ${NEXT_ADMIN_API_TOKEN:?NEXT_ADMIN_API_TOKEN is required}" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://{subdomain}.29next.store/api/admin/subscriptions/?limit=1"
```
- `200` = key works
- `401` / `403` = bad key or missing scope
- `404` with `<!DOCTYPE html>` or "Page not found" = wrong URL convention;
  re-check `/api/admin/`, `Authorization: Bearer ...`, and the store domain

### Step 3: Input File

Ask the user:

> Provide the path to the CSV/XLSX with subscription IDs.
>
> Required column:
> - **Subscription ID** (accepted header names: `Subscription ID`, `subscription_id`, `id`, `Sub ID`)
>
> Optional columns that may be used by some update modes:
> - `Status` — current status (useful for filtering which rows apply)
> - `Pause Until` — date-only `YYYY-MM-DD` value for per-row pause end dates
> - `Next Renewal Date` — final renewal timestamp to apply for that row

Detect the subscription ID column case-insensitively. If the column can't be auto-detected, show the headers and ask the user to specify.

Report:
> Loaded **{N}** subscriptions from `{filename}`.

### Step 4: Update Mode

Ask the user what to apply. Offer these common modes:

| Mode | Endpoint | Body field(s) | Notes |
|------|----------|---------------|-------|
| **Pause subscriptions** | `POST /subscriptions/{id}/pause/` | `pause_until` (optional) | Preferred for actual subscription pauses. If omitted, the subscription pauses indefinitely and is auto-cancelled if not resumed within 6 months. |
| **Set renewal date** | `PATCH /subscriptions/{id}/` | `next_renewal_date` | Supply the final timestamp explicitly per row or in `--payload`. Keeps status unchanged. |
| **Cancel subscriptions** | `POST /subscriptions/{id}/cancel/` | `cancel_reason`, `cancel_reason_other_message`, `send_cancel_notification` | Preferred for cancellations because it records cancellation semantics. |
| **Change interval** | `PATCH /subscriptions/{id}/` | `interval`, `interval_count` | e.g., `{"interval": "month", "interval_count": 2}` |
| **Update gateway** | `PATCH /subscriptions/{id}/` | `payment_details.gateway` (object `{id}`) | Used for bankcard migration off a retired gateway. |
| **Replace address** | `PATCH /subscriptions/{id}/` | `shipping_address` / `billing_address` | Full address object — see API docs. |
| **Other partial update** | `PATCH /subscriptions/{id}/` | Any field accepted by `subscriptionsPartialUpdate` | Freeform JSON. |

**For the `pause` mode**, ask:

> Should these subscriptions be paused indefinitely, paused until one shared date, or paused until a per-row `Pause Until` date from the file?
>
> If using a date, provide it as `YYYY-MM-DD`.

The bundled executor does not fetch renewal-date baselines, calculate offsets, or
infer timezones. If the requested operation is expressed as "+N days", calculate
and review the final timestamps outside this executor before running it, then
provide those explicit values through the CSV or `--payload`.

Confirm the final action template with the user before writing the script. Examples:

> I'll POST each subscription to `/pause/` with:
> ```json
> {"pause_until": "2026-08-01"}
> ```
> Skipping {M} subscription IDs per your exclusion list. Ready?

Or:

> I'll PATCH each subscription with:
> ```json
> {"next_renewal_date": "2026-08-17T10:09:01-04:00"}
> ```
> Skipping {M} subscription IDs per your exclusion list. Ready?

---

## Phase 2: Run the Bundled Executor

Use `next-bulk-subscription/scripts/bulk_subscription.py`. It is stdlib-only,
dry-run by default, reads authentication only from `NEXT_ADMIN_API_TOKEN`, resumes
only rows completed with the same action and payload fingerprint, and continues
after individual failures. Allowlisted per-row CSV fields such as `pause_until`
and `next_renewal_date` override the shared `--payload` default when nonblank.
Its explicit `ACTIONS` mapping is the authority for methods, endpoint templates,
and permitted payload fields; non-allowlisted actions and fields are refused.

### The API Patterns

Pause action:

```
POST /api/admin/subscriptions/{id}/pause/
Content-Type: application/json
{
  "pause_until": "2026-08-01"
}
```

Use `{}` for an indefinite pause.

Cancel action:

```
POST /api/admin/subscriptions/{id}/cancel/
Content-Type: application/json
{
  "cancel_reason": "customer_request",
  "send_cancel_notification": false
}
```

Partial update:

```
PATCH /api/admin/subscriptions/{id}/
Content-Type: application/json
{
  "{field}": "{value}",
  ...
}
```

All three patterns return HTTP **200** with the full updated subscription object.
The response echoes the canonical stored value — compare to what was sent to
verify.

### Script Requirements

- **Rate limiting**: 4 requests/sec max. Sleep **0.26s** between subs (1 request per sub).
- **Auth headers**: token comes only from `NEXT_ADMIN_API_TOKEN`
- **Dry-run mode**: `--dry-run` flag that computes the body but does not send the POST/PATCH. Still reports one row per subscription.
- **Limit flag**: `--limit N` to process only the first N rows (for testing).
- **Skip list**: Hard-code or pass a set of `SKIP_IDS` (e.g., a test record already updated, or records known to be in a non-normal state).
- **Output**: only `subscription_id, order_id, customer_id, action,
  payload_fingerprint, status, error_code, error_message`; the fingerprint is a
  one-way SHA-256 of the canonical request payload for safe resume matching.
  Never write request/response bodies, addresses, payment fields, customer names,
  or emails.
- **Status values**: `OK`, `DRY_RUN`, `SKIPPED`, `PARSE_ERROR`, `HTTP_<code>`, `EXC`
- **Use `python3 -u`** for unbuffered output (live progress).
- **Only Python stdlib** (for XLSX support, convert upstream).

### Explicit Date Fields

Supply `next_renewal_date` as a final, reviewed ISO 8601 timestamp with the intended
timezone offset (for example, `2026-07-03T10:09:01-04:00`). The executor forwards
the value as supplied; it does not parse dates, add offsets, fetch a store timezone,
or convert timezone-naive CSV values. For `pause_until`, send a date-only
`YYYY-MM-DD` value, not a timestamp.

### Gotchas (encode these in the script)

- **Do not pause by PATCHing `status: paused`**. Use the official `subscriptionsPauseCreate` endpoint instead. The pause endpoint sets lifecycle fields (`status`, `paused_at`, `paused_until`) and applies platform pause semantics; a raw status patch does not.
- **Indefinite pauses auto-cancel after 6 months if not resumed**. Confirm the merchant understands this before sending `{}` to `/pause/`.
- **Blank per-row `next_renewal_date`**: a blank CSV value does not request date computation; the shared `--payload` value applies when present.
- **Full entity response**: POST/PATCH may return address and payment data. Discard
  the body after determining success; never write it to results or logs.
- **`HTTP 200 not 201`** — subscription action and update endpoints return 200 on success. Don't treat 200 as an error.
- **Rate limit = 4 req/s**. At 0.26s sleep you're at ~3.8 req/s — safely under.

### Execution Flow

**2a. Dry-run:**
```bash
python3 -u next-bulk-subscription/scripts/bulk_subscription.py \
  --store {subdomain} --input subscriptions.csv --action pause \
  --payload '{"pause_until":"2026-08-01"}' --results subscription-results.csv
```

Report counts by status (OK-candidate / SKIPPED / PARSE_ERROR) and show any parse errors so the user can fix source data before a live run.

**2b. Confirm and run live:**

Ask the user:
> Dry run: {OK_candidates} subs ready for {operation}, {skipped} skipped, {errors} errors. Proceed with live run?
> - A) Yes
> - B) No, let me review the dry-run output first
> - C) Limit to first N for a smaller test

Run live:
```bash
python3 -u next-bulk-subscription/scripts/bulk_subscription.py \
  --store {subdomain} --input subscriptions.csv --action pause \
  --payload '{"pause_until":"2026-08-01"}' --results subscription-results.csv \
  --resume subscription-results.csv --execute
```

---

## Phase 3: Verify & Report

### Spot-Check Verification

After the live run, pick 1-3 random subscription IDs that reported `OK` and
re-GET them to confirm the action/field took effect. The POST/PATCH response can
be trusted in principle, but a live GET catches any downstream state issues.

```bash
curl -s -H "Authorization: Bearer ${NEXT_ADMIN_API_TOKEN:?NEXT_ADMIN_API_TOKEN is required}" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://{subdomain}.29next.store/api/admin/subscriptions/{id}/" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('status'), d.get('paused_until'), d.get('next_renewal_date'))"
```

### Report Results

> **Bulk subscription action complete:**
> - **OK**: {N} completed successfully
> - **SKIPPED**: {N} (excluded / already in target state)
> - **PARSE_ERROR**: {N} (bad source data — listed below)
> - **HTTP errors**: {N} (non-2xx responses)
> - **Exceptions**: {N} (network / timeout)
>
> Results CSV: `{results_path}`
> Duration: {elapsed} seconds

If any errors, list the affected subscription IDs with the error detail so the user can remediate manually.

### Clean Up

Keep the bundled executor. Delete transient payload/input/results artifacts by
default after confirming the outcome unless needed for resume or audit. Run
`unset NEXT_ADMIN_API_TOKEN` when finished.

---

## Reference: API Endpoints

| Operation | Method | Endpoint | Scope |
|-----------|--------|----------|-------|
| List subscriptions | GET | `/api/admin/subscriptions/` | `subscriptions:read` |
| Get subscription | GET | `/api/admin/subscriptions/{id}/` | `subscriptions:read` |
| Partial update | PATCH | `/api/admin/subscriptions/{id}/` | `subscriptions:write` |
| Pause subscription | POST | `/api/admin/subscriptions/{id}/pause/` | `subscriptions:write` |
| Resume subscription | POST | `/api/admin/subscriptions/{id}/resume/` | `subscriptions:write` |
| Cancel subscription | POST | `/api/admin/subscriptions/{id}/cancel/` | `subscriptions:write` |
| Trigger renewal | POST | `/api/admin/subscriptions/{id}/renew/` | `subscriptions:write` |
| Trigger retry | POST | `/api/admin/subscriptions/{id}/retry/` | `subscriptions:write` |

**API version**: `2024-04-01`

**Docs**: [Subscription Management Guide](https://developers.nextcommerce.com/docs/admin-api/guides/subscription-management) · [subscriptionsPauseCreate reference](https://developers.nextcommerce.com/docs/admin-api/reference/subscriptions/subscriptionsPauseCreate) · [subscriptionsPartialUpdate reference](https://developers.nextcommerce.com/docs/admin-api/reference/subscriptions/subscriptionsPartialUpdate)

---

## Valid `status` Values

`active`, `past_due`, `canceled`, `retrying`, `paused`

**Pause caveat**: `paused` is a valid subscription status, but bulk pause scripts
must call `POST /subscriptions/{id}/pause/` instead of PATCHing the status field.

## Example Request Bodies

```json
// Pause until a specific date
{"pause_until": "2026-08-01"}

// Pause indefinitely
{}

// Set an explicit next renewal timestamp
{"next_renewal_date": "2026-08-17T10:09:01-04:00"}

// Change to every-2-months
{"interval": "month", "interval_count": 2}

// Cancel with reason
{"cancel_reason": "customer_request", "send_cancel_notification": false}

// Move to a new gateway
{"payment_details": {"gateway": {"id": 42}}}
```
