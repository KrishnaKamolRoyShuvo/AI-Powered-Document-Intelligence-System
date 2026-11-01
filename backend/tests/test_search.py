from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.search import _like, build_search_text


def rec(**changes):
    base = dict(
        invoice_number="INV-1", supplier_name="Acme Ltd", customer_name="Beta", invoice_date=date(2024, 1, 5),
        total=Decimal("110.00"), currency="USD", line_items=[{"description": "Desk"}, {"description": "Chair"}],
    )
    base.update(changes)
    return SimpleNamespace(**base)


def test_search_text_has_the_main_fields():
    t = build_search_text(rec(), "a.pdf")
    assert "File: a.pdf" in t and "Invoice: INV-1" in t and "Supplier: Acme Ltd" in t
    assert "Total: 110.00 USD" in t and "Items: Desk; Chair" in t


def test_search_text_skips_missing_values():
    t = build_search_text(rec(invoice_number=None, total=None, line_items=[]), "a.pdf")
    assert "Invoice:" not in t and "Total:" not in t and "Items" not in t


def test_page_text_is_added_for_pages_but_not_for_sheets():
    assert "Text: hello world" in build_search_text(rec(), "a.pdf", "hello   world")
    assert "Text:" not in build_search_text(rec(), "a.xlsx", "hello world", is_sheet=True)


def test_like_escapes_wildcards():
    assert _like("50%_off") == "%50\\%\\_off%"
