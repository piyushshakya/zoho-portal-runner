"""Line-item rows and PDF verification (regressions from real runs)."""

from portal_runner.core.documents import content_fingerprint, pdf_text
from portal_runner.portals.zoho.inputs import parse_invoice
from portal_runner.portals.zoho.invoice import row_fields, tag_row, verify_invoice_text

# Zoho's trap: the 2nd row on screen is "line_items.2" and an empty row is "line_items.1".
TABLE = """
<table><tbody>
<tr><td><textarea aria-label="Item"></textarea></td>
    <td><input aria-label="Quantity" data-integrity="line_items.0.quantity" value="1.00"></td>
    <td><input aria-label="Rate" data-integrity="line_items.0.rate" value="75000"></td></tr>
<tr><td><textarea aria-label="Item"></textarea></td>
    <td><input aria-label="Quantity" data-integrity="line_items.2.quantity" value="1.00"></td>
    <td><input aria-label="Rate" data-integrity="line_items.2.rate" value="5000"></td></tr>
<tr><td><textarea aria-label="Item"></textarea></td>
    <td><input aria-label="Quantity" data-integrity="line_items.1.quantity" value="1.00"></td>
    <td><input aria-label="Rate" data-integrity="line_items.1.rate" value="0.00"></td></tr>
</tbody></table>
"""

INVOICE_HTML = """
<style>td { padding: 4px 18px; }</style>
<h1>TAX INVOICE # ASSIGN-000102</h1><p>Test Customer B</p>
<table><tr><td>1</td><td>Window AC</td><td>1.00</td><td>75,000.00</td></tr>
<tr><td>2</td><td>Ceiling Fan</td><td>3.00</td><td>5,000.00</td></tr></table>
"""


def inputs(*items: tuple[str, float]):
    return parse_invoice(
        {
            "invoice": {
                "number": "ASSIGN-000102",
                "customer": "Test Customer B",
                "invoice_date": "2026-10-15",
                "items": [{"name": n, "quantity": q} for n, q in items],
            }
        }
    )


async def test_fields_come_from_the_row_being_edited(offline_page):
    page = await offline_page(TABLE)
    assert await tag_row(page.get_by_role("textbox", name="Item").nth(1), 1)
    fields = await row_fields(page, 1)
    assert fields is not None
    rate, quantity = fields
    assert await rate.input_value() == "5000"
    assert await quantity.get_attribute("data-integrity") == "line_items.2.quantity"


async def test_pdf_verification(browser, tmp_path):
    page = await browser.new_page()
    await page.set_content(INVOICE_HTML)
    pdf = tmp_path / "invoice.pdf"
    await page.pdf(path=str(pdf))
    text = pdf_text(pdf)
    assert verify_invoice_text(text, inputs(("Window AC", 1), ("Ceiling Fan", 3))) == []
    assert verify_invoice_text(text, inputs(("Ceiling Fan", 4))) == ["item 'Ceiling Fan' with quantity 4.00 missing"]


def test_quantity_must_not_be_glued_to_other_digits():
    text = "ASSIGN-000102 Test Customer B Window AC 1.00 x Ceiling Fan 13.00"
    assert verify_invoice_text(text, inputs(("Ceiling Fan", 3))) == ["item 'Ceiling Fan' with quantity 3.00 missing"]


def test_fingerprint_ignores_whitespace_only():
    assert content_fingerprint("A  B\nC") == content_fingerprint("A B C")
    assert content_fingerprint("A B C") != content_fingerprint("A B D")
