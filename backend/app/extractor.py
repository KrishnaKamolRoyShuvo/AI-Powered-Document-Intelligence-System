import os
import time

from google import genai
from google.genai import errors, types

from app.schemas import InvoiceData

PROMPT = """You extract data from ONE invoice. You may get the invoice image, the OCR text, or both.
The OCR text can contain mistakes. If the image and the OCR text disagree, trust the image.

Return ONLY a JSON object with exactly these keys:
invoice_number, invoice_date, supplier_name, customer_name, subtotal, tax, discount, total, currency,
line_items (list of objects with description, quantity, unit_price, amount), uncertain_fields (list of key names), assumptions (list of short text notes).

Rules:
- If a value is not clearly present, use null. Never guess. Never calculate a missing value (the only exception is adding up several printed tax lines).
- invoice_date as YYYY-MM-DD. For a numeric date where day and month could be swapped (for example 02/01/2019), decide the order from clear cues on the document: another date on it with a day above 12, or the country, address, language or currency (Malaysia/MYR/RM, India, the UK, Australia and most of Europe write day first; the USA writes month first). If the cues make the order clear, use it and add a short note to assumptions, for example "date read as day-first because the receipt is Malaysian (MYR)". If there are no clear cues, use null and add "invoice_date" to uncertain_fields.
- Amounts are plain numbers: no currency symbol, no thousands separator.
- currency is a 3-letter code (EUR, USD...) only if it is shown or clearly implied, else null.
- invoice_number is ONLY a number labeled as an invoice number or invoice ID. A PO number, order number, account number or reference is NOT an invoice number. If there is no invoice number, use null.
- tax is the total tax AMOUNT for the whole invoice, not a percentage and not a per-line value. If the invoice shows several separate tax lines (for example GST and VAT), add up their printed amounts and add "tax" to uncertain_fields. Add up only tax lines from the totals section, never per-line tax columns. If no tax amount is shown in the totals section, use null.
- discount is the total discount AMOUNT as a positive number, or null if none is shown.
- supplier_name is who issues the invoice. customer_name is who is billed.
- line_items has one entry per row. Use null for any missing part of a row.
- uncertain_fields lists keys you filled in but are not sure about (blurry, unclear, conflicting).
"""

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(
            api_key=os.environ["GEMINI_API_KEY"],
            http_options=types.HttpOptions(
                timeout=60_000,
                retry_options=types.HttpRetryOptions(attempts=1),
            ),
        )
    return _client


def extract_invoice(ocr_text=None, image_bytes=None, mime_type="image/jpeg"):
    parts = []
    if image_bytes:
        parts.append(types.Part.from_bytes(data=image_bytes, mime_type=mime_type))
    parts.append(PROMPT + "\n\nOCR text:\n" + (ocr_text or "(none)"))

    models = [os.environ.get("GEMINI_MODEL"), os.environ.get("GEMINI_FALLBACK_MODEL")]
    models = [m for m in models if m]
    log = []
    start = time.time()

    for model in models:
        for attempt in (1, 2):
            try:
                resp = _get_client().models.generate_content(
                    model=model,
                    contents=parts,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", temperature=0
                    ),
                )
                data = InvoiceData.model_validate_json(resp.text)
                return {
                    "ok": True,
                    "data": data,
                    "model": model,
                    "seconds": round(time.time() - start, 1),
                    "errors": log,
                }
            except errors.APIError as e:
                log.append(f"{model} try {attempt}: API {e.code}")
                if e.code in (429, 500, 503, 504):
                    time.sleep(2 * attempt)
                    continue
                break  # e.g. 404: go to the next model
            except Exception as e:
                log.append(f"{model} try {attempt}: {type(e).__name__}")

    return {
        "ok": False,
        "data": None,
        "model": None,
        "seconds": round(time.time() - start, 1),
        "errors": log,
    }
