import glob
import json
import os

import pytesseract
from PIL import Image

from app.extractor import extract_invoice

ROOT = "/data/fatura/invoices_dataset_final"


def gold(path):
    d = json.load(open(path))
    out = {}
    for k in ("NUMBER", "DATE", "SELLER_NAME", "SUB_TOTAL", "TAX", "DISCOUNT", "TOTAL"):
        v = d.get(k)
        out[k] = " ".join(v["text"].split()) if isinstance(v, dict) and v.get("text") else None
    return out


images = sorted(glob.glob(f"{ROOT}/images/*.jpg"))
step = max(1, len(images) // 5)
for img in images[::step][:5]:
    name = os.path.basename(img)
    label = gold(f"{ROOT}/Annotations/Original_Format/{name[:-4]}.json")
    ocr = pytesseract.image_to_string(Image.open(img))
    res = extract_invoice(ocr_text=ocr, image_bytes=open(img, "rb").read())
    print("=" * 60)
    print(name, "| model:", res["model"], "| seconds:", res["seconds"], "| ok:", res["ok"])
    if res["errors"]:
        print("errors:", res["errors"])
    if not res["ok"]:
        continue
    d = res["data"]
    rows = [
        ("number", d.invoice_number, label["NUMBER"]),
        ("date", d.invoice_date, label["DATE"]),
        ("supplier", d.supplier_name, label["SELLER_NAME"]),
        ("subtotal", d.subtotal, label["SUB_TOTAL"]),
        ("tax", d.tax, label["TAX"]),
        ("discount", d.discount, label["DISCOUNT"]),
        ("total", d.total, label["TOTAL"]),
        ("currency", d.currency, None),
    ]
    for n, p, g in rows:
        print(f"{n:9} AI: {p!s:26} LABEL: {g!r}")
    print("line items:", len(d.line_items), "| uncertain:", d.uncertain_fields)
