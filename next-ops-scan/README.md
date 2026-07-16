# Daily Ops Risk Scan

A **read-only** daily operations scan for a single Next Commerce store. It finds:

- **Incomplete orders** that likely need refund review.
- **Rejected orders** that need fulfillment or order-data correction review.
- **Delivery Tracking failures or stale shipments** (when the Delivery Tracking
  app is installed).

Output is two local files: `next_ops_scan_summary.md` (human-first action
summary) and `next_ops_scan_results.csv` (machine-readable sidecar). The scan
never refunds, cancels, fulfills, edits, or messages customers — it produces a
queue with manual next steps, and the merchant decides what to do.

## Requirements

- **Python 3** — the bundled scanner uses only the standard library.
- **A Next Commerce store** and its subdomain.
- **Admin API token** with the `orders:read` scope, created at
  **Dashboard > Settings > API Access**. Use a limited-scope token; keep it
  private and rotate it if exposed.
- Environment variables (or equivalent CLI flags):

```bash
export NEXT_STORE_DOMAIN="mystore.29next.store"
export NEXT_ADMIN_API_TOKEN="<token>"
```

Delivery-risk rows only appear when the Delivery Tracking app is installed and
the token can read order delivery statuses.

## Install

See the [repo README](../README.md) for the guided installer, or install just this skill:

```bash
npx skills add NextCommerceCo/skills -g --skill next-ops-scan
```

## How to Use

Ask your AI tool something like:

> Run /next-ops-scan for my store and help me review today's risky order queues.

Or run the scanner directly — it lives at
[`scripts/next_ops_scan.py`](scripts/next_ops_scan.py):

```bash
python3 next-ops-scan/scripts/next_ops_scan.py --out-dir ./next-ops-scan-output
```

Thresholds are tunable with flags such as `--lookback-days`,
`--rejected-idle-days`, `--in-transit-days`, and `--delayed-days` — see the
SKILL.md for the full list.

## Safety

- **Read-only.** No store state is ever changed; only local files are written.
- The scan is a queue, not a risk score — refund/reship/escalate decisions stay
  with the merchant.
- Tokens are read from the environment; never commit them or paste them into
  shared docs.
