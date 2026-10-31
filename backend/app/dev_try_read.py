import glob
import os
import shutil
import time

import pandas as pd
import pymupdf
from PIL import Image, ImageFilter

from app.readers import ReadError, read_file

SRC = sorted(glob.glob("/data/fatura/invoices_dataset_final/images/*.jpg"))[:2]
OUT = "/tmp/rt"
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)

TEXT = "INVOICE # TEST-{n}\nDate: 2024-01-0{n}\nSupplier: Test Co\nTOTAL: {n}00.00 USD\nThis page has a real text layer."

# digital PDF (2 pages with a text layer)
doc = pymupdf.open()
for n in (1, 2):
    doc.new_page().insert_text((72, 72), TEXT.format(n=n))
doc.save(f"{OUT}/digital.pdf")
doc.close()

# scanned PDF (2 pages that are only pictures)
doc = pymupdf.open()
for f in SRC:
    p = doc.new_page()
    p.insert_image(p.rect, filename=f)
doc.save(f"{OUT}/scanned.pdf")
doc.close()

# mixed PDF (page 1 digital, page 2 scanned)
doc = pymupdf.open()
doc.new_page().insert_text((72, 72), TEXT.format(n=1))
p = doc.new_page()
p.insert_image(p.rect, filename=SRC[0])
doc.save(f"{OUT}/mixed.pdf")
doc.close()

# password-protected PDF
doc = pymupdf.open()
doc.new_page().insert_text((72, 72), TEXT.format(n=1))
doc.save(f"{OUT}/locked.pdf", encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
doc.close()

# images: JPG, PNG, 2-frame TIFF, poor quality (small + blurry)
imgs = [Image.open(f).convert("RGB") for f in SRC]
shutil.copy(SRC[0], f"{OUT}/invoice.jpg")
imgs[0].save(f"{OUT}/invoice.png")
imgs[0].save(f"{OUT}/two_frames.tif", save_all=True, append_images=imgs[1:])
small = imgs[0].resize((imgs[0].width // 3, imgs[0].height // 3)).filter(ImageFilter.GaussianBlur(1.2))
small.save(f"{OUT}/poor_quality.jpg")

# spreadsheets
with pd.ExcelWriter(f"{OUT}/two_sheets.xlsx") as w:
    pd.DataFrame({"invoice_no": ["A-1", "A-2"], "date": ["2024-01-01", "2024-01-02"], "total": [100.5, 200]}).to_excel(w, sheet_name="Jan", index=False)
    pd.DataFrame({"invoice_no": ["B-1"], "date": ["2024-02-01"], "total": [50]}).to_excel(w, sheet_name="Feb", index=False)
with open(f"{OUT}/semicolon_latin1.csv", "w", encoding="latin-1") as f:
    f.write("invoice_no;supplier;total\nC-1;Café Müller;12,50\n")

# broken files
with open(f"{OUT}/random.pdf", "wb") as f:
    f.write(os.urandom(2000))
data = open(f"{OUT}/digital.pdf", "rb").read()
with open(f"{OUT}/cut_in_half.pdf", "wb") as f:
    f.write(data[: len(data) // 2])
data = open(SRC[0], "rb").read()
with open(f"{OUT}/cut_off.jpg", "wb") as f:
    f.write(data[: int(len(data) * 0.3)])
with open(f"{OUT}/fake.xlsx", "wb") as f:
    f.write(os.urandom(1000))
open(f"{OUT}/empty.csv", "wb").close()
with open(f"{OUT}/notes.docx", "wb") as f:
    f.write(b"hello")

for name in sorted(os.listdir(OUT)):
    t = time.time()
    try:
        r = read_file(f"{OUT}/{name}")
        confs = [u.ocr_confidence for u in r.units if u.ocr_confidence is not None]
        warns = r.warnings + [w for u in r.units for w in u.warnings]
        print(f"OK    {name:20} {r.file_type:11} units={len(r.units)} methods={sorted({u.method for u in r.units})} "
              f"conf={confs} warn={warns} {round(time.time() - t, 1)}s")
        print(f"      sample: {r.units[0].text[:70]!r}")
    except ReadError as e:
        print(f"ERROR {name:20} code={e.code} | {e.message}")
    except Exception as e:
        print(f"CRASH {name:20} {type(e).__name__}: {e}")
