# Examples

| Path | What it is |
|---|---|
| `inputs.customer_b.json` | A second inputs file: another customer, date, terms and two line items. Same code, different document. |
| `sample-run/` | A real successful run: fresh TOTP login, new invoice with two items, PDF downloaded and verified. `manifest.json` + `run.log`. |
| `sample-failure/` | A real failure from `--break detail.pdf_print`: three attempts with backoff, then the evidence bundle in `evidence/` (screenshot, page HTML, URL, console log, `error.json`). |

All samples come from real runs and are redacted (emails, names, account and record ids).
The PDF itself is not included: it prints the organisation's contact email.
