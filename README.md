# Portal Runner

A Playwright program that logs into **Zoho Invoice** with a **second factor (TOTP)**, reuses
the session on later runs, walks three pages deep to the **New Invoice** form, fills it from a
JSON file, saves it, and **downloads the invoice PDF** that Zoho generates. Every run writes a
JSON manifest; every failure writes a redacted evidence bundle.

```
inputs.json ─► session saved? ──yes─► still alive? ──yes──────────────┐
                    │ no                  │ no                         ▼
                    └────► full login: email → password → TOTP ─► Dashboard
                                                                       │
          output/ASSIGN-000101.pdf ◄─ PDF/Print ◄─ Save ◄─ New Invoice ◄─ Invoices
          runs/<id>/manifest.json        (or open the existing invoice with that number)
```

---

## Quick start

```bash
./setup.sh                      # one command: venv, dependencies, browser, .env
# edit .env with your Zoho details (section 2)
source .venv/bin/activate
make test                       # offline tests, no account needed
python -m portal_runner --headed
```

---

## 1. Prerequisites

* macOS or Linux with **Python 3.10+** (`python3 --version`)
* A free **Zoho Invoice** account (section 2)
* An authenticator app on your phone (Google Authenticator, Microsoft Authenticator, ...)

## 2. Set up your Zoho account (one time, about 10 minutes)

1. **Sign up** at <https://www.zoho.com/in/invoice/> (free plan). Finish the organisation setup.
2. **Turn on the second factor:** go to <https://accounts.zoho.in> → *Security* →
   *Multi-Factor Authentication* → choose **Authenticator App** (not OneAuth, not SMS).
3. When the QR code appears, click **"Can't scan?"** to show the **setup key** (a text like
   `JBSW Y3DP EHPK 3PXP`). Scan the QR with your phone too, so you can still log in by hand.
   This key is what lets the program produce the 6-digit codes itself. **Keep it private.**
4. **Create test data** in Zoho Invoice:
   * Customers: `Test Customer A` and `Test Customer B`
   * Items: `Consulting hour`, `Window AC`, `Ceiling Fan` (any rates)
5. Log out and back in once by hand, using the authenticator code, to confirm it works.

## 3. Install

```bash
./setup.sh
```

It creates `.venv`, installs the pinned dependencies and Chromium, copies `.env.example` to
`.env` (private permissions) and installs a git hook that blocks commits containing secrets.
On Linux, if Chromium fails to start, run `.venv/bin/python -m playwright install --with-deps chromium`.

## 4. Configure `.env`

| Variable | Required | Meaning |
|---|---|---|
| `ZOHO_EMAIL` | yes | Your Zoho login email |
| `ZOHO_PASSWORD` | yes | Your Zoho password |
| `ZOHO_TOTP_SECRET` | yes | The setup key from step 2.3 (spaces allowed) |
| `ZOHO_DC` | no | Data centre: `in` (default), `com`, `eu`, `com.au`, ... |
| `REDACT_NAMES` | no | Your display name(s), comma-separated, scrubbed from evidence |

`.env` is git-ignored and never leaves your machine.

## 5. Run

```bash
python -m portal_runner                                 # uses inputs.json, headless
python -m portal_runner --headed --slow-mo 500          # watch it in slow motion
python -m portal_runner --inputs examples/inputs.customer_b.json
```

| Option | What it does |
|---|---|
| `--inputs FILE` | Inputs file (default `inputs.json`) |
| `--headed` | Show the browser window |
| `--slow-mo MS` | Pause before every browser action |
| `--fresh-login` | Ignore the saved session and log in again |
| `--break TARGET` | Make one element unfindable on purpose (e.g. `detail.pdf_print`) to see retries and the evidence bundle |
| `--trace` | Record a replayable Playwright trace (`playwright show-trace runs/<id>/trace-1.zip`). It contains your password and cookies: never share it |

**What you get**

| Path | Content |
|---|---|
| `output/<invoice number>.pdf` | The document Zoho produced |
| `runs/<id>/manifest.json` | Inputs, session decision, every stage with timings and attempts, pages visited, dependent-field checks, which locator strategy found each element, documents with SHA-256 and content fingerprint |
| `runs/<id>/run.log` | The full log (redacted) |
| `runs/<id>/evidence/` | Only on failure: `screenshot.png`, `page.html`, `url.txt`, `console.log`, `error.json` |
| `state/zoho_invoice.json` | The saved session (cookies), file mode `0600` |

Exit codes: `0` document captured, `1` run failed (see the evidence), `2` bad `.env` or inputs.

### Inputs

```json
{
  "portal": "zoho_invoice",
  "invoice": {
    "number": "ASSIGN-000101",
    "customer": "Test Customer A",
    "invoice_date": "2026-10-29",
    "terms": "Net 15",
    "items": [{"name": "Consulting hour", "quantity": 2}],
    "notes": "Thanks for your business."
  }
}
```

`number` is the key: if an invoice with that number exists it is opened instead of created, so
running twice never makes a duplicate. A new number, customer, date, terms or item list gives a
different document with no code change.

---

## 6. Reproduce the evaluation

| You do | Command | Expected |
|---|---|---|
| Run with no saved state | `python -m portal_runner --fresh-login` | Full login through TOTP, invoice created, PDF in `output/`, manifest complete |
| Run again | `python -m portal_runner` | `session: REUSED`, no login page, existing invoice opened, same document (same `content_sha256`), faster (about 8 s vs 30+ s) |
| Corrupt the saved state | `echo garbage > state/zoho_invoice.json` then `python -m portal_runner` | `DEAD (saved session is not valid JSON (corrupt))`, full login, success |
| Change the input JSON | `python -m portal_runner --inputs examples/inputs.customer_b.json` | A different invoice (Customer B, Net 30, two items) |
| Mutate saved HTML, resolver offline | `python -m portal_runner mutate tests/fixtures/invoice_new.html /tmp/m.html --all` then `python -m portal_runner offline /tmp/m.html` | Every field still found; the output shows which strategy won and in which frame |
| Break a step | `python -m portal_runner --break detail.pdf_print` | Three attempts with backoff, then `runs/<id>/evidence/` with nothing sensitive in it |

Add `--strip-aria` to `mutate` to also remove every ARIA attribute: the resolver then falls
back to nearby text, structure and attributes, and still finds every field.

Samples from real runs: [`examples/sample-run/`](examples/sample-run) and
[`examples/sample-failure/`](examples/sample-failure).

## 7. Tests and quality

```bash
make test           # 83 offline tests (real Zoho pages saved as fixtures, no network)
make lint           # ruff format + ruff lint + mypy
make offline        # resolver on the saved Zoho pages
make check-secrets  # also runs automatically before every git commit
```

---

## Design answers

### How the code arrives, and what happens when it is wrong

The second factor is a **TOTP authenticator code** computed from the setup key
(`core/codes.py`, `TotpProvider`, RFC 6238), behind
`async def get_code(challenge) -> str`, so an IMAP or SMS provider could replace it without
touching the flow. Two timing rules: if the current 30-second window has less than 3 seconds
left, wait for the next one (so the code cannot expire while it is typed), and on a retry always
wait for a new window (so the rejected code is never resent).

Zoho shows the code as six one-digit boxes; the flow types the code across them and clicks
Verify. If Zoho rejects it, the program retries **exactly once** with a fresh code. A second
rejection is a final error (more attempts risk locking the account). The code is never logged:
it is registered with the redactor the moment it is used, and errors raised while submitting it
are rewritten with the code masked (Playwright echoes `fill("123456")` in its own error messages).

### How a dead session is detected

Three independent signals, cheapest first (`core/session.py`):

1. **Preflight, without a browser:** the session file is missing, is not valid JSON, or every
   Zoho cookie in it has expired.
2. **Redirect:** opening the app lands on `accounts.zoho.*` (compared by **hostname**: Zoho's
   login URL contains the app URL in its query string, which fooled a substring check).
3. **Logged-in marker:** even on the app host, the sidebar's *Invoices* link must render. A
   half-valid session can serve an empty shell without redirecting.

A dead file is moved aside to `*.dead.json`, a full login runs, and the new session is saved
atomically with `0600` permissions.

### Locator strategy order, and why

Every element is a `Target` described several ways (`portals/zoho/targets.py`). The resolver
(`core/resolver.py`) tries, in order:

1. **role + accessible name**: what a person or screen reader sees; the most stable signal
2. **label / placeholder**
3. **nearby text**: the visible label next to a field, walking up a few ancestors
4. **structure**: position among similar elements ("the 2nd date input")
5. **id / class / data attributes**: last, because they are build artefacts. Zoho
   regenerates its element ids on every page load.

A strategy wins only if it matches **exactly one visible** element; ambiguity falls through
instead of guessing. Every strategy runs in the page and in every iframe, the winner is logged
and stored in the manifest, and failed attempts go into the evidence bundle. Tests run it on
real Zoho pages with renamed classes and ids, reversed siblings, wrapper divs, an iframe, and
with all ARIA removed.

### Portal quirks handled

| Quirk | Handling |
|---|---|
| Native **download event** | The PDF comes from *PDF/Print → PDF* via `expect_download`, then is checked against the inputs |
| **Dependent fields** | *Terms* recomputes *Due Date*; choosing an item fills *Rate*. Both are awaited and verified, and recorded in the manifest |
| TOTP split into **six input boxes** | Found from the first box; typed digit by digit |
| **Popups at random times** | A review prompt, an *Invoice Number Preferences* modal, *Trust this browser?*: handled by Playwright locator handlers, always choosing the harmless option |
| **Shadow DOM** | The invoice preview lives in a shadow root; evidence scrubbing walks into it |
| Sidebar **covering its own links** | The blocking element is identified, then keyboard activation, then a recorded URL fallback |
| Line-item **row numbers that don't match the screen** | Fields are found inside the table row actually edited |

### Which failures retry

`core/retry.py` retries **retryable** errors with exponential backoff (1 s, 2 s, 4 s, with
jitter; up to 3 attempts per stage, resetting the page between attempts): element not rendered
yet, Playwright timeouts, a click blocked by an overlay, a dependent field that has not updated,
transient network errors.

**Final** errors stop immediately and write evidence: wrong password, CAPTCHA, 2FA code
rejected twice, a customer or item not in the dropdown, a duplicate invoice number, a PDF that
does not match the inputs, invalid configuration, and any unexpected exception (a bug should
fail loudly). **Save is never retried**, because a second click could create a duplicate invoice.

### What I would change first for a hundred portals

The code is already split for it: `core/` knows nothing about Zoho, and a portal is one package
implementing the small `Portal` interface (`portals/base.py`) plus one line in the registry.
Next steps, in order:

1. **Targets as data** (YAML per portal) with a recorder that captures and sanitizes fixtures,
   so adding a portal is mostly configuration, and each portal gets its offline mutation tests
   automatically.
2. **A job queue and worker pool** around `Runner`, with per-portal concurrency limits and rate
   limits, session files in a secret store instead of on disk, and a code provider per
   challenge type (TOTP, IMAP, SMS webhook).
3. **Scheduled canary runs** against every portal, so a front-end change shows up as an alert,
   with the resolver's strategy-win history showing which elements are drifting.

---

## Project layout

```
portal_runner/
  cli.py, runner.py          command line and the run lifecycle
  core/                      portal-agnostic engine
    codes.py                 TOTP provider, one-retry rule
    resolver.py              the five-strategy element resolver
    session.py               session store and dead-session detection
    retry.py, errors.py      retry policy and error types
    popups.py, actions.py    popup guard, clicks that explain failures
    record.py, evidence.py   manifest, evidence bundle
    redact.py, documents.py  redaction, PDF text and fingerprint
    offline.py, mutate.py    resolver on saved HTML, HTML mutation tool
  portals/
    base.py                  the Portal interface
    zoho/                    targets, login, invoice flow, popups, inputs, config
tests/                       83 offline tests; fixtures are sanitized real Zoho pages
tools/                       capture_fixture.py, check_secrets.py, pre-commit hook
examples/                    alternative inputs, a sample run, a sample failure bundle
```

## Security and privacy

* Secrets live only in `.env`; the session file is `0600`; `runs/`, `state/`, `output/` are git-ignored.
* Everything written by a run is redacted: the email and its user part, the password, the TOTP
  secret, every TOTP code used, display names, any email address, account and record ids, and
  URL query strings. The live page is scrubbed (including shadow DOM) **before** the screenshot,
  and images such as profile photos are masked.
* `tools/check_secrets.py` runs before every commit and refuses files containing secrets.
* Traces (`--trace`) are **not** redacted and must never be shared.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `error: missing in .env: ...` | Fill in `.env` (section 4) |
| `the TOTP secret is not valid base32` | Copy the setup key again; letters A–Z and digits 2–7 only |
| `2FA: code rejected` twice | Check your computer's clock is set automatically (TOTP depends on it) |
| `Zoho is showing a CAPTCHA` | Log in once by hand in a normal browser, then run again |
| `customer '...' is not in the dropdown` | Create that customer in Zoho, or fix `inputs.json` |
| A run fails | Open `runs/<id>/evidence/screenshot.png` and `error.json` |
| Anything else | Run with `--headed --slow-mo 500` to watch, or `--trace` to replay step by step |

## Limitations

* One portal (Zoho Invoice) is implemented; the structure is ready for more.
* Closed shadow roots cannot be scrubbed from inside the page; the screenshot masking still applies.
* Headless runs present a regular Chrome user agent; there is no further fingerprint hardening.
