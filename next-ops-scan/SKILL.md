---
name: next-ops-scan
version: 0.2.0
description: |
  Read-only daily operations scan for a single Next Commerce store. Finds risky
  Incomplete orders, Rejected orders, and Delivery Tracking failures/staleness,
  then produces a merchant-facing summary and CSV with manual next steps.

  Use when: "daily ops scan", "risk scan", "find risky orders", "check
  incomplete orders", "check rejected orders", "stuck shipments", "delivery
  failures", or when a merchant wants a daily queue to reduce dispute risk.
allowed-tools:
  - Bash
  - Read
  - AskUserQuestion
  - TodoWrite
---

# /next-ops-scan: Daily Operations Risk Scan

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Recommended** | Clone `NextCommerceCo/skills` and run `./skills.sh`; choose your local agent target and this skill. |
| **No checkout** | Use `npx skills add NextCommerceCo/skills -g --skill next-ops-scan` and add `-a <agent>` when you want a specific agent. |
| **Fallback** | Load this `SKILL.md` as a system prompt, context file, rule, or chat upload if your tool does not support native skills. |

---

Runs a read-only scan of one store's daily operational risk queues:

1. **Incomplete orders** needing refund review.
2. **Rejected orders** needing fulfillment or order-data correction review.
3. **Delivery Tracking failures or potentially stale records** when Delivery Tracking is installed.

The skill never refunds, cancels, fulfills, moves, edits, messages customers, or
changes store state. It produces local files only:

- `next_ops_scan_summary.md` - human-first action summary.
- `next_ops_scan_results.csv` - stable machine-readable sidecar.

## Admin API Conventions

Use the public Admin API conventions from
https://developers.nextcommerce.com/docs/admin-api before making any store
request:

- Base URL: `https://{store}.29next.store/api/admin/`
- Auth header: `Authorization: Bearer <api access token>`
- Version header: `X-29next-API-Version: 2024-04-01`
- Orders list endpoint: `GET /api/admin/orders/`
- Delivery status filtering uses the Orders List `delivery_status` query
  parameter, for example `GET /api/admin/orders/?delivery_status=in_transit`.

Do not use `/api/v1/...` paths or `Authorization: Token ...`; those are not the
documented NEXT Admin API convention and commonly return storefront HTML 404
pages instead of JSON.

Optional smoke test before running the scanner:

```bash
curl -sS \
  -H "Authorization: Bearer $NEXT_ADMIN_API_TOKEN" \
  -H "X-29next-API-Version: 2024-04-01" \
  "https://$NEXT_STORE_DOMAIN/api/admin/orders/?limit=1" | head -c 500
```

The expected response is JSON with a `results` array. If you see `<!DOCTYPE
html>` or a "Page not found" page, re-check the URL path first.

## Phase 1: Gather Access

### Step 1: Store Domain

Ask:

> Which store should I scan? Provide the subdomain, e.g. `mystore` for `mystore.29next.store`.

Normalize to `NEXT_STORE_DOMAIN={subdomain}.29next.store` unless the user
already provides a full domain.

### Step 2: Admin API Token

Ask the merchant to create a limited-scope Admin API token:

1. Open the NEXT dashboard for the store.
2. Go to **Settings > API Access**.
3. Create or rotate an Admin API token for this scan.
4. Grant only these read scopes when available:
   - `orders:read`
5. Keep the token private: do not commit it, paste it into shared docs, or
   include it in screenshots. Rotate any token that is exposed.

The scanner reads the token only from `NEXT_ADMIN_API_TOKEN`. The token never
enters conversation, CLI flags, echoes, scripts, or results files.

If the store's variable (naming below) or `NEXT_ADMIN_API_TOKEN` is already in
the environment, use it. Otherwise tokens live in `.env` in the current
working directory (the user's project, not the skill checkout) — one line per
store, named `{SUBDOMAIN}_NEXT_ADMIN_API_TOKEN` (caps, hyphens → underscores;
`my-store` → `MY_STORE_NEXT_ADMIN_API_TOKEN`). The user pastes the token in
with a text editor, so it never touches the chat.

1. In a git repository, `.env` must pass `git check-ignore .env` first — add
   it to that repo's `.gitignore` if needed. Don't create the file until it
   does.
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
   exports unrelated secrets) — in the same command as the smoke test or
   scanner run, since shell state may not persist between tool commands.
   `NEXT_STORE_DOMAIN` is not a secret and can be exported directly:

   ```bash
   NEXT_ADMIN_API_TOKEN="$(grep -m1 '^MYSTORE_NEXT_ADMIN_API_TOKEN=' .env | cut -d'=' -f2-)"
   [ -n "$NEXT_ADMIN_API_TOKEN" ] || { echo "no token for mystore in .env" >&2; exit 1; }
   export NEXT_ADMIN_API_TOKEN
   export NEXT_STORE_DOMAIN="mystore.29next.store"
   ```

## Phase 2: Run the Read-Only Scanner

Use the bundled script:

```bash
python3 next-ops-scan/scripts/next_ops_scan.py --out-dir ./next-ops-scan-output
```

If the skill was installed into an agent-specific skills directory, locate the
installed `next-ops-scan/scripts/next_ops_scan.py` file and run that path.

Optional flags:

```bash
python3 next-ops-scan/scripts/next_ops_scan.py \
  --out-dir ./next-ops-scan-output \
  --lookback-days 30 \
  --rejected-idle-days 1 \
  --incomplete-idle-days 0 \
  --tracking-added-days 5 \
  --in-transit-days 7 \
  --delayed-days 3 \
  --orders-max-pages 50 \
  --delivery-max-pages 25
```

If your admin dashboard uses a custom URL, pass it explicitly:

```bash
python3 next-ops-scan/scripts/next_ops_scan.py \
  --admin-base-url "https://mystore.29next.store/dashboard"
```

## Phase 3: Explain Results

Read `next_ops_scan_summary.md` first and summarize the highest-priority rows.
Use the CSV only when the user wants the full queue.

Output groups:

- **Refund review** - Incomplete orders. Review Order Details, check Payment
  Summary, and use the Refund button if money is owed back. If the store syncs
  through Shop Sync, a canceled Shopify order is one possible cause.
- **Rejected order review** - Fulfillment was rejected. Correct bad customer
  data, stock settings, or other rejection causes, then request fulfillment
  again if appropriate. If the store uses Shop Sync, Shopify or Shop Sync may
  be the source of the rejection.
- **Delivery risk review** - failed or delayed delivery statuses, or
  `tracking_added` / `in_transit` rows whose available timestamp exceeds the
  threshold. The scanner prefers a delivery-status event timestamp when the
  API supplies one; otherwise it explicitly reports the age of the order
  record timestamp, which does not prove how long the delivery held its status.
  Contact the customer, carrier, or 3PL; reship, refund, or document the next
  step.

## Caveats

- Delivery risk rows only appear when the Delivery Tracking app is installed and
  the token can read order delivery statuses. NEXT Payments customers often have
  Delivery Tracking by default.
- The scan is a queue, not a risk score. The merchant decides whether to refund,
  reship, correct data, or escalate.
- If an endpoint rejects the token, ask the user to confirm the store domain and
  scopes. Do not ask them to paste tokens into persistent project files.
