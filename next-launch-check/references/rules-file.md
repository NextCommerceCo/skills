# Rules file

Pass your own QA rules with `--rules <file>`. Two formats are accepted.

## Markdown

Any Markdown checklist. Every list item becomes a checklist item for the
tester to answer, grouped under the nearest heading. Front matter and code blocks are ignored.
Use this to bring an existing QA document as it is.

## JSON

```json
{
  "name": "Acme campaign rules",
  "require": {
    "tracking": ["gtm", "ga4", "everflow"],
    "legal": ["terms", "privacy", "returns", "shipping", "contact"]
  },
  "allowedParams": { "affid": ["1"], "sub1": ["test"] },
  "byDesign": [
    { "id": "tracking-consistency:meta:*", "reason": "The pixel loads through GTM on purpose" }
  ],
  "manual": [
    { "section": "Acme rules", "text": "Shipping insurance sits after the guarantee and before payment.", "severity": "blocker" }
  ]
}
```

| Key | Effect |
|---|---|
| `require.tracking` | Any of `gtm`, `ga4`, `meta`, `tiktok`, `everflow`. A required tag missing from every page fails. |
| `require.legal` | Which of `terms`, `privacy`, `returns`, `shipping`, `contact`, `faq` must be linked. Default: all but `faq`. |
| `allowedParams` | Affiliate parameter values the order recorder accepts without `--confirm-test-params`. Any other value is refused. |
| `byDesign` | Row ids (with `*` wildcards) that are intended. They stay in the report with your reason and stop counting against the result. |
| `manual` | Extra checklist items for the tester to answer. `severity` is `blocker`, `warn` or `info`. |
