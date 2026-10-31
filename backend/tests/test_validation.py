from datetime import date

from app.validation import validate_fields

TODAY = date(2025, 1, 1)

GOOD = {
    "invoice_number": "INV-1",
    "invoice_date": date(2024, 1, 5),
    "supplier_name": "Acme Ltd",
    "customer_name": "Bob",
    "subtotal": 100.0,
    "tax": 10.0,
    "discount": None,
    "total": 110.0,
    "currency": "USD",
    "line_items": [
        {"description": "A", "quantity": 2, "unit_price": 25.0, "amount": 50.0},
        {"description": "B", "quantity": 1, "unit_price": 50.0, "amount": 50.0},
    ],
}


def check(**changes):
    return validate_fields({**GOOD, **changes}, today=TODAY)


def codes(issues):
    return {i.code for i in issues}


def test_clean_invoice_has_no_issues():
    assert check() == []


def test_missing_required_fields_are_errors():
    issues = check(invoice_number=None, total=None)
    errors = {i.field for i in issues if i.code == "missing_field" and i.severity == "error"}
    assert errors == {"invoice_number", "total"}


def test_missing_supplier_is_info_only():
    issues = check(supplier_name=None)
    assert [(i.code, i.severity) for i in issues] == [("missing_field", "info")]


def test_total_mismatch():
    assert "total_mismatch" in codes(check(total=150.0))


def test_small_rounding_difference_is_allowed():
    assert "total_mismatch" not in codes(check(total=110.01))


def test_discount_is_taken_into_account():
    assert check(discount=10.0, tax=5.0, total=95.0) == []


def test_subtotal_mismatch():
    assert "subtotal_mismatch" in codes(check(subtotal=90.0))


def test_line_item_math():
    items = [{**GOOD["line_items"][0], "amount": 60.0}, GOOD["line_items"][1]]
    assert "line_item_math" in codes(check(line_items=items))


def test_negative_amount():
    assert "negative_amount" in codes(check(tax=-5.0))


def test_future_date():
    assert "future_date" in codes(check(invoice_date=date(2030, 1, 1)))


def test_invalid_currency():
    assert "invalid_currency" in codes(check(currency="US"))


def test_subtotal_can_come_from_line_items():
    assert "total_mismatch" not in codes(check(subtotal=None))
