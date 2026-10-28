import copy
from datetime import date

import pytest

from portal_runner.core.errors import InputError
from portal_runner.portals.zoho.inputs import expected_due_date, parse_invoice, zoho_date

GOOD = {
    "portal": "zoho_invoice",
    "invoice": {
        "number": "ASSIGN-000101",
        "customer": "Test Customer A",
        "invoice_date": "2026-10-29",
        "terms": "Net 15",
        "items": [{"name": "Consulting hour", "quantity": 2}],
    },
}


def with_invoice(**changes) -> dict:
    data = copy.deepcopy(GOOD)
    data["invoice"].update(changes)
    return data


def test_good_inputs_parse():
    inv = parse_invoice(GOOD)
    assert inv.number == "ASSIGN-000101"
    assert inv.invoice_date == date(2026, 10, 29)
    assert inv.items[0].quantity == 2


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"number": ""}, "number is required"),
        ({"number": "bad number!"}, "invoice.number"),
        ({"number": "A/B"}, "invoice.number"),  # would become a path in the output file name
        ({"invoice_date": "29/10/2026"}, "YYYY-MM-DD"),
        ({"items": []}, "items is required"),
        ({"items": [{"name": "x", "quantity": 0}]}, "positive"),
        ({"items": [{"name": "x", "quantity": True}]}, "positive"),
    ],
)
def test_bad_inputs_have_clear_errors(changes, message):
    with pytest.raises(InputError, match=message):
        parse_invoice(with_invoice(**changes))


@pytest.mark.parametrize(
    ("terms", "expected"),
    [
        ("Due on Receipt", date(2026, 10, 29)),
        ("Net 15", date(2026, 11, 13)),
        ("Net 30", date(2026, 11, 28)),
        ("Due end of the month", date(2026, 10, 31)),
        ("Due end of next month", date(2026, 11, 30)),
        ("Some custom terms", None),
    ],
)
def test_due_date_rules(terms, expected):
    assert expected_due_date(date(2026, 10, 29), terms) == expected


def test_due_end_of_next_month_crosses_the_year():
    assert expected_due_date(date(2026, 12, 31), "Due end of next month") == date(2027, 1, 31)


def test_zoho_date_format():
    assert zoho_date(date(2026, 1, 5)) == "05/01/2026"
