import json

from portal_runner.core.errors import RetryableError
from portal_runner.core.evidence import write_bundle
from portal_runner.core.redact import Redactor

SENSITIVE = """
<div>Signed in as jane.doe@example.com · org 55500011122</div>
<a href="https://invoice.zoho.in/app/55500011122#/invoices/9876543210000012345">ASSIGN-000101</a>
<input type="password" value="hunter2pass">
<input id="note" value="mail me at other@example.com">
<p>Code used: 482913</p>
<script>window.app = {"user_name": "Someone Private"}; console.log("debug for jane.doe@example.com")</script>
"""

# Zoho renders its invoice preview inside a shadow root.
SHADOW = """
<img src="data:image/gif;base64,R0lGODlhAQABAIAAAP///wAAACw=" width="40" height="40">
<div id="host"></div>
<script>
  const root = document.getElementById('host').attachShadow({mode: 'open'});
  root.innerHTML = '<p>owner.private@example.com</p><div id="inner"></div>';
  root.getElementById('inner').attachShadow({mode: 'open'}).innerHTML = '<span>Jane Doe</span>';
</script>
"""


async def bundle_for(browser, tmp_path, html: str, redactor: Redactor):
    page = await browser.new_page()
    console: list[str] = []
    page.on("console", lambda m: console.append(m.text))
    await page.set_content(html)
    error = RetryableError("could not click for jane.doe@example.com at /app/55500011122#/invoices/9876543210000012345")
    out = await write_bundle(
        tmp_path, page, stage="fill_form", error=error, redactor=redactor, console=console, attempts=3
    )
    return page, out


async def test_bundle_has_every_file_and_no_secrets(browser, tmp_path):
    redactor = Redactor(["jane.doe@example.com", "hunter2pass", "Someone Private"])
    redactor.add("482913")
    _, out = await bundle_for(browser, tmp_path, SENSITIVE, redactor)
    assert {p.name for p in out.iterdir()} >= {"screenshot.png", "page.html", "url.txt", "console.log", "error.json"}
    text = "".join(p.read_text(errors="ignore") for p in out.iterdir() if p.suffix != ".png")
    for leak in ["jane.doe", "hunter2pass", "482913", "other@example.com", "55500011122", "9876543210000", "Someone"]:
        assert leak not in text, leak
    info = json.loads((out / "error.json").read_text())
    assert (info["stage"], info["retryable"], info["attempts"]) == ("fill_form", True, 3)


async def test_shadow_dom_is_scrubbed(browser, tmp_path):
    page, out = await bundle_for(browser, tmp_path, SHADOW, Redactor(["Jane Doe"]))
    shadow_text = await page.evaluate(
        "() => { const r = document.getElementById('host').shadowRoot;"
        " return r.textContent + r.getElementById('inner').shadowRoot.textContent; }"
    )
    assert "owner.private" not in shadow_text
    assert "Jane" not in shadow_text
    report = json.loads((out / "error.json").read_text())["redaction"]
    assert report["shadow_roots_scrubbed"] == 2
    assert report["leftovers_after_scrub"] == []
