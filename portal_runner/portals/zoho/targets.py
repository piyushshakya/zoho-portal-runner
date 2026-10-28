"""Every element the Zoho flow touches, each described several ways.

No Zoho ``id`` is used anywhere: Zoho regenerates ids (e.g. "a79f4c30b") on every page load.
"""

from portal_runner.core.resolver import Target

CUSTOMER = Target(
    key="invoice.customer",
    role="combobox",
    name="Customer Name",
    label="Customer Name",
    near_text="Customer Name",
    structure=("[ac-toggler]", 0),  # Zoho marks its dropdown widgets with ac-toggler
    css=("[data-integrity='customer_id'] [ac-toggler]", "#customer-selection .ac-box"),
)
INVOICE_NUMBER = Target(
    key="invoice.number",
    role="textbox",
    name="Invoice#",
    label="Invoice#",
    near_text="Invoice#",
    css=("[data-integrity='invoice_number']", "input[data-auto-gen-binding-key='invoice_number']"),
)
INVOICE_DATE = Target(
    key="invoice.date",
    role="textbox",
    name="Invoice Date",
    label="Invoice Date",
    near_text="Invoice Date",
    structure=("input[placeholder='dd/MM/yyyy']", 0),
    css=("input.zf-date-picker[data-integrity='date']",),
)
TERMS = Target(
    key="invoice.terms",
    role="combobox",
    name="Terms",
    label="Terms",
    near_text="Terms",
    structure=("[ac-toggler]", 1),
    css=("[data-binding-key='payment_terms_label'] [ac-toggler]", ".payment-terms-selection .ac-box"),
)
DUE_DATE = Target(
    key="invoice.due_date",
    role="textbox",
    name="Due Date",
    label="Due Date",
    near_text="Due Date",
    structure=("input[placeholder='dd/MM/yyyy']", 1),
    css=("input.zf-date-picker.editable",),
)
ITEM = Target(
    key="invoice.item[0]",
    role="combobox",
    name="Type or click to select an item.",
    label="Type or click to select an item.",
    structure=("textarea", 0),
    css=("[data-integrity='line_items.0.item_id'] textarea", "textarea.zb-invoice-item-textarea"),
)
QUANTITY = Target(
    key="invoice.quantity[0]",
    role="textbox",
    name="Quantity",
    label="Quantity",
    css=("[data-integrity='line_items.0.quantity']", "input.qty-field"),
)
RATE = Target(
    key="invoice.rate[0]",
    role="textbox",
    name="Rate",
    label="Rate",
    css=("[data-integrity='line_items.0.rate']",),
)
SAVE_DRAFT = Target(
    key="invoice.save_draft",
    role="button",
    name="Save as Draft",
    near_text="Save as Draft",
    css=("[data-agent-save='primary-save-btn']",),
)

INVOICE_FORM = (CUSTOMER, INVOICE_NUMBER, INVOICE_DATE, TERMS, DUE_DATE, ITEM, QUANTITY, RATE, SAVE_DRAFT)


def item_targets(i: int) -> tuple[Target, Target]:
    """(item, quantity) targets for line item row ``i``. Rows share labels, so for
    i > 0 role/label are ambiguous by design and the resolver falls through to structure."""
    if i == 0:
        return ITEM, QUANTITY
    item = Target(
        key=f"invoice.item[{i}]",
        role="combobox",
        name="Type or click to select an item.",
        structure=("textarea[placeholder='Type or click to select an item.']", i),
        css=(f"[data-integrity='line_items.{i}.item_id'] textarea",),
    )
    quantity = Target(
        key=f"invoice.quantity[{i}]",
        role="textbox",
        name="Quantity",
        css=(f"[data-integrity='line_items.{i}.quantity']",),
    )
    return item, quantity


def rate_target(i: int) -> Target:
    return Target(
        key=f"invoice.rate[{i}]", role="textbox", name="Rate", css=(f"[data-integrity='line_items.{i}.rate']",)
    )


ADD_ROW = Target(key="invoice.add_row", role="link", name="Add New Row", near_text="Add New Row")
NOTES = Target(
    key="invoice.notes", role="textbox", name="Customer Notes", label="Customer Notes", near_text="Customer Notes"
)

# Login (accounts.zoho.*)
LOGIN_EMAIL = Target(
    key="login.email",
    role="textbox",
    name="Email address or mobile number",
    placeholder="Email address or mobile number",
    css=("#login_id", "input[name='LOGIN_ID']"),
)
LOGIN_NEXT = Target(key="login.next", role="button", name="Next", css=("#nextbtn",))
LOGIN_PASSWORD = Target(
    key="login.password", placeholder="Enter password", label="Password", css=("#password", "input[type=password]")
)
LOGIN_SIGN_IN = Target(key="login.sign_in", role="button", name="Sign in", css=("#nextbtn",))
LOGIN_TOTP = Target(  # the first of six one-digit boxes
    key="login.totp",
    placeholder="●",
    structure=("input[autocomplete='one-time-code']", 0),
    css=("#mfa_totp input:not([type=hidden])",),
)
LOGIN_VERIFY = Target(key="login.verify", role="button", name="Verify", css=("#totpverifybtn",))

# Navigation
NAV_INVOICES = Target(
    key="nav.invoices", role="link", name="Invoices", css=("a[href$='#/invoices']", "a[href*='#/invoices']")
)
NEW_INVOICE = Target(
    key="invoices.new", role="button", name="New", css=("a[href*='#/invoices/new']", "button.btn-primary")
)


# Invoice detail page
PDF_PRINT = Target(key="detail.pdf_print", role="button", name="PDF/Print", near_text="PDF/Print")
PDF_OPTION = Target(
    key="detail.pdf_option",
    role="menuitem",
    name="PDF",
    structure=(".dropdown-menu.show a, .dropdown-menu.show button", 0),
)
