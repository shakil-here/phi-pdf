"""Every PDF tool as a pure function over files: merge, split, organize, compress, OCR, convert, edit, stamp, protect…

A tool reads its inputs and writes NEW files into an output folder; inputs are never changed. The web app (web.py),
the REST API (/api/v1) and the command line (cli.py) all run tools through `run()`, so they behave the same.
PyMuPDF does the PDF work. Text drawn on pages goes through `insert_htmlbox` (HarfBuzz shaping), so Bengali,
Devanagari and Arabic print correctly; MuPDF's built-in Noto fonts cover scripts the chosen font lacks.

Coordinates are PDF points of the page as it is seen (rotated pages get `remove_rotation()` before drawing).
"""
from __future__ import annotations

import base64
import html
import io
import math
import re
import zipfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pymupdf

Progress = Callable[[int, int, str], None]

MM = 72 / 25.4
PAPER = {"A4": (595.28, 841.89), "A3": (841.89, 1190.55), "A5": (419.53, 595.28), "Letter": (612.0, 792.0),
         "Legal": (612.0, 1008.0)}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}
OFFICE_SUFFIXES = {".docx", ".doc", ".odt", ".rtf", ".xlsx", ".xls", ".ods", ".csv", ".pptx", ".ppt", ".odp"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".html", ".htm"}
MAX_OUTPUTS = 500
MAX_PAGES = 5000
SPLIT_FILES_LIMIT = 20          # more pictures/pages than this go out as one ZIP (they would flood "My files")
POSITIONS = ("tl", "tc", "tr", "ml", "c", "mr", "bl", "bc", "br")
BN_DIGITS = str.maketrans("0123456789", "০১২৩৪৫৬৭৮৯")
FONTS = {"sans": "Sans (Helvetica + Noto)", "serif": "Serif (Times + Noto Serif)", "mono": "Monospace"}


class ToolError(ValueError):
    """A message for the person using the tool: wrong pages, a locked PDF, nothing to do…"""


@dataclass
class Input:
    path: Path
    name: str            # the file's name as the person knows it ("Physics book.pdf")
    kind: str = "pdf"    # pdf | image | office | text | zip

    @property
    def stem(self) -> str:
        return Path(self.name).stem[:80] or "file"


@dataclass
class Output:
    path: Path
    name: str
    note: str = ""


@dataclass
class Result:
    outputs: list[Output] = field(default_factory=list)
    summary: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Tool:
    id: str
    label: str
    group: str
    icon: str
    blurb: str
    accepts: str = "pdf"          # pdf | image (pictures, PDFs too) | any (PDF or picture) | office | text
    min_files: int = 1
    max_files: int = 1
    page: str = ""                # a tool with its own page (organize, edit)
    preview: bool = False         # the tool page can show a live preview of one page
    needs: str = ""               # a program it needs: tesseract | libreoffice (see capabilities.py)


GROUPS = {"organize": "Organize", "optimize": "Optimize & OCR", "convert": "Convert", "edit": "Edit & stamp",
          "security": "Security & privacy"}
TOOLS: dict[str, Tool] = {t.id: t for t in (
    Tool("merge", "Merge PDF", "organize", "merge", "Join PDFs and pictures into one PDF, in the order you choose, "
         "with a bookmark per file.", "any", 2, 200),
    Tool("split", "Split PDF", "organize", "split", "Cut a PDF into parts: by page ranges, every N pages, each page, "
         "or by its chapters (bookmarks)."),
    Tool("organize", "Organize pages", "organize", "grid", "See every page: drag to reorder, rotate, delete, "
         "duplicate, add blank pages or pages from other PDFs.", "pdf", 1, 20, page="organize"),
    Tool("remove", "Remove pages", "organize", "remove", "Delete the pages you pick."),
    Tool("extract", "Extract pages", "organize", "extract", "Take the pages you pick into a new PDF, in your order."),
    Tool("rotate", "Rotate pages", "organize", "rotate", "Turn all or some pages by 90°, 180° or 270°."),
    Tool("insert", "Insert pages", "organize", "insert", "Put blank pages or pages of another PDF/picture at the "
         "start, the end or after any page."),
    Tool("compress", "Compress PDF", "optimize", "compress", "Make a PDF smaller: pictures resampled, fonts "
         "subset, unused data removed. Choose how strong."),
    Tool("ocr", "OCR: scans to text", "optimize", "scan", "Read scanned pages in 100+ languages (বাংলা too): a "
         "searchable PDF, a Word file or plain text. Cleans and straightens pages first.", needs="ocr"),
    Tool("repair", "Repair PDF", "optimize", "repair", "Rebuild a damaged PDF that will not open or print."),
    Tool("pdf2word", "PDF to Word", "convert", "word", "An editable .docx with the layout, tables and pictures "
         "kept, or plain flowing text."),
    Tool("pdf2excel", "PDF to Excel", "convert", "table", "Find the tables in a PDF and put each one in a "
         "spreadsheet (.xlsx)."),
    Tool("pdf2ppt", "PDF to PowerPoint", "convert", "slides", "Every page as a slide (.pptx), with the page text in "
         "the speaker notes."),
    Tool("office2pdf", "Office to PDF", "convert", "office", "Word, Excel, PowerPoint and LibreOffice files to PDF.",
         "office", 1, 50, needs="libreoffice"),
    Tool("text2pdf", "Text, Markdown or HTML to PDF", "convert", "doc", "Type or upload text, Markdown or HTML and "
         "get a clean PDF (বাংলা works).", "text", 1, 50),
    Tool("img2pdf", "Images to PDF", "convert", "image", "JPG, PNG, WEBP, TIFF… to a PDF: page size, margins, "
         "orientation; phone photos turned upright.", "image", 1, 300),
    Tool("pdf2img", "PDF to images", "convert", "picture", "Each page as a PNG or JPG at the resolution you "
         "need."),
    Tool("pdf2text", "PDF to text", "convert", "text", "All the text of a PDF as a .txt file."),
    Tool("extract_images", "Extract pictures", "convert", "pictures", "Save every picture inside a PDF in its "
         "original quality."),
    Tool("edit", "Edit PDF", "edit", "edit", "Change the text already on the page like in Word, and add text, "
         "pictures, signatures, shapes, highlights and redaction.", page="edit"),
    Tool("watermark", "Watermark", "edit", "stamp", "Stamp text or your logo on the pages: position, angle, "
         "size, see-through, tiled.", preview=True),
    Tool("numbers", "Page numbers & header", "edit", "hash", "Page numbers (also বাংলা digits), header and "
         "footer text, mirrored for books.", preview=True),
    Tool("replace", "Find & replace text", "edit", "replace", "Replace a word or number everywhere in a PDF "
         "(a year, a name, a phone number), or remove it."),
    Tool("crop", "Crop pages", "edit", "crop", "Trim margins by millimetres, or cut away the white border "
         "automatically.", preview=True),
    Tool("resize", "Page size", "edit", "resize", "Fit every page onto A4, A5, Letter… with margins.",
         preview=True),
    Tool("nup", "Pages per sheet & booklet", "edit", "layout", "2, 4, 6, 8, 9 or 16 pages on one sheet, or a "
         "fold-in-half booklet for printing.", preview=True),
    Tool("protect", "Protect PDF", "security", "lock", "Lock with a password and choose whether it may be "
         "printed, copied or changed."),
    Tool("unlock", "Unlock PDF", "security", "unlock", "Remove the password (you need to know it) and the "
         "print/copy restrictions."),
    Tool("redact", "Auto-redact", "security", "redact", "Black out every email address, phone number, ID or word "
         "you list: really removed from the file, not just covered."),
    Tool("sanitize", "Clean hidden data", "security", "shield", "Remove scripts, attachments, links, comments, form "
         "data and properties that travel unseen inside a PDF."),
    Tool("metadata", "Title & properties", "security", "info", "Change the title, author, subject and "
         "keywords, or remove them all."),
    Tool("flatten", "Flatten PDF", "security", "layers", "Fix form fields, comments and stamps into the page "
         "so they can no longer be changed."),
)}

# ---------------------------------------------------------------------------------------------- params
def _get(p: dict, key: str, default: Any = "") -> Any:
    value = p.get(key, default)
    return default if value is None else value


def _int(p: dict, key: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(float(str(_get(p, key, default)).strip() or default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


def _float(p: dict, key: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(str(_get(p, key, default)).strip() or default)
    except (TypeError, ValueError):
        value = default
    if math.isnan(value):
        value = default
    return max(lo, min(hi, value))


def _bool(p: dict, key: str, default: bool = False) -> bool:
    value = p.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).lower() in ("1", "on", "true", "yes")


def _choice(p: dict, key: str, choices, default: str) -> str:
    value = str(_get(p, key, default))
    return value if value in choices else default


def _text(p: dict, key: str, limit: int = 500) -> str:
    return str(_get(p, key, ""))[:limit]


def color(value: str, default: tuple[float, float, float] = (0, 0, 0)) -> tuple[float, float, float]:
    """"#c00", "#cc0000" -> (0.8, 0, 0)."""
    value = str(value or "").strip().lstrip("#")
    if re.fullmatch(r"[0-9a-fA-F]{3}", value):
        value = "".join(c * 2 for c in value)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", value):
        return default
    return tuple(int(value[i:i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def _hex(rgb: tuple[float, float, float]) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c * 255))):02x}" for c in rgb)


def page_list(spec: str, count: int, *, empty_all: bool = True) -> list[int]:
    """"1-3, 7, 10-" -> 0-based page indexes in the order written (repeats kept).

    Also: "all", "odd", "even", "last", "5-last", "-3" (= 1-3), "8-5" (backwards) and Bengali digits.
    A page past the end is an error (a typo should not silently print the wrong pages)."""
    text = str(spec or "").translate(str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")).lower().strip()
    if not text or text == "all":
        if empty_all:
            return list(range(count))
        raise ToolError("Choose the pages (for example 1-3, 7).")
    pages: list[int] = []
    text = re.sub(r"\s*([-–—])\s*", r"\1", text)
    for part in re.split(r"[,;\s]+", text):
        part = part.strip()
        if not part:
            continue
        if part in ("all",):
            pages += range(count)
            continue
        if part in ("odd", "even"):
            pages += range(0 if part == "odd" else 1, count, 2)
            continue
        m = re.fullmatch(r"(\d+|last|end)?\s*(?:([-–—])\s*(\d+|last|end)?)?", part)
        if not m or not (m.group(1) or m.group(3)):
            raise ToolError(f"“{part}” is not a page or a range (write it like 1-3, 7, 10-last).")

        def number(token: str | None, fallback: int) -> int:
            if token is None:
                return fallback
            return count if token in ("last", "end") else int(token)

        start = number(m.group(1), 1)
        end = number(m.group(3), count) if m.group(2) else start
        for n in (start, end):
            if n < 1 or n > count:
                raise ToolError(f"Page {n} does not exist: the PDF has {count} page{'s' if count != 1 else ''}.")
        step = 1 if end >= start else -1
        pages += [n - 1 for n in range(start, end + step, step)]
        if len(pages) > MAX_PAGES * 2:
            raise ToolError("That is too many pages.")
    if not pages:
        raise ToolError("Choose the pages (for example 1-3, 7).")
    return pages


def page_set(spec: str, count: int) -> list[int]:
    """page_list sorted, without repeats."""
    return sorted(set(page_list(spec, count)))


def format_ranges(indexes) -> str:
    """{0, 2, 3, 4} -> "1, 3-5" (page numbers from 1)."""
    runs: list[list[int]] = []
    for n in sorted(set(indexes)):
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return ", ".join(str(a + 1) if a == b else f"{a + 1}-{b + 1}" for a, b in runs)


# ---------------------------------------------------------------------------------------------- helpers
def open_pdf(inp: Input, password: str = "") -> pymupdf.Document:
    if inp.kind == "image":
        raise ToolError(f"{inp.name} is a picture: turn it into a PDF first (Images to PDF).")
    try:
        doc = pymupdf.open(inp.path, filetype="pdf")
    except Exception as exc:  # mupdf raises several types for broken files
        raise ToolError(f"{inp.name} could not be opened as a PDF ({exc}). Try “Repair PDF”.") from exc
    if doc.needs_pass and not (password and doc.authenticate(password)):
        doc.close()
        raise ToolError(f"{inp.name} is locked with a password. Unlock it first with “Unlock PDF”.")
    if doc.page_count == 0:
        doc.close()
        raise ToolError(f"{inp.name} has no pages.")
    if doc.page_count > MAX_PAGES:
        doc.close()
        raise ToolError(f"{inp.name} has more than {MAX_PAGES} pages.")
    return doc


def save_pdf(doc: pymupdf.Document, path: Path, *, garbage: int = 3, **extra: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path, garbage=garbage, deflate=True, **extra)


def _name(stem: str, label: str, ext: str = ".pdf") -> str:
    stem = re.sub(r"[\\/:*?\"<>|\r\n]+", " ", stem).strip()[:90] or "file"
    return f"{stem} ({label}){ext}" if label else f"{stem}{ext}"


def _slug(text: str, limit: int = 60) -> str:
    return re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", text).strip()[:limit] or "part"


def _pdf_out(out_dir: Path, doc: pymupdf.Document, name: str, note: str = "", **save: Any) -> Output:
    path = out_dir / f"out-{len(list(out_dir.iterdir())) if out_dir.exists() else 0:04d}.pdf"
    save_pdf(doc, path, **save)
    return Output(path, name, note)


def _file_out(out_dir: Path, data: bytes, name: str, note: str = "") -> Output:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"out-{len(list(out_dir.iterdir())):04d}{Path(name).suffix.lower()}"
    path.write_bytes(data)
    return Output(path, name, note)


def _zip_out(out_dir: Path, files: list[tuple[str, bytes]], name: str, note: str = "") -> Output:
    buffer = io.BytesIO()
    seen: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        for fname, data in files:
            base, n = fname, 2
            while fname in seen:
                fname = f"{Path(base).stem} ({n}){Path(base).suffix}"
                n += 1
            seen.add(fname)
            z.writestr(fname, data)
    return _file_out(out_dir, buffer.getvalue(), name, note)


def _upright(page: pymupdf.Page) -> pymupdf.Page:
    """Drawing uses the page as seen: a rotated page is turned upright for real first (it looks the same)."""
    if page.rotation:
        page.remove_rotation()
    return page


def _size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


# Fonts for text drawn on pages (insert_htmlbox). A font file (a system font matched by edit_text) goes in through
# an Archive; scripts it lacks still print through MuPDF's built-in Noto fallback.
@dataclass
class FontKit:
    css: str
    archive: Any = None


def font_kit(family: str, font_file: Path | None = None) -> FontKit:
    stack = {"sans": "sans-serif", "serif": "serif", "mono": "monospace"}.get(family, "sans-serif")
    if font_file is not None:
        return FontKit(f"@font-face {{font-family: own; src: url({font_file.name});}}\n"
                       f"* {{font-family: own, {stack};}}", pymupdf.Archive(str(font_file.parent)))
    return FontKit(f"* {{font-family: {stack};}}")


def _html_text(text: str) -> str:
    return "<br>".join(html.escape(line) for line in str(text).split("\n"))


def draw_text(page: pymupdf.Page, rect: pymupdf.Rect, text: str, *, size: float = 12, rgb=(0, 0, 0),
              family: str = "sans", align: str = "left", bold: bool = False, italic: bool = False,
              underline: bool = False, strike: bool = False, opacity: float = 1.0, line_height: float = 1.2,
              background: str = "", overlay: bool = True, nowrap: bool = False, kit: FontKit | None = None,
              runs: list[tuple[str, tuple]] | None = None) -> float:
    """Text in a box (Bengali shaped); shrinks to fit. Returns the scale used (1 = as asked). `runs` gives parts
    of the text their own colour: [(text, rgb)] that join up to `text`."""
    kit = kit or font_kit(family)
    decoration = " ".join(d for d, on in (("underline", underline), ("line-through", strike)) if on) or "none"
    css = (kit.css + f"\nbody {{margin: 0; padding: 0; font-size: {size:.2f}px; color: {_hex(rgb)}; "
           f"text-align: {align if align in ('left', 'center', 'right', 'justify') else 'left'}; "
           f"line-height: {line_height}; font-weight: {'bold' if bold else 'normal'}; "
           f"font-style: {'italic' if italic else 'normal'}; "
           f"text-decoration: {decoration};"
           f"{' white-space: nowrap;' if nowrap else ''}"
           f"{f' background-color: {background};' if background else ''}}}")
    body = "".join(f'<span style="color: {_hex(rgb_)}">{_html_text(part)}</span>' for part, rgb_ in runs) \
        if runs else _html_text(text)
    spare, scale = page.insert_htmlbox(rect, body, css=css, archive=kit.archive, scale_low=0,
                                       opacity=max(0.0, min(1.0, opacity)), overlay=overlay)
    return scale if spare >= 0 else 0


# ---------------------------------------------------------------------------------------------- pictures
def image_frames(path: Path) -> list[tuple[bytes, int, int]]:
    """A picture file as (bytes for insert_image, width, height) per frame: JPG/PNG kept as they are unless the
    phone saved them sideways (EXIF), other formats converted to PNG, every page of a TIFF."""
    from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError

    try:
        source = Image.open(path)
    except (UnidentifiedImageError, OSError) as exc:
        raise ToolError(f"{path.name} is not a picture this app can read ({exc}).") from exc
    frames: list[tuple[bytes, int, int]] = []
    with source:
        fmt = (source.format or "").upper()
        many = fmt == "TIFF" and getattr(source, "n_frames", 1) > 1
        for frame in (ImageSequence.Iterator(source) if many else [source]):
            orientation = frame.getexif().get(0x0112, 1) if hasattr(frame, "getexif") else 1
            if not many and fmt in ("JPEG", "PNG") and orientation in (0, 1) and frame.mode in ("RGB", "L", "RGBA",
                                                                                                "P", "LA", "CMYK"):
                frames.append((path.read_bytes(), frame.width, frame.height))
                continue
            image = ImageOps.exif_transpose(frame.copy())
            if image.mode not in ("RGB", "RGBA", "L", "LA"):
                image = image.convert("RGBA" if "A" in image.mode or "transparency" in image.info else "RGB")
            out = io.BytesIO()
            if fmt == "JPEG" and image.mode in ("RGB", "L"):
                image.save(out, "JPEG", quality=92)
            else:
                image.save(out, "PNG", optimize=True)
            frames.append((out.getvalue(), image.width, image.height))
    if not frames:
        raise ToolError(f"{path.name} has no picture in it.")
    return frames


def _fit(box: pymupdf.Rect, w: float, h: float) -> pymupdf.Rect:
    """The largest w:h rectangle centred in box."""
    scale = min(box.width / max(w, 1e-6), box.height / max(h, 1e-6))
    rw, rh = w * scale, h * scale
    x0, y0 = box.x0 + (box.width - rw) / 2, box.y0 + (box.height - rh) / 2
    return pymupdf.Rect(x0, y0, x0 + rw, y0 + rh)


def _image_page(doc: pymupdf.Document, data: bytes, w: int, h: int, size: str = "A4", orientation: str = "auto",
                margin: float = 0.0) -> None:
    if size == "fit":
        pw, ph = w * 0.75, h * 0.75             # the picture's own size at 96 dpi
        if max(pw, ph) > 14400:                 # PDF pages stop at 200 inches
            k = 14400 / max(pw, ph)
            pw, ph = pw * k, ph * k
        pw, ph = pw + 2 * margin, ph + 2 * margin
    else:
        pw, ph = PAPER.get(size, PAPER["A4"])
        landscape = orientation == "landscape" or (orientation == "auto" and w > h)
        if landscape:
            pw, ph = ph, pw
    page = doc.new_page(width=pw, height=ph)
    box = pymupdf.Rect(margin, margin, pw - margin, ph - margin)
    page.insert_image(_fit(box, w, h), stream=data)


# ---------------------------------------------------------------------------------------------- organize
def merge(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    out = pymupdf.open()
    toc: list[list] = []
    bookmarks = _choice(p, "bookmarks", ("files", "keep", "none"), "files")
    odd_blank = _bool(p, "odd_blank")
    ranges = p.get("ranges") if isinstance(p.get("ranges"), list) else []
    image_size = _choice(p, "image_size", ("A4", "fit", "match"), "match")
    for i, inp in enumerate(inputs):
        progress(i, len(inputs), inp.name)
        start = out.page_count
        if inp.kind == "image":
            size = image_size
            if size == "match":                 # the size of the page before (A4 when the picture comes first)
                size = "A4"
            for data, w, h in image_frames(inp.path):
                _image_page(out, data, w, h, size, "auto", 0 if size == "fit" else 18)
        else:
            src = open_pdf(inp)
            spec = str(ranges[i]) if i < len(ranges) and ranges[i] else ""
            chosen = page_list(spec, src.page_count)
            if chosen == list(range(src.page_count)):
                out.insert_pdf(src)
                if bookmarks != "none":
                    for level, title, pno, *_ in src.get_toc(simple=True):
                        if pno >= 1:
                            toc.append([level + (1 if bookmarks == "files" else 0), title, start + pno])
            else:
                for a, b in _runs(chosen):
                    out.insert_pdf(src, from_page=a, to_page=b, start_at=-1)
            src.close()
        if bookmarks == "files" and out.page_count > start:
            toc.insert(_first_after(toc, start), [1, Path(inp.name).stem, start + 1])
        if odd_blank and (out.page_count - start) % 2 and i < len(inputs) - 1:
            last = out[-1].rect
            out.new_page(width=last.width, height=last.height)
    if out.page_count == 0:
        raise ToolError("Nothing to merge.")
    if toc:
        try:
            out.set_toc(_valid_toc(toc))
        except Exception:  # a broken bookmark tree in a source file: keep the PDF, drop the bookmarks
            pass
    progress(len(inputs), len(inputs), "saving")
    name = _name(inputs[0].stem, "merged") if len(inputs) > 1 else _name(inputs[0].stem, "")
    o = _pdf_out(out_dir, out, name)
    pages = out.page_count
    out.close()
    return Result([o], f"{len(inputs)} files joined: {pages} pages.", {"pages": pages})


def _first_after(toc: list[list], start: int) -> int:
    """Where the file bookmark goes: before the bookmarks of its own pages."""
    for index, entry in enumerate(toc):
        if entry[2] > start:
            return index
    return len(toc)


def _valid_toc(toc: list[list]) -> list[list]:
    """set_toc refuses a level that jumps by more than 1: flatten such jumps."""
    fixed: list[list] = []
    previous = 0
    for level, title, page in toc:
        level = max(1, min(level, previous + 1))
        fixed.append([level, str(title)[:200], page])
        previous = level
    return fixed


def _runs(indexes: list[int]) -> list[tuple[int, int]]:
    """[0, 1, 2, 5, 4] -> [(0, 2), (5, 5), (4, 4)] (ascending runs, order kept)."""
    runs: list[list[int]] = []
    for n in indexes:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return [(a, b) for a, b in runs]


def split(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    src = open_pdf(inp)
    count = src.page_count
    mode = _choice(p, "mode", ("ranges", "every", "each", "bookmarks"), "ranges")
    parts: list[tuple[str, list[int]]] = []
    if mode == "ranges":
        spec = _text(p, "ranges", 4000)
        chunks = [c for c in re.split(r"[;,\n]+", spec) if c.strip()]
        if not chunks:
            raise ToolError("Write the parts, for example 1-10, 11-25, 26-last.")
        for chunk in chunks:
            pages = page_list(chunk, count, empty_all=False)
            parts.append((f"pages {format_ranges(pages)}", pages))
    elif mode in ("every", "each"):
        n = 1 if mode == "each" else _int(p, "every", 10, 1, MAX_PAGES)
        for a in range(0, count, n):
            pages = list(range(a, min(a + n, count)))
            parts.append((f"page {a + 1}" if len(pages) == 1 else f"pages {a + 1}-{pages[-1] + 1}", pages))
    else:
        level = _int(p, "level", 1, 1, 6)
        marks = [(t, pno - 1) for lvl, t, pno, *_ in src.get_toc(simple=True) if lvl <= level and pno >= 1]
        if not marks:
            raise ToolError(f"{inp.name} has no bookmarks (chapters) to split by. Use page ranges instead.")
        marks.sort(key=lambda m: m[1])
        starts = sorted({m[1] for m in marks})
        if starts[0] > 0:
            parts.append(("start", list(range(0, starts[0]))))
        for i, s in enumerate(starts):
            end = starts[i + 1] if i + 1 < len(starts) else count
            title = " · ".join(t for t, pno in marks if pno == s)
            parts.append((_slug(title), list(range(s, end))))
    if len(parts) > MAX_OUTPUTS:
        raise ToolError(f"That makes {len(parts)} files; the most is {MAX_OUTPUTS}. Use bigger parts.")
    outputs: list[Output] = []
    for i, (label, pages) in enumerate(parts):
        progress(i, len(parts), label)
        doc = pymupdf.open()
        for a, b in _runs(pages):
            doc.insert_pdf(src, from_page=a, to_page=b)
        outputs.append(_pdf_out(out_dir, doc, _name(inp.stem, f"{i + 1:0{len(str(len(parts)))}d} {label}"),
                                f"{len(pages)} page{'s' if len(pages) != 1 else ''}"))
        doc.close()
    src.close()
    return Result(outputs, f"{inp.name} split into {len(outputs)} files.", {"files": len(outputs)})


def organize(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    """`plan`: the new document's pages in order, each {"f": input index, "p": page index, "r": extra rotation}
    or {"blank": true, "w": width, "h": height}."""
    plan = p.get("plan")
    if not isinstance(plan, list) or not plan:
        raise ToolError("The new PDF would have no pages.")
    if len(plan) > MAX_PAGES:
        raise ToolError(f"The most is {MAX_PAGES} pages.")
    docs = [open_pdf(i) for i in inputs]
    out = pymupdf.open()
    offsets = []
    for doc in docs:                          # every source once (shared fonts and pictures copied once)
        offsets.append(out.page_count)
        out.insert_pdf(doc)
    order: list[int] = []
    blanks: list[tuple[int, float, float]] = []
    rotations: list[int] = []
    for position, entry in enumerate(plan):
        if not isinstance(entry, dict):
            raise ToolError("The page list is not valid.")
        if entry.get("blank"):
            blanks.append((position, _float(entry, "w", 595.28, 72, 14400), _float(entry, "h", 841.89, 72, 14400)))
            continue
        f = _int(entry, "f", 0, 0, len(docs) - 1)
        page = _int(entry, "p", 0, 0, docs[f].page_count - 1)
        order.append(offsets[f] + page)
        rotations.append(_int(entry, "r", 0, -270, 360) // 90 * 90)
    if not order and not blanks:
        raise ToolError("The new PDF would have no pages.")
    if order:
        out.select(order)
        for index, turn in enumerate(rotations):
            if turn % 360:
                out[index].set_rotation((out[index].rotation + turn) % 360)
    else:
        out.close()
        out = pymupdf.open()
    for position, w, h in blanks:             # in increasing position: each lands where the plan put it
        out.new_page(pno=min(position, out.page_count), width=w, height=h)
    for doc in docs:
        doc.close()
    stem = inputs[0].stem
    o = _pdf_out(out_dir, out, _name(stem, "organized"), garbage=4)
    pages = out.page_count
    out.close()
    return Result([o], f"New PDF with {pages} pages.", {"pages": pages})


def remove(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    gone = page_set(_text(p, "pages", 4000), doc.page_count) if _text(p, "pages", 10).strip() else []
    if not gone:
        raise ToolError("Choose the pages to remove.")
    if len(gone) >= doc.page_count:
        raise ToolError("That would remove every page.")
    doc.delete_pages(gone)
    o = _pdf_out(out_dir, doc, _name(inp.stem, "pages removed"), garbage=4)
    left = doc.page_count
    doc.close()
    return Result([o], f"{len(gone)} page(s) removed ({format_ranges(gone)}); {left} left.", {"pages": left})


def extract(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    if not _text(p, "pages", 10).strip():
        raise ToolError("Choose the pages to take out.")
    pages = page_list(_text(p, "pages", 4000), doc.page_count)
    if _bool(p, "separate"):
        outputs = []
        for i, n in enumerate(pages):
            progress(i, len(pages), f"page {n + 1}")
            one = pymupdf.open()
            one.insert_pdf(doc, from_page=n, to_page=n)
            outputs.append(_pdf_out(out_dir, one, _name(inp.stem, f"page {n + 1}")))
            one.close()
        doc.close()
        return Result(outputs, f"{len(outputs)} pages saved as separate PDFs.", {"files": len(outputs)})
    doc.select(pages)
    o = _pdf_out(out_dir, doc, _name(inp.stem, f"pages {format_ranges(pages)}"[:60]), garbage=4)
    doc.close()
    return Result([o], f"{len(pages)} page(s) taken out.", {"pages": len(pages)})


def rotate(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    angle = _int(p, "angle", 90, -270, 270) // 90 * 90
    if not angle % 360:
        raise ToolError("Choose how far to turn the pages.")
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    if _choice(p, "only", ("all", "portrait", "landscape"), "all") != "all":
        want = p["only"]
        pages = [i for i in pages if (doc[i].rect.width > doc[i].rect.height) == (want == "landscape")]
        if not pages:
            raise ToolError(f"No {want} pages among the ones chosen.")
    for i in pages:
        doc[i].set_rotation((doc[i].rotation + angle) % 360)
    o = _pdf_out(out_dir, doc, _name(inp.stem, "rotated"))
    doc.close()
    return Result([o], f"{len(pages)} page(s) turned {angle % 360}°.", {"pages": len(pages)})


def insert(inputs: list[Input], p: dict, out_dir: Path, progress: Progress, extras: dict[str, Input]) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    count = doc.page_count
    where = _choice(p, "where", ("start", "end", "after", "every"), "end")
    after = _int(p, "after", count, 0, count)
    what = _choice(p, "what", ("blank", "file"), "blank")
    if what == "blank":
        n = _int(p, "count", 1, 1, 500)
        positions = ([0] if where == "start" else [count] if where == "end" else [after] if where == "after"
                     else list(range(_int(p, "step", 1, 1, count), count + 1, _int(p, "step", 1, 1, count))))
        for at in sorted(positions, reverse=True):
            ref = doc[min(max(at - 1, 0), doc.page_count - 1)].rect
            for _ in range(n):
                doc.new_page(pno=at, width=ref.width, height=ref.height)
        added = n * len(positions)
    else:
        source = extras.get("source_file")
        if source is None:
            raise ToolError("Choose the PDF or picture to insert.")
        at = 0 if where == "start" else count if where in ("end", "every") else after
        if source.kind == "image":
            tmp = pymupdf.open()
            ref = doc[min(max(at - 1, 0), count - 1)].rect
            size = "A4" if abs(ref.width - PAPER["A4"][0]) < 3 or abs(ref.height - PAPER["A4"][0]) < 3 else "fit"
            for data, w, h in image_frames(source.path):
                _image_page(tmp, data, w, h, size, "auto", 18 if size != "fit" else 0)
        else:
            tmp = open_pdf(source)
            chosen = page_list(_text(p, "source_pages", 4000), tmp.page_count)
            if chosen != list(range(tmp.page_count)):
                tmp.select(chosen)
        added = tmp.page_count
        doc.insert_pdf(tmp, start_at=at)
        tmp.close()
    o = _pdf_out(out_dir, doc, _name(inp.stem, "pages inserted"))
    pages = doc.page_count
    doc.close()
    return Result([o], f"{added} page(s) inserted; the PDF now has {pages} pages.", {"pages": pages})


# ---------------------------------------------------------------------------------------------- optimize
COMPRESS_LEVELS = {
    # dpi_threshold, dpi_target, jpeg quality
    "low": (None, 0, 0),           # no picture changed: only unused data and fonts
    "medium": (180, 150, 75),
    "high": (120, 100, 55),
    "extreme": (90, 72, 40),
}


def compress(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    before = _size(inp.path)
    doc = open_pdf(inp)
    level = _choice(p, "level", tuple(COMPRESS_LEVELS) + ("custom",), "medium")
    if level == "custom":
        target = _int(p, "dpi", 120, 36, 600)
        settings = (target + target // 5, target, _int(p, "quality", 65, 10, 95))
    else:
        settings = COMPRESS_LEVELS[level]
    grey = _bool(p, "grey")
    progress(0, 3, "pictures")
    if settings[0] is not None or grey:
        try:
            doc.rewrite_images(dpi_threshold=settings[0], dpi_target=settings[1] or 0, quality=settings[2] or 0,
                               lossy=True, lossless=True, bitonal=True, color=True, gray=True, set_to_gray=grey)
        except Exception as exc:  # an unusual picture filter: go on with the lossless steps
            settings = (None, 0, 0) if not grey else settings
            note = str(exc)
        else:
            note = ""
    else:
        note = ""
    progress(1, 3, "fonts")
    if _bool(p, "fonts", True):
        try:
            doc.subset_fonts()
        except Exception:  # fonts that cannot be subset stay as they are
            pass
    if _bool(p, "strip", False):
        doc.set_metadata({})
        doc.del_xml_metadata()
    try:
        doc.scrub(attached_files=False, clean_pages=False, embedded_files=False, hidden_text=False,
                  javascript=False, metadata=False, redactions=False, remove_links=False, reset_fields=False,
                  reset_responses=False, thumbnails=True, xml_metadata=False)
    except Exception:
        pass
    progress(2, 3, "saving")
    tmp = out_dir / "compress.tmp.pdf"
    save_pdf(doc, tmp, garbage=4, clean=True, deflate_images=True, deflate_fonts=True, use_objstms=1)
    doc.close()
    after = _size(tmp)
    if after >= before:
        tmp.unlink(missing_ok=True)
        raise ToolError(f"{inp.name} is already as small as this level makes it ({human_size(before)}). "
                        "Try a stronger level" + (" or tick “grey pictures”." if not grey else "."))
    final = out_dir / "out-0000.pdf"
    tmp.replace(final)
    saved = 100 * (before - after) / max(before, 1)
    summary = f"{human_size(before)} → {human_size(after)}: {saved:.0f}% smaller."
    if note:
        summary += " Some pictures could not be resampled and were kept."
    return Result([Output(final, _name(inp.stem, "compressed"), f"−{saved:.0f}%")], summary,
                  {"before": before, "after": after, "saved_pct": round(saved, 1)})


def repair(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    try:
        doc = pymupdf.open(inp.path, filetype="pdf")
    except Exception as exc:
        raise ToolError(f"{inp.name} is too damaged to read ({exc}).") from exc
    if doc.needs_pass:
        raise ToolError(f"{inp.name} is locked with a password. Unlock it first.")
    repaired = bool(getattr(doc, "is_repaired", False))
    o = _pdf_out(out_dir, doc, _name(inp.stem, "repaired"), garbage=4, clean=True)
    pages = doc.page_count
    doc.close()
    note = "Its broken parts were rebuilt." if repaired else "No damage was found; it was saved clean anyway."
    return Result([o], f"{pages} pages saved. {note}", {"pages": pages, "repaired": repaired})


# ---------------------------------------------------------------------------------------------- convert
def img2pdf(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    size = _choice(p, "size", tuple(PAPER) + ("fit",), "A4")
    orientation = _choice(p, "orientation", ("auto", "portrait", "landscape"), "auto")
    margin = {"none": 0, "small": 18, "big": 40}[_choice(p, "margin", ("none", "small", "big"), "small")]
    one = _choice(p, "merge", ("one", "each"), "one") == "one"
    outputs: list[Output] = []
    doc = pymupdf.open()
    for i, inp in enumerate(inputs):
        progress(i, len(inputs), inp.name)
        if inp.kind == "pdf":                         # a PDF among the pictures: its pages go in as they are
            src = open_pdf(inp)
            doc.insert_pdf(src)
            src.close()
        else:
            for data, w, h in image_frames(inp.path):
                _image_page(doc, data, w, h, size, orientation, margin)
        if not one:
            outputs.append(_pdf_out(out_dir, doc, _name(inp.stem, "")))
            doc.close()
            doc = pymupdf.open()
    if one:
        name = _name(inputs[0].stem, "") if len(inputs) == 1 else _name(inputs[0].stem, f"+{len(inputs) - 1} more")
        outputs.append(_pdf_out(out_dir, doc, name))
        pages = doc.page_count
    else:
        pages = len(outputs)
    doc.close()
    return Result(outputs, f"{len(inputs)} picture(s) → {'one PDF of ' + str(pages) + ' pages' if one else str(len(outputs)) + ' PDFs'}.",
                  {"pages": pages})


def pdf2img(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    fmt = _choice(p, "format", ("png", "jpg"), "png")
    dpi = _int(p, "dpi", 150, 36, 600)
    grey = _bool(p, "grey")
    quality = _int(p, "quality", 88, 30, 100)
    pages = page_list(_text(p, "pages", 4000), doc.page_count)
    files: list[tuple[str, bytes]] = []
    width = len(str(doc.page_count))
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = doc[i]
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if grey else pymupdf.csRGB, alpha=False)
        if pix.width * pix.height > 120_000_000:
            raise ToolError("That resolution makes pictures too big: choose a lower DPI.")
        data = pix.tobytes("jpg", jpg_quality=quality) if fmt == "jpg" else pix.tobytes("png")
        files.append((f"{inp.stem} - page {i + 1:0{width}d}.{fmt}", data))
    doc.close()
    if len(files) > SPLIT_FILES_LIMIT:
        o = _zip_out(out_dir, files, _name(inp.stem, f"{len(files)} {fmt.upper()} pages", ".zip"))
        return Result([o], f"{len(files)} pages as {fmt.upper()} at {dpi} dpi, in one ZIP.", {"files": len(files)})
    outputs = [_file_out(out_dir, data, name) for name, data in files]
    return Result(outputs, f"{len(files)} page(s) as {fmt.upper()} at {dpi} dpi.", {"files": len(files)})


def pdf2text(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    pages = page_list(_text(p, "pages", 4000), doc.page_count)
    marks = _bool(p, "page_marks", True)
    parts = []
    empty = 0
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        text = doc[i].get_text("text", sort=_bool(p, "sort", True)).strip()
        empty += not text
        parts.append((f"\n\n===== Page {i + 1} =====\n\n" if marks else "\n\n") + text)
    doc.close()
    body = "".join(parts).strip() + "\n"
    o = _file_out(out_dir, body.encode("utf-8"), _name(inp.stem, "", ".txt"))
    summary = f"Text of {len(pages)} page(s), {len(body):,} characters."
    if empty:
        summary += f" {empty} page(s) had no text (scans: run OCR first)."
    return Result([o], summary, {"chars": len(body)})


def extract_images(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    smallest = _int(p, "min_px", 80, 1, 4000)
    seen: set[int] = set()
    files: list[tuple[str, bytes]] = []
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        for n, img in enumerate(doc[i].get_images(full=True)):
            xref = img[0]
            if xref in seen:
                continue
            seen.add(xref)
            try:
                info = doc.extract_image(xref)
            except Exception:
                continue
            if not info or min(info.get("width", 0), info.get("height", 0)) < smallest:
                continue
            data, ext = info["image"], info.get("ext", "png")
            if img[1]:                                   # a soft mask (transparency): rebuild as PNG
                try:
                    pix = pymupdf.Pixmap(pymupdf.Pixmap(doc, xref), pymupdf.Pixmap(doc, img[1]))
                    data, ext = pix.tobytes("png"), "png"
                except Exception:
                    pass
            elif ext not in ("png", "jpg", "jpeg", "jpx", "bmp", "gif", "tiff"):
                try:
                    pix = pymupdf.Pixmap(doc, xref)
                    if pix.n - pix.alpha > 3:
                        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
                    data, ext = pix.tobytes("png"), "png"
                except Exception:
                    continue
            files.append((f"{inp.stem} - page {i + 1} - picture {n + 1}.{ext}", data))
            if len(files) > 5000:
                break
    doc.close()
    if not files:
        raise ToolError(f"No pictures of at least {smallest} pixels were found in {inp.name}.")
    if len(files) > SPLIT_FILES_LIMIT:
        o = _zip_out(out_dir, files, _name(inp.stem, f"{len(files)} pictures", ".zip"))
        return Result([o], f"{len(files)} pictures found, in one ZIP.", {"files": len(files)})
    return Result([_file_out(out_dir, d, n) for n, d in files], f"{len(files)} picture(s) found.",
                  {"files": len(files)})


# ---------------------------------------------------------------------------------------------- stamps
def _spot(box: pymupdf.Rect, w: float, h: float, position: str, margin: float) -> pymupdf.Rect:
    """A w×h rectangle at one of the nine spots of box (tl … br), margin from the edges."""
    col = {"l": 0, "c": 1, "r": 2}[position[-1] if position != "c" else "c"]
    row = {"t": 0, "m": 1, "c": 1, "b": 2}[position[0]]
    x = (box.x0 + margin, box.x0 + (box.width - w) / 2, box.x1 - margin - w)[col]
    y = (box.y0 + margin, box.y0 + (box.height - h) / 2, box.y1 - margin - h)[row]
    return pymupdf.Rect(x, y, x + w, y + h)


def _rotated_size(w: float, h: float, angle: float) -> tuple[float, float]:
    a = math.radians(angle)
    return abs(w * math.cos(a)) + abs(h * math.sin(a)), abs(w * math.sin(a)) + abs(h * math.cos(a))


def _text_stamp(text: str, size: float, rgb, family: str, bold: bool, opacity: float) -> pymupdf.Document:
    """A one-page PDF holding just the text (its page cropped to the text), to place at any angle."""
    stamp = pymupdf.open()
    longest = max(len(line) for line in text.split("\n"))
    page = stamp.new_page(width=max(200, size * longest * 1.1 + 40), height=size * 1.6 * (text.count("\n") + 1) + 20)
    draw_text(page, page.rect, text, size=size, rgb=rgb, family=family, align="center", bold=bold,
              opacity=opacity, nowrap=True)
    box = pymupdf.Rect()
    for block in page.get_text("dict")["blocks"]:
        box |= pymupdf.Rect(block["bbox"])
    if box.is_empty:
        raise ToolError("The watermark text is empty.")
    page.set_cropbox((box + (-2, -2, 2, 2)) & page.rect)
    return stamp


def logo_png(path: Path, opacity: int, grey: bool, pixels: int = 1400) -> tuple[bytes, int, int]:
    """A logo as a see-through PNG (opacity and grey applied), with its pixel size. A JPG's white background (or a
    PNG without transparency) is made see-through, or it would print as a pale box over the text."""
    from PIL import Image, ImageChops

    if path.suffix.lower() == ".pdf":
        with pymupdf.open(path) as doc:
            page = doc[0]
            zoom = pixels / max(page.rect.width, page.rect.height, 1)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True)
            image = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
            see_through = True
    else:
        with Image.open(path) as source:
            source.load()
            see_through = source.mode in ("RGBA", "LA", "PA") or "transparency" in source.info
            image = source.convert("RGBA")
    image.thumbnail((pixels, pixels))
    red, green, blue, alpha = image.split()
    if not see_through:
        lightest = ImageChops.darker(ImageChops.darker(red, green), blue)
        alpha = lightest.point(lambda v: 0 if v >= 245 else min(255, (245 - v) * 6))
    if grey:
        red = green = blue = image.convert("L")
    alpha = alpha.point(lambda v: v * opacity // 100)
    out = io.BytesIO()
    Image.merge("RGBA", (red, green, blue, alpha)).save(out, "PNG", optimize=True)
    return out.getvalue(), image.width, image.height


def _image_stamp(path: Path, opacity: int, grey: bool) -> pymupdf.Document:
    png, w, h = logo_png(path, max(1, min(100, opacity)), grey)
    stamp = pymupdf.open()
    page = stamp.new_page(width=w * 0.5, height=h * 0.5)
    page.insert_image(page.rect, stream=png)
    return stamp


def _place_stamp(page: pymupdf.Page, stamp: pymupdf.Document, *, width: float, angle: float, position: str,
                 margin: float, tiled: bool, rows: int, cols: int, overlay: bool) -> None:
    sw, sh = stamp[0].rect.width, stamp[0].rect.height
    w, h = width, width * sh / max(sw, 1e-6)
    bw, bh = _rotated_size(w, h, angle)
    box = page.rect
    if tiled:
        cw, ch = box.width / cols, box.height / rows
        k = min(1.0, cw * 0.9 / max(bw, 1e-6), ch * 0.9 / max(bh, 1e-6))
        rects = [pymupdf.Rect(box.x0 + c * cw + (cw - bw * k) / 2, box.y0 + r * ch + (ch - bh * k) / 2,
                              box.x0 + c * cw + (cw + bw * k) / 2, box.y0 + r * ch + (ch + bh * k) / 2)
                 for r in range(rows) for c in range(cols)]
    else:
        k = min(1.0, (box.width - 2 * margin) / max(bw, 1e-6), (box.height - 2 * margin) / max(bh, 1e-6))
        rects = [_spot(box, bw * k, bh * k, position, margin)]
    for rect in rects:
        page.show_pdf_page(rect, stamp, 0, overlay=overlay, rotate=angle, keep_proportion=True)


def apply_watermark(doc: pymupdf.Document, p: dict, extras: dict[str, Input], pages: list[int],
                    progress: Progress) -> int:
    kind = _choice(p, "kind", ("text", "image"), "text")
    opacity = _int(p, "opacity", 20, 1, 100)
    angle = _float(p, "angle", 45 if kind == "text" else 0, -360, 360)
    if kind == "text":
        text = _text(p, "text", 300).strip()
        if not text:
            raise ToolError("Write the watermark text.")
        stamp = _text_stamp(text, _float(p, "size", 60, 6, 300), color(_text(p, "color"), (0.6, 0.6, 0.6)),
                            _choice(p, "font", FONTS, "sans"), _bool(p, "bold", True), opacity / 100)
    else:
        image = extras.get("image_file")
        if image is None:
            raise ToolError("Choose the logo or picture for the watermark.")
        stamp = _image_stamp(image.path, opacity, _bool(p, "grey"))
    tiled = _choice(p, "position", POSITIONS + ("tiled",), "c") == "tiled"
    rows, cols = _int(p, "rows", 3, 1, 8), _int(p, "cols", 2, 1, 8)
    scale = _int(p, "scale", 50, 5, 100)
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = _upright(doc[i])
        if kind == "text":
            width = stamp[0].rect.width                 # text keeps its point size
        else:
            width = page.rect.width * scale / 100
        _place_stamp(page, stamp, width=width, angle=angle, position=_choice(p, "position", POSITIONS, "c"),
                     margin=_float(p, "margin", 12, 0, 100) * MM, tiled=tiled, rows=rows, cols=cols,
                     overlay=_choice(p, "layer", ("over", "under"), "over") == "over")
    stamp.close()
    return len(pages)


def watermark(inputs: list[Input], p: dict, out_dir: Path, progress: Progress, extras: dict[str, Input]) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    pages = _stamp_pages(doc, p)
    n = apply_watermark(doc, p, extras, pages, progress)
    o = _pdf_out(out_dir, doc, _name(inp.stem, "watermarked"))
    doc.close()
    return Result([o], f"Watermark on {n} page(s).", {"pages": n})


def _stamp_pages(doc: pymupdf.Document, p: dict) -> list[int]:
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    skip = set(page_set(_text(p, "skip", 4000), doc.page_count)) if _text(p, "skip", 10).strip() else set()
    chosen = [i for i in pages if i not in skip]
    if not chosen:
        raise ToolError("No pages left to stamp.")
    return chosen


NUMBER_FORMATS = {"n": "{n}", "n_of": "{n} / {total}", "page_n": "Page {n}", "page_n_of": "Page {n} of {total}",
                  "bn_n": "পৃষ্ঠা {n}", "bn_n_of": "পৃষ্ঠা {n} / {total}", "dash": "– {n} –", "custom": ""}


def _fill(template: str, n: int, total: int, file: str, digits: str) -> str:
    def num(v: int) -> str:
        return str(v).translate(BN_DIGITS) if digits == "bengali" else str(v)
    return (template.replace("{n}", num(n)).replace("{total}", num(total)).replace("{file}", file)
            .replace("{date}", date.today().strftime("%d %b %Y")))


def apply_numbers(doc: pymupdf.Document, p: dict, pages: list[int], progress: Progress, *, file: str = "",
                  total: int | None = None, offset: int = 0) -> int:
    """Page numbers + header + footer. `offset`/`total` let a one-page preview number like the whole PDF."""
    position = _choice(p, "position", ("bl", "bc", "br", "tl", "tc", "tr", "none"), "bc")
    fmt = _choice(p, "format", NUMBER_FORMATS, "n")
    template = _text(p, "custom", 120) if fmt == "custom" else NUMBER_FORMATS[fmt]
    if fmt == "custom" and "{n}" not in template and position != "none":
        raise ToolError("Put {n} in your own format where the number goes (for example “My Notes · {n}”).")
    digits = _choice(p, "digits", ("latin", "bengali"), "latin")
    first = _int(p, "first", 1, 1, MAX_PAGES) - 1             # numbering starts on this page…
    start = _int(p, "start", 1, -9999, 99999)                 # …with this number
    size = _float(p, "size", 11, 5, 72)
    rgb = color(_text(p, "color"), (0.2, 0.2, 0.2))
    kit = font_kit(_choice(p, "font", FONTS, "sans"))
    margin = _float(p, "margin", 10, 2, 60) * MM
    mirror = _bool(p, "mirror")
    header, footer = _text(p, "header", 300).strip(), _text(p, "footer", 300).strip()
    header_align = _choice(p, "header_align", ("left", "center", "right"), "center")
    footer_align = _choice(p, "footer_align", ("left", "center", "right"), "center")
    total_pages = total or doc.page_count
    numbered_total = total_pages - first + start - 1
    bold = _bool(p, "bold")
    line = _bool(p, "line")
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = _upright(doc[i])
        real = i + offset
        box = page.rect
        height = size * 1.7
        top = pymupdf.Rect(margin, margin, box.width - margin, margin + height)
        bottom = pymupdf.Rect(margin, box.height - margin - height, box.width - margin, box.height - margin)
        even = (real + 1) % 2 == 0

        def flip(align: str) -> str:
            return {"left": "right", "right": "left"}.get(align, align) if mirror and even else align

        slots: dict[tuple[str, str], list[str]] = {}
        if position != "none" and real >= first:
            text = _fill(template, real - first + start, numbered_total, file, digits)
            slots.setdefault((position[0], flip({"l": "left", "c": "center", "r": "right"}[position[1]])), []).append(text)
        if header:
            slots.setdefault(("t", flip(header_align)), []).append(_fill(header, real - first + start, numbered_total,
                                                                         file, digits))
        if footer:
            slots.setdefault(("b", flip(footer_align)), []).append(_fill(footer, real - first + start, numbered_total,
                                                                         file, digits))
        for (edge, align), texts in slots.items():
            draw_text(page, top if edge == "t" else bottom, "  ·  ".join(texts), size=size, rgb=rgb, align=align,
                      bold=bold, kit=kit, line_height=1.3)
        if line and header:
            page.draw_line((margin, top.y1 + 2), (box.width - margin, top.y1 + 2), color=rgb, width=0.5)
        if line and footer:
            page.draw_line((margin, bottom.y0 - 2), (box.width - margin, bottom.y0 - 2), color=rgb, width=0.5)
    return len(pages)


def numbers(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    pages = _stamp_pages(doc, p)
    n = apply_numbers(doc, p, pages, progress, file=inp.stem)
    o = _pdf_out(out_dir, doc, _name(inp.stem, "numbered"))
    doc.close()
    return Result([o], f"Numbers and text on {n} page(s).", {"pages": n})


def replace(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    find = _text(p, "find", 200)
    if not find.strip():
        raise ToolError("Write the text to find.")
    new = _text(p, "replace", 300)
    case = _bool(p, "case")
    family = _choice(p, "font", tuple(FONTS) + ("match",), "match")
    kits = {f: font_kit(f) for f in (("sans", "serif") if family == "match" else (family,))}
    doc = open_pdf(inp)
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    total = 0
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = _upright(doc[i])
        hits = page.search_for(find)
        if case:
            hits = [r for r in hits if find in page.get_textbox(r + (-1, -1, 1, 1))]
        if not hits:
            continue
        spans = [s for b in page.get_text("dict")["blocks"] for line in b.get("lines", []) for s in line["spans"]]
        looks = []
        for rect in hits:
            span = next((s for s in spans if pymupdf.Rect(s["bbox"]).intersects(rect)), None)
            size = span["size"] if span else rect.height * 0.8
            rgb = pymupdf.sRGB_to_pdf(span["color"]) if span else (0, 0, 0)
            looks.append((rect, size, rgb, bool(span and span["flags"] & 16),
                          family if family != "match" else ("serif" if span and _serif(span["font"]) else "sans")))
            shrink = rect.height * 0.18
            page.add_redact_annot(pymupdf.Rect(rect.x0, rect.y0 + shrink, rect.x1, rect.y1 - shrink), fill=False)
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)
        if new:
            for rect, size, rgb, bold, fam in looks:
                # in the old word's place: a longer replacement prints smaller rather than over the next word,
                # on the same bottom line
                wide = pymupdf.get_text_length(new, fontname="hebo" if bold else "helv", fontsize=size)
                k = min(1.0, rect.width * 0.98 / max(wide, 1e-6))
                top = rect.y1 - rect.height * k
                room = pymupdf.Rect(rect.x0, top, min(page.rect.x1, rect.x0 + max(rect.width, wide * k) + size),
                                    rect.y1 + rect.height)
                draw_text(page, room, new, size=size * k, rgb=rgb, bold=bold, kit=kits[fam], line_height=1.0,
                          nowrap=True)
        total += len(hits)
    if not total:
        doc.close()
        raise ToolError(f"“{find}” was not found. (Bengali in typeset PDFs is often stored broken, so it may not "
                        "be found; English words and numbers work best.)")
    o = _pdf_out(out_dir, doc, _name(inp.stem, "replaced" if new else "text removed"))
    doc.close()
    return Result([o], f"{total} place(s) changed: “{find}” → “{new}”." if new else f"“{find}” removed from {total} "
                  "place(s).", {"count": total})


def _serif(font: str) -> bool:
    name = font.lower()
    return "sans" not in name and any(k in name for k in ("serif", "times", "roman", "termes", "pagella", "schola",
                                                          "bonum", "georgia", "garamond", "minion", "cambria", "cmr"))


# ---------------------------------------------------------------------------------------------- geometry
def _visible_to_cropbox(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """A rectangle of the page as seen -> the CropBox to set (unrotated, from the MediaBox's top left)."""
    unrotated = rect * page.derotation_matrix
    unrotated.normalize()
    return unrotated + (page.cropbox.x0, page.cropbox.y0, page.cropbox.x0, page.cropbox.y0)


def content_box(page: pymupdf.Page, threshold: int = 245) -> pymupdf.Rect | None:
    """Where anything non-white is on the page (as seen), from a small greyscale render."""
    from PIL import Image

    zoom = 60 / 72
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    image = Image.frombytes("L", (pix.width, pix.height), pix.samples)
    bbox = image.point(lambda v: 255 if v < threshold else 0).getbbox()
    if not bbox:
        return None
    return pymupdf.Rect(bbox[0] / zoom, bbox[1] / zoom, bbox[2] / zoom, bbox[3] / zoom) & page.rect


def apply_crop(doc: pymupdf.Document, p: dict, pages: list[int], progress: Progress) -> int:
    mode = _choice(p, "mode", ("margins", "auto"), "margins")
    t, r, b, l = (_float(p, k, 0, 0, 500) * MM for k in ("top", "right", "bottom", "left"))
    pad = _float(p, "pad", 5, 0, 100) * MM
    changed = 0
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = doc[i]
        box = page.rect
        if mode == "auto":
            found = content_box(page)
            if found is None:
                continue
            seen = (found + (-pad, -pad, pad, pad)) & box
        else:
            seen = pymupdf.Rect(box.x0 + l, box.y0 + t, box.x1 - r, box.y1 - b)
        if seen.width < 36 or seen.height < 36:
            raise ToolError(f"That would leave almost nothing of page {i + 1}.")
        if seen == box:
            continue
        target = _visible_to_cropbox(page, seen) & page.mediabox
        page.set_cropbox(target)
        changed += 1
    return changed


def crop(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    n = apply_crop(doc, p, pages, progress)
    if not n:
        doc.close()
        raise ToolError("Nothing to cut: give the margins in mm, or the pages have no white border.")
    o = _pdf_out(out_dir, doc, _name(inp.stem, "cropped"))
    doc.close()
    return Result([o], f"{n} page(s) cropped.", {"pages": n})


def _target_size(p: dict) -> tuple[float, float] | None:
    size = _choice(p, "size", tuple(PAPER) + ("custom",), "A4")
    if size == "custom":
        return _float(p, "width", 210, 20, 2000) * MM, _float(p, "height", 297, 20, 2000) * MM
    return PAPER[size]


def resize_doc(src: pymupdf.Document, p: dict, pages: list[int], progress: Progress) -> pymupdf.Document:
    pw, ph = _target_size(p)  # type: ignore[misc]
    orientation = _choice(p, "orientation", ("auto", "portrait", "landscape"), "auto")
    margin = _float(p, "margin", 0, 0, 100) * MM
    out = pymupdf.open()
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        r = src[i].rect
        landscape = r.width > r.height if orientation == "auto" else orientation == "landscape"
        w, h = (max(pw, ph), min(pw, ph)) if landscape else (min(pw, ph), max(pw, ph))
        page = out.new_page(width=w, height=h)
        page.show_pdf_page(pymupdf.Rect(margin, margin, w - margin, h - margin), src, i)
    return out


def resize(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    src = open_pdf(inp)
    out = resize_doc(src, p, list(range(src.page_count)), progress)
    label = p.get("size") if p.get("size") in PAPER else "resized"
    o = _pdf_out(out_dir, out, _name(inp.stem, str(label)), garbage=4)
    n = out.page_count
    out.close()
    src.close()
    return Result([o], f"{n} page(s) fitted onto {label}.", {"pages": n})


NUP_GRIDS = {2: (2, 1), 4: (2, 2), 6: (2, 3), 8: (4, 2), 9: (3, 3), 16: (4, 4)}   # cols, rows on the sheet as used


def nup_doc(src: pymupdf.Document, p: dict, pages: list[int], progress: Progress) -> pymupdf.Document:
    mode = _choice(p, "mode", ("grid", "booklet"), "grid")
    pw, ph = PAPER[_choice(p, "sheet", tuple(PAPER), "A4")]
    margin = _float(p, "margin", 8, 0, 60) * MM
    gap = _float(p, "gap", 4, 0, 60) * MM
    borders = _bool(p, "borders")
    first = src[pages[0]].rect
    portrait_pages = first.height >= first.width
    out = pymupdf.open()
    if mode == "booklet":
        slots: list[int | None] = list(pages) + [None] * (-len(pages) % 4)
        n = len(slots)
        order: list[tuple[int | None, int | None]] = []
        for s in range(n // 4):
            order.append((slots[n - 1 - 2 * s], slots[2 * s]))          # front: last … first
            order.append((slots[2 * s + 1], slots[n - 2 - 2 * s]))      # back
        w, h = (max(pw, ph), min(pw, ph)) if portrait_pages else (min(pw, ph), max(pw, ph))
        for k, (a, b) in enumerate(order):
            progress(k, len(order), f"sheet side {k + 1}")
            sheet = out.new_page(width=w, height=h)
            if portrait_pages:
                cells = [pymupdf.Rect(margin, margin, w / 2 - gap / 2, h - margin),
                         pymupdf.Rect(w / 2 + gap / 2, margin, w - margin, h - margin)]
            else:
                cells = [pymupdf.Rect(margin, margin, w - margin, h / 2 - gap / 2),
                         pymupdf.Rect(margin, h / 2 + gap / 2, w - margin, h - margin)]
            for cell, index in zip(cells, (a, b)):
                if index is not None:
                    sheet.show_pdf_page(cell, src, index)
                if borders:
                    sheet.draw_rect(cell, color=(0.75, 0.75, 0.75), width=0.4)
        return out
    per = _int(p, "per_sheet", 2, 2, 16)
    if per not in NUP_GRIDS:
        per = 2
    cols, rows = NUP_GRIDS[per]
    landscape_sheet = cols > rows
    if not portrait_pages:
        cols, rows = rows, cols
        landscape_sheet = not landscape_sheet
    w, h = (max(pw, ph), min(pw, ph)) if landscape_sheet else (min(pw, ph), max(pw, ph))
    by_column = _choice(p, "order", ("rows", "columns"), "rows") == "columns"
    cw = (w - 2 * margin - (cols - 1) * gap) / cols
    ch = (h - 2 * margin - (rows - 1) * gap) / rows
    sheets = math.ceil(len(pages) / per)
    for s in range(sheets):
        progress(s, sheets, f"sheet {s + 1}")
        sheet = out.new_page(width=w, height=h)
        for slot in range(per):
            k = s * per + slot
            if k >= len(pages):
                break
            c, r = (slot // rows, slot % rows) if by_column else (slot % cols, slot // cols)
            cell = pymupdf.Rect(margin + c * (cw + gap), margin + r * (ch + gap),
                                margin + c * (cw + gap) + cw, margin + r * (ch + gap) + ch)
            sheet.show_pdf_page(cell, src, pages[k])
            if borders:
                sheet.draw_rect(cell, color=(0.75, 0.75, 0.75), width=0.4)
    return out


def nup(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    src = open_pdf(inp)
    pages = page_list(_text(p, "pages", 4000), src.page_count)
    out = nup_doc(src, p, pages, progress)
    booklet = p.get("mode") == "booklet"
    o = _pdf_out(out_dir, out, _name(inp.stem, "booklet" if booklet else f"{p.get('per_sheet', 2)} per sheet"),
                 garbage=4)
    n = out.page_count
    out.close()
    src.close()
    if booklet:
        return Result([o], f"Booklet of {n // 2} sheet(s): print double-sided, flip on the short edge, fold in half.",
                      {"pages": n})
    return Result([o], f"{len(pages)} pages on {n} sheet(s).", {"pages": n})


# ---------------------------------------------------------------------------------------------- security
PERMISSIONS = {"print": ("Print", pymupdf.PDF_PERM_PRINT | pymupdf.PDF_PERM_PRINT_HQ),
               "copy": ("Copy text and pictures", pymupdf.PDF_PERM_COPY),
               "edit": ("Change pages and content", pymupdf.PDF_PERM_MODIFY | pymupdf.PDF_PERM_ASSEMBLE),
               "annotate": ("Add comments and fill forms", pymupdf.PDF_PERM_ANNOTATE | pymupdf.PDF_PERM_FORM)}


def protect(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    user = str(p.get("password", ""))[:128]
    owner = str(p.get("owner_password", ""))[:128]
    allowed = [k for k in PERMISSIONS if _bool(p, f"allow_{k}", k == "print")]
    if not user and not owner:
        raise ToolError("Give a password to open the PDF, or a permissions password (or both).")
    if user and len(user) < 4:
        raise ToolError("Use a password of at least 4 characters.")
    if not owner:
        import secrets
        owner = secrets.token_urlsafe(18)          # a strong one nobody knows: the limits cannot be lifted
    perms = pymupdf.PDF_PERM_ACCESSIBILITY
    for k in allowed:
        perms |= PERMISSIONS[k][1]
    doc = open_pdf(inp)
    o = _pdf_out(out_dir, doc, _name(inp.stem, "protected"), encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 user_pw=user, owner_pw=owner, permissions=perms)
    doc.close()
    allowed_text = ", ".join(PERMISSIONS[k][0].lower() for k in allowed) or "nothing"
    head = "Opens only with the password" if user else "Opens without a password"
    return Result([o], f"{head} (AES-256). Allowed: {allowed_text}.", {})


def unlock(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    password = str(p.get("password", ""))[:128]
    try:
        doc = pymupdf.open(inp.path, filetype="pdf")
    except Exception as exc:
        raise ToolError(f"{inp.name} could not be opened ({exc}).") from exc
    if doc.needs_pass and not doc.authenticate(password):
        doc.close()
        raise ToolError("Wrong password." if password else "This PDF needs its password to open: type it in.")
    if not doc.is_encrypted and not doc.needs_pass:
        doc.close()
        raise ToolError(f"{inp.name} has no password or restrictions.")
    o = _pdf_out(out_dir, doc, _name(inp.stem, "unlocked"), encryption=pymupdf.PDF_ENCRYPT_NONE)
    doc.close()
    return Result([o], "Password and restrictions removed.", {})


META_KEYS = ("title", "author", "subject", "keywords", "creator", "producer")


def metadata(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    if _bool(p, "clear"):
        doc.set_metadata({})
        doc.del_xml_metadata()
        summary = "Every property removed."
    else:
        meta = dict(doc.metadata or {})
        for key in META_KEYS:
            if key in p:
                meta[key] = _text(p, key, 500)
        doc.set_metadata({k: v for k, v in meta.items() if k in META_KEYS + ("creationDate", "modDate")})
        summary = f"Properties saved (title: {meta.get('title') or '—'})."
    o = _pdf_out(out_dir, doc, _name(inp.stem, ""))
    doc.close()
    return Result([o], summary, {})


def flatten(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    annots = sum(1 for page in doc for _ in page.annots())
    widgets = sum(1 for page in doc for _ in page.widgets())
    doc.bake(annots=_bool(p, "annots", True), widgets=_bool(p, "widgets", True))
    o = _pdf_out(out_dir, doc, _name(inp.stem, "flattened"))
    doc.close()
    return Result([o], f"{annots} comment(s)/stamp(s) and {widgets} form field(s) fixed into the pages.",
                  {"annots": annots, "widgets": widgets})


# ---------------------------------------------------------------------------------------------- privacy
REDACT_PATTERNS = {
    "email": ("Email addresses", r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"),
    "phone": ("Phone numbers", r"(?<![\w০-৯])(?:\+|00)?[0-9০-৯][0-9০-৯ ()\-]{7,}[0-9০-৯](?![\w০-৯])"),
    "url": ("Web addresses", r"(?:https?://|www\.)[^\s<>\"]+"),
    "id": ("Long numbers (ID, account, card)", r"(?<![\w০-৯])[0-9০-৯](?:[0-9০-৯ \-]{8,})[0-9০-৯](?![\w০-৯])"),
    "date": ("Dates", r"(?<![\w০-৯])[0-9০-৯]{1,2}[/.\-][0-9০-৯]{1,2}[/.\-][0-9০-৯]{2,4}(?![\w০-৯])"),
}


def redact(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    """Find text by kind (emails, phones…), by the words listed, or by a pattern, and black it out for real."""
    inp = inputs[0]
    patterns: list[re.Pattern] = []
    kinds = p.get("find") if isinstance(p.get("find"), list) else []
    for key in kinds:
        if key in REDACT_PATTERNS:
            patterns.append(re.compile(REDACT_PATTERNS[key][1]))
    case = _bool(p, "case")
    words = [w.strip() for w in _text(p, "words", 4000).splitlines() if w.strip()]
    custom = _text(p, "pattern", 300).strip()
    if custom:
        try:
            patterns.append(re.compile(custom, 0 if case else re.I))
        except re.error as exc:
            raise ToolError(f"The pattern is not valid: {exc}.") from exc
    if not patterns and not words:
        raise ToolError("Tick what to find (emails, phone numbers…) or list the words to black out.")
    mark_only = _choice(p, "mode", ("apply", "mark"), "apply") == "mark"
    fill = color(_text(p, "fill"), (0, 0, 0))
    doc = open_pdf(inp)
    pages = page_set(_text(p, "pages", 4000), doc.page_count)
    total, found_on = 0, 0
    for k, i in enumerate(pages):
        progress(k, len(pages), f"page {i + 1}")
        page = _upright(doc[i])
        text = page.get_text("text")
        needles = set(words)
        for pattern in patterns:
            needles.update(m.group(0).strip() for m in pattern.finditer(text) if m.group(0).strip())
        rects: list[pymupdf.Rect] = []
        for needle in sorted(needles, key=len, reverse=True)[:2000]:
            for rect in page.search_for(needle):
                if case and needle in words and needle not in page.get_textbox(rect + (-1, -1, 1, 1)):
                    continue
                rects.append(rect)
        if not rects:
            continue
        found_on += 1
        total += len(rects)
        for rect in rects:
            if mark_only:
                page.draw_rect(rect + (-1, -1, 1, 1), color=(0.86, 0.15, 0.15), width=1.2)
            else:
                page.add_redact_annot(rect + (-0.5, -0.5, 0.5, 0.5), fill=fill)
        if not mark_only:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=getattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 1))
    if not total:
        doc.close()
        raise ToolError("Nothing matching was found. Scanned pages have no text: run OCR first.")
    if not mark_only:
        doc.set_metadata({})
        doc.del_xml_metadata()
    o = _pdf_out(out_dir, doc, _name(inp.stem, "marked for checking" if mark_only else "redacted"), garbage=4,
                 clean=True)
    doc.close()
    summary = (f"{total} place(s) on {found_on} page(s) outlined in red, nothing removed yet." if mark_only else
               f"{total} place(s) on {found_on} page(s) blacked out and removed from the file.")
    return Result([o], summary, {"count": total})


SANITIZE = {"javascript": "Scripts (JavaScript)", "files": "Attached and embedded files", "metadata": "Title, author "
            "and other properties", "forms": "Filled-in form data", "comments": "Comments, notes and highlights",
            "links": "Links", "thumbnails": "Page thumbnails", "hidden_text": "Hidden text (also an OCR text layer)"}


def sanitize(inputs: list[Input], p: dict, out_dir: Path, progress: Progress) -> Result:
    inp = inputs[0]
    on = {k: _bool(p, k, k not in ("links", "comments", "hidden_text")) for k in SANITIZE}
    if not any(on.values()):
        raise ToolError("Tick at least one thing to remove.")
    doc = open_pdf(inp)
    progress(0, 2, "cleaning")
    removed_comments = 0
    if on["comments"]:
        for page in doc:
            for annot in list(page.annots() or []):
                if annot.type[0] not in (pymupdf.PDF_ANNOT_LINK, pymupdf.PDF_ANNOT_WIDGET):
                    page.delete_annot(annot)
                    removed_comments += 1
    try:
        doc.scrub(attached_files=on["files"], clean_pages=True, embedded_files=on["files"],
                  hidden_text=on["hidden_text"], javascript=on["javascript"], metadata=on["metadata"],
                  redactions=False, remove_links=on["links"], reset_fields=on["forms"], reset_responses=on["comments"],
                  thumbnails=on["thumbnails"], xml_metadata=on["metadata"])
    except Exception as exc:  # an unusual file: report rather than half-clean it
        doc.close()
        raise ToolError(f"{inp.name} could not be cleaned ({exc}).") from exc
    progress(1, 2, "saving")
    o = _pdf_out(out_dir, doc, _name(inp.stem, "cleaned"), garbage=4, clean=True)
    doc.close()
    done = [SANITIZE[k].lower() for k, v in on.items() if v]
    return Result([o], "Removed: " + ", ".join(done) + ".", {"comments": removed_comments})


# ---------------------------------------------------------------------------------------------- edit
EDIT_TYPES = ("textedit", "text", "image", "rect", "roundrect", "ellipse", "triangle", "diamond", "star", "line",
              "arrow", "arrow2", "pen", "pencil", "marker", "whitener", "highlight", "underline", "strike", "whiteout",
              "redact", "check", "cross", "dot", "stamp", "note", "link")
STROKES = ("pen", "pencil", "marker", "whitener")
POLYGONS = {   # corners in a unit box, stretched over the item's box (the editor draws the same)
    "triangle": [(0.5, 0), (1, 1), (0, 1)],
    "diamond": [(0.5, 0), (1, 0.5), (0.5, 1), (0, 0.5)],
    "star": [(0.5 + 0.5 * (1 if i % 2 == 0 else 0.4) * math.cos(math.radians(-90 + 36 * i)),
              0.5 + 0.5 * (1 if i % 2 == 0 else 0.4) * math.sin(math.radians(-90 + 36 * i))) for i in range(10)],
}
MAX_EDITS = 3000


def _unit_points(kind: str) -> list[tuple[float, float]]:
    pts = POLYGONS[kind]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    return [((x - min(xs)) / (max(xs) - min(xs)), (y - min(ys)) / (max(ys) - min(ys))) for x, y in pts]


def _dashes(op: dict, width: float) -> str | None:
    style = _choice(op, "dash", ("solid", "dash", "dot"), "solid")
    w = max(width, 0.5)
    return {"dash": f"[{w * 3:.2f} {w * 2:.2f}] 0", "dot": f"[{w * 0.01:.2f} {w * 2:.2f}] 0"}.get(style)


def _arrow_head(page: pymupdf.Page, tail: pymupdf.Point, tip: pymupdf.Point, width: float, stroke, opacity: float):
    angle = math.atan2(tip.y - tail.y, tip.x - tail.x)
    head = max(6.0, width * 4)
    left = tip - (head * math.cos(angle - 0.45), head * math.sin(angle - 0.45))
    right = tip - (head * math.cos(angle + 0.45), head * math.sin(angle + 0.45))
    page.draw_polyline([left, tip, right], color=stroke, fill=stroke, width=max(width, 0.3), closePath=True,
                       stroke_opacity=opacity, fill_opacity=opacity)


def _safe_uri(uri: str) -> str:
    uri = uri.strip()
    if re.match(r"^(https?://|mailto:)", uri, re.I):
        return uri
    if re.match(r"^[\w.-]+\.[a-z]{2,}(/.*)?$", uri, re.I):
        return "https://" + uri
    raise ToolError(f"A link needs a web address (https://…) or mailto:, not “{uri[:80]}”.")


def _rect_of(op: dict, page: pymupdf.Page) -> pymupdf.Rect:
    x, y = _float(op, "x", 0, -10000, 20000), _float(op, "y", 0, -10000, 20000)
    w, h = _float(op, "w", 10, 0, 20000), _float(op, "h", 10, 0, 20000)
    return pymupdf.Rect(x, y, x + w, y + h)


def _image_data(op: dict, extras: dict[str, Input]) -> bytes:
    data = str(op.get("data", ""))
    if data.startswith("data:"):
        head, _, body = data.partition(",")
        if ";base64" not in head or not re.match(r"data:image/(png|jpe?g|webp|gif)", head):
            raise ToolError("A picture in the edits is not PNG/JPG.")
        try:
            raw = base64.b64decode(body, validate=False)
        except ValueError as exc:
            raise ToolError("A picture in the edits could not be read.") from exc
        if head.startswith(("data:image/webp", "data:image/gif")):   # mupdf: convert through PIL
            from PIL import Image
            with Image.open(io.BytesIO(raw)) as image:
                out = io.BytesIO()
                image.convert("RGBA").save(out, "PNG")
                raw = out.getvalue()
        return raw
    ref = extras.get(f"file:{op.get('file', '')}")
    if ref is None:
        raise ToolError("A picture in the edits is missing.")
    return image_frames(ref.path)[0][0]


def apply_edits(doc: pymupdf.Document, ops: list[dict], extras: dict[str, Input], progress: Progress) -> int:
    if not isinstance(ops, list):
        raise ToolError("The edits are not valid.")
    if len(ops) > MAX_EDITS:
        raise ToolError(f"The most is {MAX_EDITS} edits at a time.")
    by_page: dict[int, list[dict]] = {}
    for op in ops:
        if isinstance(op, dict) and op.get("type") in EDIT_TYPES:
            page = _int(op, "page", -1, -1, doc.page_count - 1)
            if page >= 0:
                by_page.setdefault(page, []).append(op)
    done = 0
    kits: dict[str, FontKit] = {}
    for k, (index, items) in enumerate(sorted(by_page.items())):
        progress(k, len(by_page), f"page {index + 1}")
        page = _upright(doc[index])
        rewritten = [op for op in items if op["type"] == "textedit"]
        if rewritten:                               # the page's own text, changed in place (before anything is drawn)
            from .edit_text import apply_text_edits
            done += apply_text_edits(page, rewritten)
        redactions = [op for op in items if op["type"] == "redact"]
        for op in redactions:                       # first: they remove what is under them
            page.add_redact_annot(_rect_of(op, page), fill=color(_text(op, "fill"), (0, 0, 0)))
        if redactions:
            page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
                                  graphics=getattr(pymupdf, "PDF_REDACT_LINE_ART_REMOVE_IF_COVERED", 1))
            done += len(redactions)
        for op in items:
            kind = op["type"]
            if kind in ("redact", "textedit"):
                continue
            opacity = _float(op, "opacity", 1, 0.02, 1)
            stroke = color(_text(op, "color"), (0.86, 0.15, 0.15))
            width = _float(op, "width", 2, 0, 50)
            rect = _rect_of(op, page)
            if kind == "text":
                key = _text(op, "font", 80) or "sans"
                if key not in kits:
                    from . import fonts as F
                    kits[key] = font_kit(key) if key in FONTS else (F.kit(key) or font_kit("sans"))
                kit = kits[key]
                text = _text(op, "text", 5000)
                if text.strip():
                    background = _text(op, "fill") if re.fullmatch(r"#[0-9a-fA-F]{6}", _text(op, "fill")) else ""
                    if background:
                        page.draw_rect(rect, color=None, fill=color(background), fill_opacity=opacity)
                    draw_text(page, rect + (0, 0, 2, 2), text, size=_float(op, "size", 14, 4, 300),
                              rgb=color(_text(op, "color"), (0, 0, 0)), align=_choice(op, "align", ("left", "center",
                              "right"), "left"), bold=_bool(op, "bold"), italic=_bool(op, "italic"),
                              underline=_bool(op, "underline"), strike=_bool(op, "strike"), opacity=opacity, kit=kit,
                              line_height=1.25)
            elif kind == "image":
                page.insert_image(rect, stream=_image_data(op, extras), keep_proportion=False,
                                  rotate=_int(op, "rotate", 0, 0, 270) // 90 * 90)
                if opacity < 1:                     # insert_image has no opacity: a translucent white veil on top
                    page.draw_rect(rect, color=None, fill=(1, 1, 1), fill_opacity=1 - opacity)
            elif kind in ("rect", "roundrect", "ellipse", "whiteout", "highlight", "triangle", "diamond", "star"):
                fill = None
                dashes = None
                if kind == "whiteout":
                    fill, stroke_c, w, fo = color(_text(op, "fill"), (1, 1, 1)), None, 0, 1.0
                elif kind == "highlight":
                    fill, stroke_c, w, fo = color(_text(op, "color"), (1, 0.92, 0.23)), None, 0, min(opacity, 0.45)
                else:
                    fill_hex = _text(op, "fill")
                    fill = color(fill_hex) if re.fullmatch(r"#[0-9a-fA-F]{6}", fill_hex) else None
                    stroke_c, w, fo = (stroke if width > 0 else None), width, opacity
                    dashes = _dashes(op, width) if width > 0 else None
                if kind == "ellipse":
                    page.draw_oval(rect, color=stroke_c, fill=fill, width=w, fill_opacity=fo, stroke_opacity=opacity,
                                   dashes=dashes, lineCap=1 if dashes else 0)
                elif kind in POLYGONS:
                    pts = [pymupdf.Point(rect.x0 + u * rect.width, rect.y0 + v * rect.height) for u, v in _unit_points(kind)]
                    page.draw_polyline(pts, color=stroke_c, fill=fill, width=w, closePath=True, fill_opacity=fo,
                                       stroke_opacity=opacity, dashes=dashes, lineJoin=1, lineCap=1 if dashes else 0)
                else:
                    radius = 0.18 if kind == "roundrect" else None
                    page.draw_rect(rect, color=stroke_c, fill=fill, width=w, fill_opacity=fo, stroke_opacity=opacity,
                                   overlay=True, dashes=dashes, radius=radius, lineCap=1 if dashes else 0)
            elif kind in ("line", "arrow", "arrow2"):
                a = pymupdf.Point(_float(op, "x1", 0, -10000, 20000), _float(op, "y1", 0, -10000, 20000))
                b = pymupdf.Point(_float(op, "x2", 0, -10000, 20000), _float(op, "y2", 0, -10000, 20000))
                page.draw_line(a, b, color=stroke, width=max(width, 0.3), stroke_opacity=opacity,
                               dashes=_dashes(op, width), lineCap=1)
                if kind in ("arrow", "arrow2") and abs(b - a) > 1:
                    _arrow_head(page, a, b, width, stroke, opacity)
                    if kind == "arrow2":
                        _arrow_head(page, b, a, width, stroke, opacity)
            elif kind in STROKES:
                points = op.get("points")
                if isinstance(points, list) and len(points) >= 2:
                    pts = [pymupdf.Point(float(pt[0]), float(pt[1])) for pt in points[:8000]
                           if isinstance(pt, (list, tuple)) and len(pt) == 2
                           and all(isinstance(v, (int, float)) for v in pt)]
                    if len(pts) >= 2:
                        if kind == "whitener":
                            stroke = color(_text(op, "color"), (1, 1, 1))
                        page.draw_polyline(pts, color=stroke, width=max(width, 0.3), stroke_opacity=opacity,
                                           lineCap=1, lineJoin=1)
            elif kind in ("underline", "strike"):
                y = rect.y1 - max(width, 0.3) / 2 if kind == "underline" else (rect.y0 + rect.y1) / 2
                page.draw_line((rect.x0, y), (rect.x1, y), color=stroke, width=max(width, 0.3), stroke_opacity=opacity)
            elif kind == "check":
                pts = [pymupdf.Point(rect.x0 + u * rect.width, rect.y0 + v * rect.height)
                       for u, v in ((0.08, 0.55), (0.38, 0.88), (0.94, 0.12))]
                page.draw_polyline(pts, color=stroke, width=max(width, 0.3), stroke_opacity=opacity, lineCap=1,
                                   lineJoin=1)
            elif kind == "cross":
                for (u0, v0), (u1, v1) in (((0.12, 0.12), (0.88, 0.88)), ((0.88, 0.12), (0.12, 0.88))):
                    page.draw_line((rect.x0 + u0 * rect.width, rect.y0 + v0 * rect.height),
                                   (rect.x0 + u1 * rect.width, rect.y0 + v1 * rect.height), color=stroke,
                                   width=max(width, 0.3), stroke_opacity=opacity, lineCap=1)
            elif kind == "dot":
                page.draw_oval(rect, color=None, fill=stroke, fill_opacity=opacity)
            elif kind == "stamp":
                text = _text(op, "text", 80).strip() or "APPROVED"
                page.draw_rect(rect, color=stroke, width=max(width, 0.5), radius=0.18, stroke_opacity=opacity)
                inner = rect + (width + 2, width + 1, -width - 2, -width - 1)
                size = max(4.0, min(inner.height * 0.62, 200.0))
                top = inner.y0 + max(0.0, (inner.height - size * 1.2) / 2)
                kit = kits.setdefault("sans", font_kit("sans"))
                draw_text(page, pymupdf.Rect(inner.x0, top, inner.x1, inner.y1 + 2), text, size=size, rgb=stroke,
                          align="center", bold=True, opacity=opacity, kit=kit, line_height=1.2, nowrap=True)
            elif kind == "note":
                text = _text(op, "text", 5000)
                if text.strip():
                    annot = page.add_text_annot(rect.tl, text, icon="Note")
                    annot.set_colors(stroke=color(_text(op, "color"), (1, 0.84, 0.2)))
                    annot.set_info(title="PHI PDF Studio")
                    annot.update(opacity=opacity)
            elif kind == "link":
                uri = _text(op, "url", 2000)
                if uri.strip():
                    page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": _safe_uri(uri)})
            done += 1
    return done


def edit(inputs: list[Input], p: dict, out_dir: Path, progress: Progress, extras: dict[str, Input]) -> Result:
    inp = inputs[0]
    doc = open_pdf(inp)
    n = apply_edits(doc, p.get("ops") or [], extras, progress)
    if not n:
        doc.close()
        raise ToolError("Nothing was changed: edit the text or put text, a picture or a shape on a page first.")
    o = _pdf_out(out_dir, doc, _name(inp.stem, "edited"))
    doc.close()
    return Result([o], f"{n} change(s) saved into the PDF.", {"edits": n})


# ---------------------------------------------------------------------------------------------- running
def _elsewhere(module: str, name: str) -> Callable[..., Result]:
    """A tool that lives in another module (loaded when first used: they import this one)."""
    def call(*args: Any) -> Result:
        from importlib import import_module
        return getattr(import_module(f".{module}", __package__), name)(*args)
    call.__name__ = name
    return call


NEEDS_EXTRAS = {"insert", "watermark", "edit"}
RUNNERS: dict[str, Callable[..., Result]] = {
    "merge": merge, "split": split, "organize": organize, "remove": remove, "extract": extract, "rotate": rotate,
    "insert": insert, "compress": compress, "repair": repair, "ocr": _elsewhere("ocr", "ocr"), "img2pdf": img2pdf,
    "pdf2img": pdf2img, "pdf2text": pdf2text, "extract_images": extract_images, "edit": edit, "watermark": watermark,
    "numbers": numbers, "replace": replace, "crop": crop, "resize": resize, "nup": nup, "protect": protect,
    "unlock": unlock, "metadata": metadata, "flatten": flatten, "redact": redact, "sanitize": sanitize,
    "pdf2word": _elsewhere("convert", "pdf2word"), "pdf2excel": _elsewhere("convert", "pdf2excel"),
    "pdf2ppt": _elsewhere("convert", "pdf2ppt"), "office2pdf": _elsewhere("convert", "office2pdf"),
    "text2pdf": _elsewhere("convert", "text2pdf"),
}
SECRET_PARAMS = ("password", "owner_password", "ai_key")


def check_inputs(tool: Tool, inputs: list[Input]) -> None:
    if len(inputs) < tool.min_files:
        raise ToolError(f"{tool.label} needs {tool.min_files} file{'s' if tool.min_files > 1 else ''} or more."
                        if tool.min_files > 1 else f"Choose a file for {tool.label}.")
    if len(inputs) > tool.max_files:
        raise ToolError(f"{tool.label} takes {'one file' if tool.max_files == 1 else f'up to {tool.max_files} files'}.")
    for inp in inputs:
        if tool.accepts == "pdf" and inp.kind != "pdf":
            raise ToolError(f"{inp.name} is not a PDF." + (" Turn pictures into a PDF with “Images to PDF”."
                                                            if inp.kind == "image" else ""))
        if tool.accepts == "image" and inp.kind not in ("image", "pdf"):
            raise ToolError(f"{inp.name} is not a picture.")
        if tool.accepts == "any" and inp.kind not in ("image", "pdf"):
            raise ToolError(f"{inp.name} is not a PDF or a picture.")
        if tool.accepts == "office" and inp.kind != "office":
            raise ToolError(f"{inp.name} is not a Word, Excel, PowerPoint or LibreOffice file.")
        if tool.accepts == "text" and inp.kind != "text":
            raise ToolError(f"{inp.name} is not a text, Markdown or HTML file.")


def run(tool_id: str, inputs: list[Input], params: dict, out_dir: Path, progress: Progress | None = None,
        extras: dict[str, Input] | None = None) -> Result:
    tool = TOOLS.get(tool_id)
    if tool is None:
        raise ToolError("Unknown tool.")
    check_inputs(tool, inputs)
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = RUNNERS[tool_id]
    report = progress or (lambda *_: None)
    if tool_id in NEEDS_EXTRAS:
        return fn(inputs, params or {}, out_dir, report, extras or {})
    return fn(inputs, params or {}, out_dir, report)


def preview(tool_id: str, inputs: list[Input], params: dict, index: int, width: int = 700,
            extras: dict[str, Input] | None = None) -> bytes:
    """One page as it will come out (watermark, numbers, crop, page size, pages per sheet), as a PNG."""
    tool = TOOLS.get(tool_id)
    if tool is None or not tool.preview:
        raise ToolError("No preview for this tool.")
    check_inputs(tool, inputs)
    src = open_pdf(inputs[0])
    count = src.page_count
    index = max(0, min(count - 1, index))
    quiet: Progress = lambda *_: None  # noqa: E731
    out = src
    try:
        if tool_id == "nup":
            per = 4 if params.get("mode") == "booklet" else _int(params, "per_sheet", 2, 2, 16)
            pages = page_list(_text(params, "pages", 4000), count)
            sheet = index
            if params.get("mode") == "booklet":
                out = nup_doc(src, params, pages, quiet)
            else:
                out = nup_doc(src, params, pages[sheet * per: sheet * per + per] or pages[:per], quiet)
            page = out[min(sheet, out.page_count - 1) if params.get("mode") == "booklet" else 0]
        elif tool_id == "resize":
            out = resize_doc(src, params, [index], quiet)
            page = out[0]
        else:
            out = src
            out.select([index])
            if tool_id == "watermark":
                chosen = _stamp_pages_at(params, count, index)
                if chosen:
                    apply_watermark(out, params, extras or {}, [0], quiet)
            elif tool_id == "numbers":
                if _stamp_pages_at(params, count, index):
                    apply_numbers(out, params, [0], quiet, file=inputs[0].stem, total=count, offset=index)
            elif tool_id == "crop":
                if index in page_set(_text(params, "pages", 4000), count):
                    apply_crop(out, params, [0], quiet)
            page = out[0]
        zoom = max(120, min(1400, width)) / max(page.rect.width, 1)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
    finally:
        if out is not src:
            out.close()
        src.close()


def _stamp_pages_at(p: dict, count: int, index: int) -> bool:
    pages = set(page_set(_text(p, "pages", 4000), count))
    skip = set(page_set(_text(p, "skip", 4000), count)) if _text(p, "skip", 10).strip() else set()
    return index in pages and index not in skip


# ---------------------------------------------------------------------------------------------- reading files
def kind_of(name: str, head: bytes) -> str | None:
    """pdf | image | office | text | None (not accepted), from the first bytes and the name's suffix."""
    suffix = Path(name).suffix.lower()
    if head.startswith(b"%PDF") or (suffix == ".pdf" and b"%PDF" in head[:1024]):
        return "pdf"
    signatures = (b"\x89PNG", b"\xff\xd8\xff", b"GIF8", b"BM", b"II*\x00", b"MM\x00*")
    if suffix in IMAGE_SUFFIXES and (head.startswith(signatures) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")):
        return "image"
    if suffix in OFFICE_SUFFIXES:
        zipped = head.startswith(b"PK\x03\x04")                          # docx, xlsx, pptx, odt, ods, odp
        ole = head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")       # doc, xls, ppt
        if (suffix in (".docx", ".xlsx", ".pptx", ".odt", ".ods", ".odp") and zipped) or \
                (suffix in (".doc", ".xls", ".ppt") and ole) or (suffix == ".rtf" and head.startswith(b"{\\rtf")) or \
                (suffix == ".csv" and _plain_text(head)):
            return "office"
        return None
    if suffix in TEXT_SUFFIXES and _plain_text(head):
        return "text"
    return None


def _plain_text(head: bytes) -> bool:
    """UTF-8 text without NUL bytes (the start of a file; a cut-off last character is fine)."""
    if b"\x00" in head:
        return False
    try:
        head.decode("utf-8")
    except UnicodeDecodeError as exc:
        return exc.start >= len(head) - 3
    return True


def describe(path: Path, kind: str) -> dict[str, Any]:
    """What the workspace shows about a file: pages, page sizes, locked, title…"""
    info: dict[str, Any] = {"size": _size(path)}
    if kind == "pdf":
        try:
            doc = pymupdf.open(path, filetype="pdf")
        except Exception as exc:
            return {**info, "pages": 0, "broken": str(exc)[:200]}
        with doc:
            info["locked"] = bool(doc.needs_pass)
            info["encrypted"] = bool(doc.is_encrypted) or bool(doc.needs_pass)
            if doc.needs_pass:
                return {**info, "pages": 0}
            info["pages"] = doc.page_count
            meta = doc.metadata or {}
            info["meta"] = {k: meta.get(k, "") for k in META_KEYS}
            info["version"] = meta.get("format", "")
            info["bookmarks"] = len(doc.get_toc(simple=True))
            sizes = [(round(pg.rect.width, 1), round(pg.rect.height, 1), pg.rotation) for pg in doc]
            info["sizes"] = sizes if len(sizes) <= MAX_PAGES else sizes[:MAX_PAGES]
            info["paper"] = paper_name(*sizes[0][:2]) if sizes else ""
            info["has_text"] = any(doc[i].get_text("text").strip() for i in range(min(doc.page_count, 3)))
            info["forms"] = bool(doc.is_form_pdf)
    elif kind == "image":
        try:
            from PIL import Image
            with Image.open(path) as image:
                info.update(width=image.width, height=image.height, format=image.format or "",
                            frames=getattr(image, "n_frames", 1))
        except Exception as exc:
            info["broken"] = str(exc)[:200]
        info["pages"] = 1
    elif kind == "text":
        try:
            sample = path.read_bytes()[:200_000].decode("utf-8", "replace")
        except OSError:
            sample = ""
        info["chars"] = len(sample)
        info["pages"] = 0
    return info


def paper_name(w: float, h: float) -> str:
    for name, (pw, ph) in PAPER.items():
        if (abs(w - pw) < 3 and abs(h - ph) < 3) or (abs(w - ph) < 3 and abs(h - pw) < 3):
            return name + (" landscape" if w > h else "")
    return f"{w / MM:.0f} × {h / MM:.0f} mm"


def render_page(path: Path, kind: str, index: int, width: int) -> bytes:
    """A page (or the picture) as a PNG about `width` pixels wide."""
    width = max(60, min(1800, width))
    if kind not in ("pdf", "image"):
        raise ToolError("No page picture for this kind of file.")
    if kind == "image":
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((width, width * 3))
            if image.mode not in ("RGB", "RGBA", "L"):
                image = image.convert("RGBA")
            out = io.BytesIO()
            image.save(out, "PNG")
            return out.getvalue()
    with pymupdf.open(path, filetype="pdf") as doc:
        if doc.needs_pass:
            raise ToolError("locked")
        if not 0 <= index < doc.page_count:
            raise IndexError(index)
        page = doc[index]
        zoom = width / max(page.rect.width, 1)
        return page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
