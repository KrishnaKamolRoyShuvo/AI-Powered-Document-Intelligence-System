import re
from datetime import date, datetime

import pandas as pd
from pydantic import BaseModel

LAYOUTS = {"one_row_per_invoice", "line_item_rows", "single_invoice", "not_invoice_data"}

COLUMN_FIELDS = [
    "invoice_number", "invoice_date", "supplier_name", "customer_name",
    "subtotal", "tax", "discount", "total", "currency",
    "item_description", "item_quantity", "item_unit_price", "item_amount",
]

TOTAL_LABELS = {"total", "totals", "grand total", "subtotal", "sub total"}


class ColumnMap(BaseModel):
    invoice_number: int | None = None
    invoice_date: int | None = None
    supplier_name: int | None = None
    customer_name: int | None = None
    subtotal: int | None = None
    tax: int | None = None
    discount: int | None = None
    total: int | None = None
    currency: int | None = None
    item_description: int | None = None
    item_quantity: int | None = None
    item_unit_price: int | None = None
    item_amount: int | None = None


class SheetMapping(BaseModel):
    layout: str = "not_invoice_data"
    header_row: int | None = None
    columns: ColumnMap = ColumnMap()
    dayfirst: bool | None = None
    decimal_comma: bool | None = False
    default_currency: str | None = None


# ---------- numbers and dates ----------

def parse_number(value, decimal_comma=False):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return None if pd.isna(value) else float(value)
    s = str(value).strip().replace("\u2212", "-")
    if not s:
        return None
    neg = (s.startswith("(") and s.endswith(")")) or s.startswith("-") or s.endswith("-")
    s = re.sub(r"[^\d.,]", "", s)
    if not re.search(r"\d", s):
        return None
    if "." in s and "," in s:
        dec = "," if s.rfind(",") > s.rfind(".") else "."
        s = s.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in s:
        if decimal_comma or not re.fullmatch(r"\d{1,3}(,\d{3})+", s):
            s = s.replace(",", ".")
        else:
            s = s.replace(",", "")
    elif s.count(".") > 1 or (decimal_comma and re.fullmatch(r"\d{1,3}(\.\d{3})+", s)):
        s = s.replace(".", "")
    try:
        n = float(s)
    except ValueError:
        return None
    if abs(n) > 1e13:
        return None
    return -n if neg else n


MONTH_FORMATS = ["%d-%b-%Y", "%d %b %Y", "%d %B %Y", "%d-%B-%Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%d-%b-%y", "%d/%b/%Y"]


def _mk(y, m, d):
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_date(value, dayfirst=None):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = str(value).strip()
    if not s or s.lower() in ("nan", "nat", "none"):
        return None
    s = re.split(r"[ T]\d{1,2}:\d{2}", s)[0].strip()
    m = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    if m:
        return _mk(int(m[1]), int(m[2]), int(m[3]))
    m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000 if y < 70 else 1900
        if a > 12:
            return _mk(y, b, a)
        if b > 12:
            return _mk(y, a, b)
        if a == b:
            return _mk(y, a, b)
        if dayfirst is None:
            return None  # ambiguous: better to flag it than to guess
        return _mk(y, b, a) if dayfirst else _mk(y, a, b)
    for fmt in MONTH_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def infer_dayfirst(values):
    saw_day = saw_month = False
    for v in values:
        if v is None:
            continue
        m = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", str(v).strip())
        if not m:
            continue
        if int(m[1]) > 12:
            saw_day = True
        if int(m[2]) > 12:
            saw_month = True
    if saw_day and not saw_month:
        return True
    if saw_month and not saw_day:
        return False
    return None


# ---------- guessing the mapping from header names (fallback when the AI is unavailable) ----------

def _empty(v):
    return v is None or (not isinstance(v, str) and pd.isna(v))


def _cell(row, idx):
    if idx is None or idx < 0 or idx >= len(row):
        return None
    v = row.iloc[idx]
    if _empty(v):
        return None
    return str(v).strip() or None


def _textish(s):
    return parse_number(s) is None and parse_date(s, dayfirst=True) is None and len(s) < 60


def find_header_row(df, max_rows=10):
    for i in range(min(len(df), max_rows)):
        cells = [str(v).strip() for v in df.iloc[i] if not _empty(v) and str(v).strip()]
        if len(cells) >= 2 and sum(_textish(c) for c in cells) >= 0.8 * len(cells):
            return i
    return None


ORDER = [
    ("invoice_date", ["invoice date", "inv date", "issue date", "date"]),
    ("subtotal", ["subtotal", "sub total", "sub-total", "net total", "net amount"]),
    ("discount", ["discount"]),
    ("tax", ["tax", "vat", "gst"]),
    ("item_amount", ["line total", "line amount", "line value"]),
    ("total", ["grand total", "total amount", "amount due", "balance due", "invoice total", "total"]),
    ("invoice_number", ["invoice number", "invoice no", "invoice #", "invoice id", "inv no", "inv #", "inv.", "bill no", "invoice", "number"]),
    ("currency", ["currency", "curr"]),
    ("supplier_name", ["supplier", "vendor", "seller", "issued by", "from"]),
    ("customer_name", ["customer", "client", "buyer", "bill to", "sold to", "billed to"]),
    ("item_unit_price", ["unit price", "unit cost", "price", "rate"]),
    ("item_quantity", ["quantity", "qty"]),
    ("item_description", ["description", "item", "product", "service"]),
]


def _norm_header(v):
    return re.sub(r"[^a-z0-9#. ]+", " ", str(v).lower()).strip()


def guess_mapping(df):
    header_row = find_header_row(df)
    found = {}
    if header_row is not None:
        headers = [("" if _empty(v) else _norm_header(v)) for v in df.iloc[header_row]]
        for j, h in enumerate(headers):
            if not h or "due" in h:
                continue
            for field, phrases in ORDER:
                if field not in found and any(p in h for p in phrases):
                    found[field] = j
                    break
        has_items = any(f in found for f in ("item_description", "item_quantity", "item_unit_price"))
        for j, h in enumerate(headers):
            if h == "amount" and j not in found.values():
                found.setdefault("item_amount" if has_items else "total", j)
    cols = ColumnMap(**found)
    has_items = any(getattr(cols, f) is not None for f in ("item_description", "item_quantity", "item_unit_price", "item_amount"))
    if cols.invoice_number is None and cols.total is None:
        layout = "not_invoice_data"
    elif has_items and cols.invoice_number is not None:
        layout = "line_item_rows"
    else:
        layout = "one_row_per_invoice"
    dayfirst = None
    if cols.invoice_date is not None and header_row is not None:
        dayfirst = infer_dayfirst([_cell(df.iloc[i], cols.invoice_date) for i in range(header_row + 1, len(df))])
    return SheetMapping(layout=layout, header_row=header_row, columns=cols, dayfirst=dayfirst, decimal_comma=False)


# ---------- building records from rows (no AI, no per-row calls) ----------

HEADER_FIELDS = ("invoice_number", "invoice_date", "supplier_name", "customer_name", "subtotal", "tax", "discount", "total", "currency")


def _new_record():
    return {
        "invoice_number": None, "invoice_date": None, "supplier_name": None, "customer_name": None,
        "subtotal": None, "tax": None, "discount": None, "total": None, "currency": None,
        "line_items": [], "uncertain_fields": [], "unparsed": [], "source": {"rows": []},
    }


def _row_values(row, c, dayfirst, decimal_comma, default_currency):
    v = {
        "invoice_number": _cell(row, c.invoice_number),
        "supplier_name": _cell(row, c.supplier_name),
        "customer_name": _cell(row, c.customer_name),
    }
    unparsed = []
    raw = _cell(row, c.invoice_date)
    v["invoice_date"] = parse_date(raw, dayfirst)
    if raw and v["invoice_date"] is None:
        unparsed.append("invoice_date")
    for f in ("subtotal", "tax", "discount", "total"):
        raw = _cell(row, getattr(c, f))
        v[f] = parse_number(raw, decimal_comma)
        if raw and v[f] is None:
            unparsed.append(f)
    cur = (_cell(row, c.currency) or "").upper()
    v["currency"] = cur if len(cur) == 3 and cur.isalpha() else default_currency
    item = {
        "description": _cell(row, c.item_description),
        "quantity": parse_number(_cell(row, c.item_quantity), decimal_comma),
        "unit_price": parse_number(_cell(row, c.item_unit_price), decimal_comma),
        "amount": parse_number(_cell(row, c.item_amount), decimal_comma),
    }
    v["item"] = item if any(x is not None for x in item.values()) else None
    return v, unparsed


def build_records(df, mapping):
    cols = mapping.columns
    start = mapping.header_row + 1 if mapping.header_row is not None else 0
    cur = (mapping.default_currency or "").strip().upper()
    default_currency = cur if len(cur) == 3 and cur.isalpha() else None
    decimal_comma = bool(mapping.decimal_comma)
    dayfirst = mapping.dayfirst
    if dayfirst is None and cols.invoice_date is not None:
        dayfirst = infer_dayfirst([_cell(df.iloc[i], cols.invoice_date) for i in range(start, len(df))])

    groups, singles, current_key = {}, [], None
    for i in range(start, len(df)):
        v, unparsed = _row_values(df.iloc[i], cols, dayfirst, decimal_comma, default_currency)
        number = v["invoice_number"]
        label = number or (v["item"] or {}).get("description") or ""
        if label.lower().rstrip(":. ") in TOTAL_LABELS:
            continue  # a footer row such as "Total"
        has_data = any(v[k] is not None for k in HEADER_FIELDS[:-1]) or v["item"]
        if not has_data:
            continue
        src_row = int(df.index[i]) + 1
        if mapping.layout == "line_item_rows":
            key = number.lower() if number else (current_key or f"row{src_row}")
            current_key = key
            rec = groups.get(key)
            if rec is None:
                rec = groups[key] = _new_record()
        else:
            rec = _new_record()
            singles.append(rec)
        for f in HEADER_FIELDS:
            if rec[f] is None and v[f] is not None:
                rec[f] = v[f]
        if v["item"]:
            rec["line_items"].append(v["item"])
        rec["source"]["rows"].append(src_row)
        for f in unparsed:
            if f not in rec["unparsed"]:
                rec["unparsed"].append(f)

    out = list(groups.values()) if mapping.layout == "line_item_rows" else singles
    for rec in out:
        rec["unparsed"] = [f for f in rec["unparsed"] if rec[f] is None]
    return out
