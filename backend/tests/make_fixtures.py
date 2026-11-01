import os
from datetime import datetime

import pandas as pd
from openpyxl import Workbook

OUT = os.path.join(os.path.dirname(__file__), "fixtures")
os.makedirs(OUT, exist_ok=True)

# 1. one row per invoice: a duplicate, a wrong total, a missing date, a bad date, a footer row
cols = ["Invoice No", "Invoice Date", "Supplier", "Customer", "Subtotal", "Tax", "Total", "Currency"]
rows = [
    ["INV-001", datetime(2024, 1, 5), "Acme Ltd", "Beta Corp", 100.0, 10.0, 110.0, "USD"],
    ["INV-002", datetime(2024, 1, 9), "Acme Ltd", "Gamma LLC", 250.5, 25.05, 275.55, "USD"],
    ["INV-003", datetime(2024, 2, 1), "Delta Co", "Beta Corp", 80.0, 8.0, 99.0, "USD"],
    ["INV-002", datetime(2024, 1, 9), "Acme Ltd", "Gamma LLC", 250.5, 25.05, 275.55, "USD"],
    ["INV-004", None, "Eps Inc", "Beta Corp", 40.0, 4.0, 44.0, "EUR"],
    ["INV-005", "not a date", "Eps Inc", "Zeta SA", 10.0, 1.0, 11.0, "EUR"],
    ["Total", None, None, None, 480.5, 48.05, 539.55, None],
]
with pd.ExcelWriter(os.path.join(OUT, "invoices_rows.xlsx")) as w:
    pd.DataFrame(rows, columns=cols).to_excel(w, sheet_name="Invoices", index=False)

# 2. line items, semicolons, decimal commas, day-first dates, Latin-1 accents
lines = [
    "Inv #;Date;Vendor;Item;Qty;Unit Price;Amount;Total",
    "A-100;25/01/2024;Café Müller;Coffee beans;2;12,50;25,00;40,00",
    ";;;Filter papers;3;5,00;15,00;",
    "A-101;02/02/2024;Café Müller;Espresso machine;1;1.299,90;1.299,90;1.299,90",
]
with open(os.path.join(OUT, "line_items.csv"), "w", encoding="latin-1", newline="") as f:
    f.write("\n".join(lines) + "\n")

# 3. title rows above the header, and a currency note
wb = Workbook()
ws = wb.active
ws.title = "Register"
ws["A1"] = "ACME Ltd - Invoice register"
ws["A2"] = "All amounts in USD"
for c, h in enumerate(["Number", "Date", "Customer", "Amount"], start=1):
    ws.cell(row=4, column=c, value=h)
data = [["T-1", datetime(2024, 4, 1), "Beta Corp", 120.0], ["T-2", datetime(2024, 4, 3), "Zeta SA", 75.5]]
for r, vals in enumerate(data, start=5):
    for c, v in enumerate(vals, start=1):
        ws.cell(row=r, column=c, value=v)
wb.save(os.path.join(OUT, "title_rows.xlsx"))

# 4. two sheets: invoices plus a notes sheet
with pd.ExcelWriter(os.path.join(OUT, "two_sheets.xlsx")) as w:
    pd.DataFrame({"Invoice Number": ["W-1", "W-2"], "Date": [datetime(2024, 5, 1), datetime(2024, 5, 2)], "Total": [30.0, 45.0]}).to_excel(w, sheet_name="Invoices", index=False)
    pd.DataFrame({"Notes": ["Prepared by Finance", "Reviewed on Friday"]}).to_excel(w, sheet_name="Notes", index=False)

# 5. one formatted invoice (labels and values scattered in cells)
wb = Workbook()
ws = wb.active
ws.title = "Invoice"
cells = {
    "A1": "INVOICE", "A3": "Invoice No:", "B3": "S-7788", "A4": "Date:", "B4": "2024-03-15",
    "A5": "From:", "B5": "Delta Co", "A6": "Bill to:", "B6": "Beta Corp",
    "A8": "Item", "B8": "Qty", "C8": "Price", "D8": "Amount",
    "A9": "Widget", "B9": 2, "C9": 20, "D9": 40, "A10": "Gadget", "B10": 1, "C10": 50, "D10": 50,
    "A12": "Subtotal", "D12": 90, "A13": "Tax", "D13": 9, "A14": "Total", "D14": 99, "A15": "Currency: USD",
}
for ref, val in cells.items():
    ws[ref] = val
wb.save(os.path.join(OUT, "single_invoice.xlsx"))
print("fixtures created in", OUT)
