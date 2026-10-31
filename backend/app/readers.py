import io
import os
import zipfile
from dataclasses import dataclass, field

import pandas as pd
import pymupdf
import pytesseract
from PIL import Image, ImageOps, UnidentifiedImageError

PDF_EXT = {".pdf"}
IMAGE_EXT = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
MIN_TEXT_CHARS = 40       # fewer characters than this on a PDF page = treat as scanned
MAX_SHEET_TEXT = 200_000  # cap for stored sheet text
LOW_OCR_CONF = 60.0


class ReadError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Unit:
    index: int            # 1-based page, TIFF frame or sheet number
    kind: str             # "page" or "sheet"
    text: str
    method: str           # "text-layer", "ocr", "table" or "failed"
    name: str | None = None
    ocr_confidence: float | None = None
    rows: int | None = None
    cols: int | None = None
    warnings: list = field(default_factory=list)


@dataclass
class ReadResult:
    filename: str
    file_type: str        # "pdf", "image" or "spreadsheet"
    units: list
    warnings: list = field(default_factory=list)


def detect_type(path):
    ext = os.path.splitext(path)[1].lower()
    if os.path.getsize(path) == 0:
        raise ReadError("empty_file", "The file is empty.")
    with open(path, "rb") as f:
        head = f.read(1024)
    if ext in PDF_EXT:
        if b"%PDF" not in head:
            raise ReadError("corrupted", "Not a valid PDF file.")
        return "pdf"
    if ext in IMAGE_EXT:
        return "image"
    if ext == ".xlsx":
        if not zipfile.is_zipfile(path):
            raise ReadError("corrupted", "Not a valid XLSX file (damaged or password protected).")
        return "spreadsheet"
    if ext == ".xls":
        if not head.startswith(b"\xd0\xcf\x11\xe0"):
            raise ReadError("corrupted", "Not a valid XLS file.")
        return "spreadsheet"
    if ext == ".csv":
        return "spreadsheet"
    raise ReadError("unsupported_type", f"Unsupported file type: {ext or 'none'}")


# ---------- OCR ----------

def _prepare(img):
    img = img.convert("L")
    if min(img.size) < 1000:
        img = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
    return ImageOps.autocontrast(img, cutoff=1)


def ocr_image(img, preprocess=True):
    if preprocess:
        img = _prepare(img)
    try:
        data = pytesseract.image_to_data(
            img, output_type=pytesseract.Output.DICT, timeout=90
        )
    except RuntimeError:  # OCR timed out
        return "", None
    lines, confs = {}, []
    for i, word in enumerate(data["text"]):
        word = word.strip()
        if not word:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        lines.setdefault(key, []).append(word)
        c = float(data["conf"][i])
        if c >= 0:
            confs.append(c)
    text = "\n".join(" ".join(words) for words in lines.values())
    conf = round(sum(confs) / len(confs), 1) if confs else None
    return text, conf


def _ocr_unit(index, img, preprocess):
    text, conf = ocr_image(img, preprocess)
    warns = []
    if not text.strip():
        warns.append("no_text_found")
    elif conf is not None and conf < LOW_OCR_CONF:
        warns.append("low_ocr_confidence")
    return Unit(index, "page", text, "ocr", ocr_confidence=conf, warnings=warns)


def _failed_unit(index, err):
    return Unit(index, "page", "", "failed", warnings=[f"read_failed: {type(err).__name__}"])


# ---------- PDF ----------

def _read_pdf(path, preprocess):
    try:
        doc = pymupdf.open(path)
    except Exception as e:
        raise ReadError("corrupted", "Could not open the PDF (damaged file).") from e
    try:
        if doc.needs_pass:
            raise ReadError("password_protected", "The PDF is password protected.")
        if doc.page_count == 0:
            raise ReadError("empty_file", "The PDF has no pages.")
        units = []
        for i in range(doc.page_count):
            try:
                page = doc[i]
                text = page.get_text("text").strip()
                if len(text) >= MIN_TEXT_CHARS:
                    units.append(Unit(i + 1, "page", text, "text-layer"))
                    continue
                pix = page.get_pixmap(dpi=200)
                img = Image.open(io.BytesIO(pix.tobytes("png")))
                units.append(_ocr_unit(i + 1, img, preprocess))
            except Exception as e:  # one bad page must not kill the whole file
                units.append(_failed_unit(i + 1, e))
        return units
    finally:
        doc.close()


# ---------- Images (JPG, PNG, TIFF) ----------

def _read_image(path, preprocess):
    try:
        img = Image.open(path)
        frames = getattr(img, "n_frames", 1)
    except (UnidentifiedImageError, OSError) as e:
        raise ReadError("corrupted", "Could not open the image (damaged or unsupported).") from e
    units = []
    for i in range(frames):
        try:
            img.seek(i)
            units.append(_ocr_unit(i + 1, img.convert("RGB"), preprocess))
        except Exception as e:
            units.append(_failed_unit(i + 1, e))
    return units


# ---------- Spreadsheets (XLSX, XLS, CSV) ----------

def _read_csv(path):
    with open(path, "rb") as f:
        bom = f.read(2)
    encodings = ["utf-16"] if bom in (b"\xff\xfe", b"\xfe\xff") else ["utf-8-sig", "latin-1"]
    last = None
    for enc in encodings:
        for sep in (None, ","):  # None = detect the delimiter
            try:
                return pd.read_csv(
                    path, header=None, dtype=str, sep=sep,
                    engine="python", encoding=enc, on_bad_lines="skip",
                )
            except pd.errors.EmptyDataError:
                raise ReadError("empty_file", "The CSV has no data.")
            except Exception as e:
                last = e
    raise ReadError("corrupted", "Could not parse the CSV.") from last


def load_sheets(path):
    """Return {sheet name: DataFrame of raw cells, no header}. Used again later for extraction."""
    if os.path.splitext(path)[1].lower() == ".csv":
        frames = {"csv": _read_csv(path)}
    else:
        try:
            frames = pd.read_excel(path, sheet_name=None, header=None, dtype=str)
        except Exception as e:
            raise ReadError("corrupted", "Could not open the spreadsheet (damaged or password protected).") from e
    out = {}
    for name, df in frames.items():
        out[str(name)] = df.dropna(how="all").dropna(axis=1, how="all").reset_index(drop=True)
    return out


def _read_sheets(path):
    units = []
    for i, (name, df) in enumerate(load_sheets(path).items(), start=1):
        if df.empty:
            units.append(Unit(i, "sheet", "", "table", name=name, rows=0, cols=0, warnings=["empty_sheet"]))
            continue
        text = df.to_csv(index=False, header=False, na_rep="")
        warns = []
        if len(text) > MAX_SHEET_TEXT:
            text = text[:MAX_SHEET_TEXT]
            warns.append("text_truncated")
        units.append(Unit(i, "sheet", text, "table", name=name, rows=len(df), cols=df.shape[1], warnings=warns))
    return units


# ---------- Public API ----------

def read_file(path, preprocess=True):
    ftype = detect_type(path)
    if ftype == "pdf":
        units = _read_pdf(path, preprocess)
    elif ftype == "image":
        units = _read_image(path, preprocess)
    else:
        units = _read_sheets(path)
    if units and all(u.method == "failed" for u in units):
        raise ReadError("corrupted", "No page or frame could be read.")
    warnings = []
    if not any(u.text.strip() for u in units):
        warnings.append("no_text_found")
    return ReadResult(os.path.basename(path), ftype, units, warnings)


def unit_image_bytes(path, file_type, index, dpi=150):
    """Render one page/frame as JPEG bytes (used later to send the image to the AI)."""
    if file_type == "pdf":
        with pymupdf.open(path) as doc:
            return doc[index - 1].get_pixmap(dpi=dpi).tobytes("jpeg"), "image/jpeg"
    if file_type == "image":
        img = Image.open(path)
        img.seek(index - 1)
        buf = io.BytesIO()
        img.convert("RGB").save(buf, "JPEG", quality=90)
        return buf.getvalue(), "image/jpeg"
    raise ValueError("Only PDFs and images have page images.")
