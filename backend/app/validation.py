from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import func, select

from app.models import Record, ValidationIssue

ABS_TOL = 0.02  # rounding tolerance for amounts


@dataclass
class Issue:
    code: str
    severity: str  # "error" and "warning" send a record to review; "info" does not
    field: str | None
    message: str


REQUIRED = {  # field -> severity when it is missing
    "invoice_number": "error",
    "invoice_date": "error",
    "total": "error",
    "supplier_name": "info",
    "customer_name": "info",
    "currency": "info",
}

VALIDATOR_CODES = {
    "missing_field", "invalid_currency", "negative_amount", "zero_total", "future_date",
    "line_item_math", "subtotal_mismatch", "total_mismatch", "duplicate_invoice", "possible_duplicate",
}


def _label(field):
    return field.replace("_", " ").capitalize()


def _close(a, b):
    return abs(a - b) <= max(ABS_TOL, 0.0001 * max(abs(a), abs(b)))


def validate_fields(rec, today=None):
    """Checks one record (a dict). No database needed."""
    today = today or date.today()
    issues = []

    for field, severity in REQUIRED.items():
        value = rec.get(field)
        if value is None or (isinstance(value, str) and not value.strip()):
            issues.append(Issue("missing_field", severity, field, f"{_label(field)} is missing."))

    cur = rec.get("currency")
    if cur and not (len(cur) == 3 and cur.isalpha()):
        issues.append(Issue("invalid_currency", "warning", "currency", f"'{cur}' is not a 3-letter currency code."))

    for field in ("subtotal", "tax", "discount", "total"):
        value = rec.get(field)
        if value is not None and value < 0:
            issues.append(Issue("negative_amount", "warning", field, f"{_label(field)} is negative ({value})."))
    if rec.get("total") == 0:
        issues.append(Issue("zero_total", "warning", "total", "The total is zero."))

    d = rec.get("invoice_date")
    if d and d > today + timedelta(days=1):
        issues.append(Issue("future_date", "warning", "invoice_date", f"The invoice date {d} is in the future."))

    # line items
    items = rec.get("line_items") or []
    bad, amounts = [], []
    for i, li in enumerate(items, 1):
        q, p, a = li.get("quantity"), li.get("unit_price"), li.get("amount")
        if q is not None and p is not None and a is not None and not _close(q * p, a):
            bad.append((i, q, p, a))
        if a is not None:
            amounts.append(a)
    if bad:
        shown = "; ".join(f"line {i}: {q} x {p} = {round(q * p, 2)} but the amount is {a}" for i, q, p, a in bad[:3])
        extra = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
        issues.append(Issue("line_item_math", "warning", "line_items", shown + extra))
    complete = bool(items) and all(li.get("amount") is not None for li in items)
    line_sum = round(sum(amounts), 2) if complete else None

    sub, tax, disc, tot = rec.get("subtotal"), rec.get("tax"), rec.get("discount"), rec.get("total")

    if sub is not None and line_sum is not None:
        ok = _close(line_sum, sub) or (disc is not None and _close(line_sum - disc, sub))
        if not ok:
            issues.append(Issue("subtotal_mismatch", "error", "subtotal",
                                f"The line items add up to {line_sum}, but the subtotal is {sub}."))

    base = sub if sub is not None else line_sum
    if tot is not None and base is not None:
        t, dsc = tax or 0.0, disc or 0.0
        # lenient on purpose: invoices differ on whether tax or discount is already included
        candidates = [base + t - dsc, base + t, base - dsc, base]
        if not any(_close(tot, c) for c in candidates):
            issues.append(Issue("total_mismatch", "error", "total",
                                f"Expected about {round(base + t - dsc, 2)} ({base} + tax {t} - discount {dsc}), but the total is {tot}."))
    return issues


def _norm(s):
    return " ".join(s.lower().split()) if s else None


def find_duplicates(session, rec):
    """rec is a saved Record. Only the later copy of an invoice is flagged."""
    if rec.created_at is None:
        return []
    num, supplier = _norm(rec.invoice_number), _norm(rec.supplier_name)
    mine = (rec.created_at, str(rec.id))

    def earlier(o):
        return (o.created_at, str(o.id)) < mine

    if num:
        q = select(Record).where(
            Record.id != rec.id, func.lower(func.trim(Record.invoice_number)) == num
        ).limit(50)
        for o in session.scalars(q):
            if not earlier(o):
                continue
            o_sup = _norm(o.supplier_name)
            if supplier and o_sup:
                same = supplier == o_sup
            else:
                same = o.total is not None and rec.total is not None and _close(float(o.total), float(rec.total))
            if same:
                return [Issue("duplicate_invoice", "error", "invoice_number",
                              f"Looks like a duplicate of invoice {o.invoice_number} (record {o.id}).")]
    elif rec.invoice_date and rec.total is not None and supplier:
        q = select(Record).where(
            Record.id != rec.id, Record.invoice_date == rec.invoice_date, Record.total == rec.total,
            func.lower(func.trim(Record.supplier_name)) == supplier,
        ).limit(50)
        for o in session.scalars(q):
            if earlier(o):
                return [Issue("possible_duplicate", "warning", None,
                              f"Same supplier, date and total as record {o.id}.")]
    return []


def _f(v):
    return float(v) if v is not None else None


def record_to_dict(rec):
    return {
        "invoice_number": rec.invoice_number, "invoice_date": rec.invoice_date,
        "supplier_name": rec.supplier_name, "customer_name": rec.customer_name,
        "subtotal": _f(rec.subtotal), "tax": _f(rec.tax), "discount": _f(rec.discount),
        "total": _f(rec.total), "currency": rec.currency, "line_items": rec.line_items or [],
    }


def revalidate(session, rec):
    """Replaces the validator's issues on a saved record and updates needs_review."""
    if not rec.ai_data and rec.total is None and rec.invoice_number is None:
        rec.needs_review = True  # nothing was extracted; keep the read/extraction error as is
        return
    for iss in list(rec.issues):
        if iss.code in VALIDATOR_CODES:
            rec.issues.remove(iss)
    for i in validate_fields(record_to_dict(rec)) + find_duplicates(session, rec):
        rec.issues.append(ValidationIssue(code=i.code, severity=i.severity, field=i.field, message=i.message))
    rec.needs_review = rec.review_status != "approved" and any(not i.resolved and i.severity in ("error", "warning") for i in rec.issues)
