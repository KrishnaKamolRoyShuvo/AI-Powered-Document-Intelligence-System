import os
import time
from datetime import date

import pandas as pd
from google.genai import errors, types

from app.extractor import _get_client, extract_invoice
from app.sheet_parsing import COLUMN_FIELDS, LAYOUTS, SheetMapping, build_records, guess_mapping

PROMPT = """You are looking at the first rows of ONE spreadsheet sheet that may contain invoices.
Each row starts with its row number (0 = first row shown) and each cell is shown as [column index] value.

Return ONLY a JSON object with these keys:
- layout: one of
    "one_row_per_invoice"  (every data row is one invoice)
    "line_item_rows"       (every data row is one line item; rows with the same invoice number form one invoice)
    "single_invoice"       (the sheet is one formatted invoice with labels and values scattered in cells)
    "not_invoice_data"     (the sheet has no invoice data)
- header_row: the row number of the column headers, or null if there is none
- columns: an object giving, for each field below, the COLUMN INDEX that holds it, or null if no column holds it:
    invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, discount, total, currency,
    item_description, item_quantity, item_unit_price, item_amount
- dayfirst: true if dates like 03/04/2024 mean 3 April, false if they mean March 4, null if unclear or unambiguous
- decimal_comma: true if numbers use a comma as the decimal separator (for example 12,50), otherwise false
- default_currency: a 3-letter code if the sheet states one currency for everything (for example in a title row), else null

Rules: never invent a column. If unsure about a field, use null. Use column indexes exactly as shown.
"""


def sample_text(df, max_rows=25, max_cols=40):
    lines = []
    for i in range(min(len(df), max_rows)):
        cells = []
        for j in range(min(df.shape[1], max_cols)):
            v = df.iat[i, j]
            if (not isinstance(v, str) and pd.isna(v)) or str(v).strip() == "":
                continue
            cells.append(f"[{j}] {str(v).strip()[:60]}")
        lines.append(f"row {i}: " + " | ".join(cells))
    return f"The sheet has {len(df)} rows and {df.shape[1]} columns. First rows:\n" + "\n".join(lines)


def ask_mapping(sample):
    models = [m for m in (os.environ.get("GEMINI_MODEL"), os.environ.get("GEMINI_FALLBACK_MODEL")) if m]
    errs = []
    for model in models:
        for attempt in (1, 2):
            try:
                resp = _get_client().models.generate_content(
                    model=model,
                    contents=[PROMPT + "\n\n" + sample],
                    config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0),
                )
                return SheetMapping.model_validate_json(resp.text), model, errs
            except errors.APIError as e:
                errs.append(f"{model} try {attempt}: API {e.code}")
                if e.code in (429, 500, 503, 504):
                    time.sleep(2 * attempt)
                    continue
                break
            except Exception as e:
                errs.append(f"{model} try {attempt}: {type(e).__name__}")
    return None, None, errs


def _check(m, df):
    """Rejects a mapping that does not fit the sheet. Returns None if it is unusable."""
    if m.layout not in LAYOUTS:
        return None
    if m.header_row is not None and not (0 <= m.header_row < len(df)):
        m.header_row = None
    for f in COLUMN_FIELDS:
        v = getattr(m.columns, f)
        if v is not None and not (0 <= v < df.shape[1]):
            setattr(m.columns, f, None)
    if m.layout in ("one_row_per_invoice", "line_item_rows") and m.columns.invoice_number is None and m.columns.total is None:
        return None
    return m


def _to_date(text):
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def _from_ai(d, sheet):
    return {
        "invoice_number": d.invoice_number, "invoice_date": _to_date(d.invoice_date),
        "supplier_name": d.supplier_name, "customer_name": d.customer_name,
        "subtotal": d.subtotal, "tax": d.tax, "discount": d.discount, "total": d.total,
        "currency": (d.currency or "")[:3].upper() or None,
        "line_items": [li.model_dump() for li in d.line_items],
        "uncertain_fields": d.uncertain_fields, "unparsed": [],
        "source": {"sheet": sheet, "rows": []},
    }


def extract_sheet(name, df):
    mapping, model, errs = ask_mapping(sample_text(df))
    method = "ai-mapping"
    if mapping is not None:
        mapping = _check(mapping, df)
    if mapping is None:
        mapping, method, model = guess_mapping(df), "header-guess", None
    out = {"layout": mapping.layout, "method": method, "model": model, "errors": errs,
           "mapping": mapping.model_dump(), "records": []}
    if mapping.layout == "not_invoice_data":
        return out
    if mapping.layout == "single_invoice":
        res = extract_invoice(ocr_text=df.to_csv(index=False, header=False)[:30000])
        if res["ok"]:
            out["records"] = [_from_ai(res["data"], name)]
            out["method"], out["model"] = "text-extraction", res["model"]
        return out
    recs = build_records(df, mapping)
    for r in recs:
        r["source"]["sheet"] = name
    out["records"] = recs
    return out
