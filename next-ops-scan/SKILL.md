---
name: next-ops-scan
version: 0.1.0
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
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
  - TodoWrite
---

# /next-ops-scan: Daily Operations Risk Scan

## Using This Skill

This skill works with any AI coding tool that can load a markdown file as context.

| Tool | How to Use |
|------|-----------|
| **Claude Code** | Install to `~/.claude/skills/next-ops-scan/` (see repo README). Invoke with `/next-ops-scan`. |
| **OpenAI Codex** | Pass as a system prompt: `codex --system-prompt next-ops-scan/SKILL.md` |
| **Cursor** | Add to `.cursor/rules/` or reference in your project's AI context files. |
| **GitHub Copilot** | Add to `.github/copilot-instructions.md` or include via `@workspace` reference. |
| **Other agents** | Load `SKILL.md` as context/system prompt. |

---

Runs a read-only scan of one store's daily operational risk queues:

1. **Incomplete orders** likely needing refund review.
2. **Rejected orders** needing Shop Sync / order-data correction review.
3. **Delivery Tracking failures or staleness** when Delivery Tracking is installed.

The skill never refunds, cancels, fulfills, moves, edits, messages customers, or
changes store state. It produces local files only:

- `next_ops_scan_summary.md` - human-first action summary.
- `next_ops_scan_results.csv` - stable machine-readable sidecar.

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
   - fulfillment read access for delivery statuses, usually exposed as
     `fulfillments:read` or `fulfillment_orders:read`
5. Copy the token once. Do not paste it into docs, tickets, screenshots, commits,
   or shared notes.

Recommended local storage:

```bash
export NEXT_STORE_DOMAIN="mystore.29next.store"
export NEXT_ADMIN_API_TOKEN="paste-token-here"
```

For repeated local runs, store those lines in a gitignored file such as
`.next-ops-scan.env`, run `chmod 600 .next-ops-scan.env`, then source it before
the scan:

```bash
source .next-ops-scan.env
```

If a token is exposed, rotate it in **Settings > API Access** before reusing the
workflow.

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
  --delayed-days 3
```

## Phase 3: Explain Results

Read `next_ops_scan_summary.md` first and summarize the highest-priority rows.
Use the CSV only when the user wants the full queue.

Output groups:

- **Refund review** - Incomplete orders. Most are true cancellations; review
  Order Details, check Payment Summary, and use the Refund button if money is
  owed back.
- **Rejected order review** - Shopify / Shop Sync refused fulfillment. Correct
  bad customer data, stock settings, or other rejection causes, then request
  fulfillment again if appropriate.
- **Delivery risk review** - failed, delayed, old `tracking_added`, or old
  `in_transit` fulfillments. Contact the customer, carrier, or 3PL; reship,
  refund, or document the next step.

## Caveats

- Delivery risk rows only appear when the Delivery Tracking app is installed and
  the token can read fulfillment delivery statuses. NEXT Payments customers
  often have Delivery Tracking by default.
- The scan is a queue, not a risk score. The merchant decides whether to refund,
  reship, correct data, or escalate.
- If an endpoint rejects the token, ask the user to confirm the store domain and
  scopes. Do not ask them to paste tokens into persistent project files.
