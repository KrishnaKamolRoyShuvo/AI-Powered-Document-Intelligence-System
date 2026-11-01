import os
from datetime import date

import pytest

from app.readers import load_sheets
from app.sheet_parsing import build_records, guess_mapping, infer_dayfirst, parse_date, parse_number

FIX = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.mark.parametrize("text,expected", [
    ("1,234.50", 1234.5), ("12,50", 12.5), ("1.234,56", 1234.56), ("$ 1 234,56", 1234.56),
    ("(12.50)", -12.5), ("EUR 99", 99.0), ("-7", -7.0), ("1,234", 1234.0),
    ("abc", None), ("", None), (None, None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parse_number_decimal_comma_flag():
    assert parse_number("1,234", decimal_comma=True) == 1.234


@pytest.mark.parametrize("text,dayfirst,expected", [
    ("2024-01-05", None, date(2024, 1, 5)),
    ("2024-01-05 00:00:00", None, date(2024, 1, 5)),
    ("26-Feb-2002", None, date(2002, 2, 26)),
    ("25/01/2024", None, date(2024, 1, 25)),
    ("01/25/2024", None, date(2024, 1, 25)),
    ("03/04/2024", True, date(2024, 4, 3)),
    ("03/04/2024", False, date(2024, 3, 4)),
    ("03/04/2024", None, None),
    ("05/05/2024", None, date(2024, 5, 5)),
    ("not a date", None, None),
])
def test_parse_date(text, dayfirst, expected):
    assert parse_date(text, dayfirst) == expected


def test_infer_dayfirst():
    assert infer_dayfirst(["25/01/2024", "02/03/2024"]) is True
    assert infer_dayfirst(["01/25/2024"]) is False
    assert infer_dayfirst(["02/03/2024"]) is None


def _records(filename, sheet=None):
    sheets = load_sheets(os.path.join(FIX, filename))
    df = sheets[sheet or next(iter(sheets))]
    mapping = guess_mapping(df)
    return mapping, build_records(df, mapping)


def test_one_row_per_invoice():
    mapping, recs = _records("invoices_rows.xlsx")
    assert mapping.layout == "one_row_per_invoice"
    assert len(recs) == 6  # the footer "Total" row is skipped
    assert recs[0]["invoice_number"] == "INV-001" and recs[0]["total"] == 110.0
    assert recs[0]["invoice_date"] == date(2024, 1, 5)
    assert recs[4]["invoice_date"] is None and recs[4]["unparsed"] == []
    assert recs[5]["unparsed"] == ["invoice_date"]


def test_line_item_rows_with_decimal_commas():
    mapping, recs = _records("line_items.csv")
    assert mapping.layout == "line_item_rows"
    assert len(recs) == 2
    assert len(recs[0]["line_items"]) == 2 and recs[0]["total"] == 40.0
    assert recs[0]["invoice_date"] == date(2024, 1, 25)
    assert recs[1]["total"] == 1299.9


def test_title_rows_are_skipped():
    mapping, recs = _records("title_rows.xlsx")
    assert len(recs) == 2 and recs[0]["invoice_number"] == "T-1" and recs[1]["total"] == 75.5


def test_sheet_without_invoices():
    mapping, recs = _records("two_sheets.xlsx", sheet="Notes")
    assert mapping.layout == "not_invoice_data" and recs == []
