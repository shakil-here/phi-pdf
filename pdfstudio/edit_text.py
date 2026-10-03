"""Change the text already printed on a PDF page, in place (the editor's "Edit text" tool).

`page_blocks` lists a page's paragraphs and lines (as the page is seen) for the editor to show as editable boxes.
`apply_text_edits` swaps what changed. When only words changed (not the look, nor the number of lines), only those
words are replaced: their letters are really removed (a redaction that touches only text, so pictures, lines and
coloured backgrounds stay) and each new word is written on the old baseline in the font, size and colour of the word
it replaces (`_word_plan`); the words after a much wider or narrower one move with it. Otherwise the paragraph is
written again where the old lines were.

Which font prints a new word: the font embedded in the PDF when it has the letters (`PageFonts`), then as below.
Which font prints a rewritten paragraph, best first:
1. the same font, when it is installed on this computer (fonts embedded in PDFs are usually cut down to the
   letters that were used, so they rarely have the new letters);
2. the closest standard font: Helvetica / Times / Courier in the same weight and slant;
3. for Bengali, Devanagari, Arabic… text: HTML layout with shaping (MuPDF's Noto fonts fill in the script).
Latin text keeps the old baselines exactly when the number of lines is unchanged; otherwise it is re-flowed in the
paragraph's box (shrinking a little when it would not fit). Any font of the editor's list (fonts.py) can be chosen too.

Pages that are pictures (scans) have no text to change: `ocr_blocks` reads them with Tesseract into the same kind of
paragraphs, and invisible text (the layer OCR puts over a scan) is found by `page_blocks` too. Both are marked `ocr`:
changing one covers its place on the picture with the paper's colour and writes the new text on top.
"""
from __future__ import annotations

import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

import pymupdf

from . import fonts as F
from . import tools as T

COMPLEX = re.compile("[\u0590-\u08ff\u0900-\u0dff\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff\uac00-\ud7af]")
UNREADABLE = re.compile("[\ufffd\ue000-\uf8ff]")
SHRINK = 0.22                      # of a line's height, top and bottom: tight lines overlap and must not lose letters
MIN_SCALE = 0.6
TEXT_FLAGS = pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_PRESERVE_LIGATURES | pymupdf.TEXT_MEDIABOX_CLIP


# ---------------------------------------------------------------------------------------------- reading
def _hex(color: int) -> str:
    return f"#{color & 0xFFFFFF:06x}"


def _round(box) -> list[float]:
    return [round(v, 2) for v in box]


def _align(lines: list[dict]) -> str:
    """left | center | right | justify, guessed from where the lines start and end."""
    if len(lines) < 2:
        return "left"
    x0 = [ln["bbox"][0] for ln in lines]
    x1 = [ln["bbox"][2] for ln in lines]
    mid = [(a + b) / 2 for a, b in zip(x0, x1)]
    near = lambda values: max(values) - min(values) < 2.5  # noqa: E731
    if near(x0) and near(x1[:-1]) and len(lines) > 2:
        return "justify"
    if near(x0):
        return "left"
    if near(mid):
        return "center"
    if near(x1):
        return "right"
    return "left"


def page_blocks(doc: pymupdf.Document, index: int) -> dict[str, Any]:
    """The paragraphs of one page as seen, for the editor. `doc` is changed (a rotated page is turned upright), so
    pass a document opened just for this."""
    page = doc[index]
    if page.rotation:
        page.remove_rotation()
    textpage = page.get_textpage(flags=TEXT_FLAGS)
    data = page.get_text("dict", textpage=textpage)
    boxes: dict[tuple[int, int], list] = {}              # the words of each line, so a click can pick one
    for x0, y0, x1, y1, word, b_no, l_no, _ in page.get_text("words", textpage=textpage):
        boxes.setdefault((b_no, l_no), []).append([*_round((x0, y0, x1, y1)), word])
    blocks = []
    for b_no, block in enumerate(data.get("blocks", [])):
        if block.get("type") != 0:
            continue
        lines = []
        for l_no, line in enumerate(block.get("lines", [])):
            dx, dy = line.get("dir", (1, 0))
            if abs(dy) > 0.05 or dx <= 0:            # vertical or turned text: not editable here
                continue
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip()]
            if not spans:
                continue
            hidden = all(s.get("alpha", 255) == 0 for s in spans)
            main = max(spans, key=lambda s: len(s["text"].strip()))
            text = "".join(s["text"] for s in line["spans"]).strip()
            box = pymupdf.Rect(spans[0]["bbox"])
            for s in spans[1:]:
                box |= pymupdf.Rect(s["bbox"])
            parts = [(sp["text"], _hex(sp["color"])) for sp in line["spans"] if sp.get("text")]
            lead = len("".join(t for t, _ in parts)) - len("".join(t for t, _ in parts).lstrip())
            lines.append({"bbox": _round(box), "text": text, "origin": _round(spans[0]["origin"]),
                          "size": round(main["size"], 2), "font": main["font"], "flags": main["flags"],
                          "color": _hex(main["color"]), "hidden": hidden, "parts": _cut(parts, lead, len(text)),
                          "words": boxes.get((b_no, l_no), [])})
        if not lines:
            continue
        box = pymupdf.Rect(lines[0]["bbox"])
        for ln in lines[1:]:
            box |= pymupdf.Rect(ln["bbox"])
        sizes = sorted(ln["size"] for ln in lines)
        main = max(lines, key=lambda ln: len(ln["text"]))
        text = "\n".join(ln["text"] for ln in lines)
        blocks.append({"id": len(blocks), "bbox": _round(box), "lines": lines, "text": text,
                       "size": sizes[len(sizes) // 2], "font": main["font"], "color": main["color"],
                       "bold": bool(main["flags"] & 16) or _is_bold(main["font"]),
                       "italic": bool(main["flags"] & 2) or _is_italic(main["font"]),
                       "family": family_of(main["font"], main["flags"]), "align": _align(lines),
                       "unreadable": bool(UNREADABLE.search(text))})
        parts = [ln.pop("parts") for ln in lines]
        colored = [(t + ("\n" if n < len(lines) - 1 and j == len(line) - 1 else ""), c)
                   for n, line in enumerate(parts) for j, (t, c) in enumerate(line)]
        blocks[-1]["runs"] = _runs_of(colored) if "".join(t for t, _ in colored) == text else None
        if all(ln["hidden"] for ln in lines):        # an OCR layer over a picture: the letters seen are pixels
            blocks[-1].update(ocr=True, font="", family="sans", bold=False, italic=False, runs=None)
    hidden = [b for b in blocks if b.get("ocr")]
    if hidden:
        picture, k = _picture(page, 110)
        for b in hidden:
            b["color"], b["bg"] = _colors(picture, [v * k for v in b["bbox"]])
            for ln in b["lines"]:
                ln["color"] = b["color"]
    return {"page": index, "width": round(page.rect.width, 2), "height": round(page.rect.height, 2), "blocks": blocks,
            "scanned": _looks_scanned(page, blocks)}


def _cut(parts: list[tuple[str, str]], start: int, length: int) -> list[tuple[str, str]]:
    """The (text, colour) parts of a line, cut to `length` characters from `start` (the line's text is stripped)."""
    out, pos = [], 0
    for text, color in parts:
        a, b = max(start, pos), min(start + length, pos + len(text))
        if a < b:
            out.append((text[a - pos:b - pos], color))
        pos += len(text)
    return out


# ---------------------------------------------------------------------------------------------- pictures (OCR)
def _picture(page: pymupdf.Page, dpi: int):
    """The page as an RGB numpy array and its pixels per point."""
    import numpy as np
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
    return np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width * 3] \
        .reshape(pix.height, pix.width, 3), dpi / 72


def _otsu(values) -> float:
    """The threshold that best splits values (0…255) in two groups."""
    import numpy as np
    hist = np.bincount(np.clip(values, 0, 255).astype(np.int64), minlength=256).astype(np.float64)
    total = hist.sum()
    weights = np.cumsum(hist)
    means = np.cumsum(hist * np.arange(256))
    between = (means[-1] * weights - means * total) ** 2 / np.maximum(weights * (total - weights), 1e-9)
    return float(np.argmax(between[:-1]))


def _as_hex(v) -> str:
    return "#%02x%02x%02x" % tuple(int(max(0, min(255, round(float(c))))) for c in v)


def _colors(picture, box) -> tuple[str, str]:
    """(ink colour, paper colour) inside a box of the page picture (pixels). The paper is whichever of the two
    tones covers more of the box, so light text on a dark slide comes out right too."""
    import numpy as np
    h, w = picture.shape[:2]
    x0, y0 = max(0, int(box[0]) - 2), max(0, int(box[1]) - 2)
    x1, y1 = min(w, int(box[2]) + 3), min(h, int(box[3]) + 3)
    crop = picture[y0:y1, x0:x1].reshape(-1, 3).astype(np.float32)
    if len(crop) < 4:
        return "#000000", "#ffffff"
    lum = crop @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
    if float(lum.max() - lum.min()) < 24:            # a flat patch: no ink to tell apart
        tone = np.median(crop, axis=0)
        return ("#ffffff" if lum.mean() < 110 else "#000000"), _as_hex(tone)
    low = lum <= _otsu(lum)
    paper_mask = low if low.sum() >= (~low).sum() else ~low
    paper = np.median(crop[paper_mask], axis=0)
    inks = crop[~paper_mask]
    if len(inks) > 3:                                # the ink's own colour, not its blurred edges
        far = np.linalg.norm(inks - paper, axis=1)
        inks = inks[far >= np.percentile(far, 55)]
    ink = np.median(inks, axis=0) if len(inks) else (255 - paper)
    return _as_hex(ink), _as_hex(paper)


def _looks_scanned(page: pymupdf.Page, blocks: list[dict]) -> bool:
    """True when the page is (mostly) a picture with no editable text on it: worth reading with OCR."""
    if any(not b.get("ocr") for b in blocks):
        return False
    area = abs(page.rect)
    covered = 0.0
    for info in page.get_image_info():
        covered += abs(pymupdf.Rect(info["bbox"]) & page.rect)
    return covered > area * 0.3 or not blocks and bool(page.get_drawings()[:1])


def _line_size(text: str, height: float) -> float:
    """A font size for a line Tesseract found, from its height (which depends on the letters it has)."""
    if COMPLEX.search(text):
        return height / 1.05
    tall = bool(re.search(r"[A-Zbdfhklt0-9!?/()\[\]{}|'\"]", text))
    deep = bool(re.search(r"[gjpqy,;()\[\]{}|]", text))
    return height / ((0.72 if tall else 0.52) + (0.22 if deep else 0))


# which fonts to try against what a scanned page shows (the best match is offered when changing its text)
BENGALI_TRY = ("notoserifbengali", "notosansbengali", "tirobangla", "hindsiliguri", "balooda2", "sys-kalpurush",
               "sys-solaimanlipi", "sys-nirmalaui", "sys-vrinda")
LATIN_TRY = ("serif", "sans", "mono", "sys-georgia", "sys-calibri", "sys-verdana", "roboto", "lora", "merriweather")
_kits: dict[str, Any] = {}


def _kit(key: str):
    if key not in _kits:
        _kits[key] = (T.font_kit(key) if F.is_generic(key) else F.kit(key)) or T.font_kit("sans")
    return _kits[key]


def _trim(mask):
    import numpy as np
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if not len(rows) or not len(cols):
        return None
    return mask[rows[0]:rows[-1] + 1, cols[0]:cols[-1] + 1]


def _drawn(text: str, key: str, bold: bool, size: float, k: float):
    """The ink (a trimmed mask) of text drawn in a font, at k pixels per point."""
    import numpy as np
    width, height = size * (len(text) + 4) * 1.3 + 40, size * 3.5
    with pymupdf.open() as doc:
        page = doc.new_page(width=width, height=height)
        T.draw_text(page, pymupdf.Rect(size, size, width, height), text, size=size, bold=bold, kit=_kit(key),
                    nowrap=True)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), colorspace=pymupdf.csGRAY, alpha=False)
    grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width]
    return _trim(grey < 128)


def _alike(seen, drawn) -> float:
    """How alike two ink masks look (−1…1): the shapes, stretched to the same box, and their width to height."""
    import numpy as np
    from PIL import Image, ImageFilter
    if seen is None or drawn is None:
        return -1.0
    hh = 40
    ww = int(max(8, min(640, round(hh * seen.shape[1] / seen.shape[0]))))

    def norm(mask):
        img = Image.fromarray((mask * 255).astype(np.uint8)).resize((ww, hh), Image.BILINEAR)
        return np.asarray(img.filter(ImageFilter.GaussianBlur(1)), dtype=np.float32).ravel()
    a, b = norm(seen), norm(drawn)
    if a.std() < 1 or b.std() < 1:
        return -1.0
    corr = float(np.corrcoef(a, b)[0, 1])
    ratio = (drawn.shape[1] / drawn.shape[0]) / (seen.shape[1] / seen.shape[0])
    return corr - 0.6 * abs(float(np.log(ratio)))


def _match_fonts(words: list[dict], k: float) -> dict[str, str]:
    """The font that looks most like the page, for Bengali (and other scripts) and for Latin text."""
    import math
    found = {}
    for script, tries in (("complex", BENGALI_TRY), ("latin", LATIN_TRY)):
        sample = [w for w in words if (script == "complex") == bool(COMPLEX.search(w["text"]))]
        sample = sorted(sample, key=lambda w: -len(w["text"]) * w["conf"])[:4]
        tries = [key for key in tries if F.is_generic(key) or F.get(key)]
        if not sample or not tries:
            continue
        scores = {key: sum(_alike(w["mask"], _drawn(w["text"], key, False, 24, k / 2)) for w in sample)
                  for key in tries}
        best = max(scores, key=scores.get)
        if not math.isinf(scores[best]):
            found[script] = best
    return found


def _word_mask(grey, box):
    """The ink of one word in the (dark ink on light) page picture, as a trimmed mask."""
    x0, y0, x1, y1 = (int(v) for v in box)
    crop = grey[max(0, y0):y1, max(0, x0):x1]
    if crop.size < 16 or int(crop.max()) - int(crop.min()) < 30:
        return None
    return _trim(crop < _otsu(crop.ravel()))


def _runs_of(words: list[tuple[str, str]]) -> list[dict] | None:
    """[(text, colour)] -> [{t, c}] with neighbours of (nearly) the same colour joined; None when one colour."""
    def rgb(c):
        return tuple(int(c[i:i + 2], 16) for i in (1, 3, 5))
    runs: list[dict] = []
    for text, c in words:
        if runs and sum((a - b) ** 2 for a, b in zip(rgb(runs[-1]["c"]), rgb(c))) ** 0.5 < 80:
            runs[-1]["t"] += text
        else:
            runs.append({"t": text, "c": c})
    return runs if len(runs) > 1 else None


def ocr_blocks(doc: pymupdf.Document, index: int, language: str, dpi: int = 300) -> dict[str, Any]:
    """Read one page that is a picture with Tesseract into editable paragraphs (the same shape as page_blocks,
    each marked `ocr`). Each paragraph also gets what it looks like, so a change can look the same: the paper and
    ink colours (per word: `runs`), the font among the editor's that looks most like it (`fontkey`), bold or not,
    and the size that font needs to be as wide as the words on the page. `doc` is changed (turned upright)."""
    import tempfile

    import numpy as np
    from PIL import Image, ImageOps

    from . import capabilities as C
    from . import ocr as O

    binary = C.tesseract_path()
    if not binary:
        raise T.ToolError("Tesseract (the free OCR program) is not installed. Install it with: "
                          f"{C.INSTALL['tesseract'][C.SYSTEM]}")
    language = O._languages({"language": language}, O.TESSDATA.languages())
    page = doc[index]
    if page.rotation:
        page.remove_rotation()
    picture, k = _picture(page, dpi)
    grey = O.clean(Image.fromarray(picture), "auto")
    if float(np.median(np.asarray(grey))) < 110:     # light letters on a dark slide: Tesseract reads dark on light
        grey = ImageOps.invert(grey)
    with tempfile.TemporaryDirectory(prefix="phipdf-edit-ocr-") as tmp:
        src = Path(tmp) / "page.png"
        grey.save(src, "PNG")
        cmd = [binary, str(src), "stdout", "-l", language, "--psm", "3", "--dpi", str(dpi)]
        tessdata = O.TESSDATA.folder()
        if tessdata:
            cmd += ["--tessdata-dir", str(tessdata)]
        out = O._run(cmd + ["tsv"])
    if out.returncode != 0:
        detail = out.stderr.decode("utf-8", "replace").strip().splitlines()
        raise T.ToolError("Tesseract could not read this page: " + (detail[-1][:300] if detail else "unknown error"))
    grey_arr = np.asarray(grey)
    paragraphs: dict[tuple[int, int], dict[int, list]] = {}
    for row in out.stdout.decode("utf-8", "replace").splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or cells[0] != "5" or not cells[11].strip():
            continue
        try:
            block, par, line = int(cells[2]), int(cells[3]), int(cells[4])
            left, top, width, height, conf = (float(c) for c in cells[6:11])
        except ValueError:
            continue
        paragraphs.setdefault((block, par), {}).setdefault(line, []).append(
            {"box": (left, top, left + width, top + height), "conf": conf, "text": cells[11].strip()})
    found = []
    samples = []
    for key in sorted(paragraphs):
        lines, confs, colored = [], [], []
        for number in sorted(paragraphs[key]):
            words = sorted(paragraphs[key][number], key=lambda w: w["box"][0])
            for n, w in enumerate(words):
                confs.append(w["conf"])
                colored.append((w["text"] + (" " if n < len(words) - 1 else "\n"), _colors(picture, w["box"])[0]))
                if w["conf"] >= 70 and len(re.sub(r"[\W\d_]", "", w["text"])) >= 3:
                    w["mask"] = _word_mask(grey_arr, w["box"])
                    if w["mask"] is not None and w["mask"].shape[0] >= 8:
                        w["para"] = len(found)
                        samples.append(w)
            x0, y0 = min(w["box"][0] for w in words) / k, min(w["box"][1] for w in words) / k
            x1, y1 = max(w["box"][2] for w in words) / k, max(w["box"][3] for w in words) / k
            text = " ".join(w["text"] for w in words)
            size = max(4.0, min(200.0, _line_size(text, y1 - y0)))
            deep = not COMPLEX.search(text) and re.search(r"[gjpqy,;]", text)
            lines.append({"bbox": _round((x0, y0, x1, y1)), "text": text,
                          "origin": _round((x0, y1 - (size * 0.21 if deep else 0))), "size": round(size, 2),
                          "font": "", "flags": 0, "color": "#000000", "hidden": True,
                          "words": [[*_round([v / k for v in w["box"]]), w["text"]] for w in words]})
        text = "\n".join(ln["text"] for ln in lines)
        if not lines or sum(confs) / len(confs) < 25 or len(re.sub(r"\W", "", text)) < 1:
            samples = [w for w in samples if w.get("para") != len(found)]
            continue                                  # specks and smudges read as letters
        if colored:
            colored[-1] = (colored[-1][0].rstrip("\n"), colored[-1][1])
        found.append({"lines": lines, "confs": confs, "text": text, "runs": _runs_of(colored)})

    fonts = _match_fonts(samples, k) if samples else {}
    blocks = []
    for n, para in enumerate(found):
        lines, text = para["lines"], para["text"]
        box = pymupdf.Rect(lines[0]["bbox"])
        for ln in lines[1:]:
            box |= pymupdf.Rect(ln["bbox"])
        ink, paper = _colors(picture, [v * k for v in box])
        sizes = sorted(ln["size"] for ln in lines)
        size = sizes[len(sizes) // 2]
        fontkey = fonts.get("complex" if COMPLEX.search(text) else "latin") or ""
        bold = False
        own = [w for w in samples if w.get("para") == n and bool(COMPLEX.search(w["text"])) ==
               bool(COMPLEX.search(text))]
        own = sorted(own, key=lambda w: -len(w["text"]))[:2]
        if fontkey and own:                           # bold or not, and the size that makes the words as wide
            regular = sum(_alike(w["mask"], _drawn(w["text"], fontkey, False, 24, k / 2)) for w in own)
            heavy = sum(_alike(w["mask"], _drawn(w["text"], fontkey, True, 24, k / 2)) for w in own)
            bold = heavy > regular + 0.03 * len(own)
            fits = []
            for w in own:
                drawn = _drawn(w["text"], fontkey, bold, 24, k)
                if drawn is not None and drawn.shape[1] > 4:
                    fits.append(24 * (w["box"][2] - w["box"][0]) / drawn.shape[1])
            if fits:
                fitted = sorted(fits)[len(fits) // 2]
                if 0.55 * size < fitted < 1.6 * size:
                    for ln in lines:
                        ln["size"] = round(ln["size"] * fitted / size, 2)
                    size = fitted
        for ln in lines:
            ln["color"] = ink
        blocks.append({"id": len(blocks), "bbox": _round(box), "lines": lines, "text": text,
                       "size": round(size, 2), "font": "", "fontkey": fontkey, "color": ink, "bg": paper,
                       "runs": para["runs"], "bold": bold, "italic": False,
                       "family": (F.get(fontkey).generic if F.get(fontkey) else fontkey) if fontkey else "sans",
                       "align": _align(lines), "unreadable": False, "ocr": True,
                       "conf": round(sum(para["confs"]) / len(para["confs"]))})
    return {"page": index, "width": round(page.rect.width, 2), "height": round(page.rect.height, 2), "blocks": blocks,
            "scanned": True, "language": language}


# ---------------------------------------------------------------------------------------------- fonts
def _clean_name(name: str) -> str:
    """"ABCDEF+Arial-BoldMT" -> "arialboldmt"."""
    name = name.split("+", 1)[1] if re.match(r"^[A-Z]{6}\+", name or "") else (name or "")
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _is_bold(name: str) -> bool:
    return bool(re.search(r"bold|black|heavy|semibold|demi", name or "", re.I))


def _is_italic(name: str) -> bool:
    return bool(re.search(r"italic|oblique|slanted", name or "", re.I))


def family_of(name: str, flags: int = 0) -> str:
    """sans | serif | mono for a PDF font name (and PyMuPDF's span flags: 8 = monospaced, 4 = serifed)."""
    low = (name or "").lower()
    if flags & 8 or re.search(r"courier|mono|consol|menlo|typewriter|code", low):
        return "mono"
    if "sans" in low or re.search(r"arial|helvet|verdana|tahoma|calibri|segoe|roboto|inter|open ?sans|noto ?sans", low):
        return "sans"
    if flags & 4 or T._serif(name or ""):
        return "serif"
    return "sans"


BASE14 = {("sans", False, False): "helv", ("sans", True, False): "hebo", ("sans", False, True): "heit",
          ("sans", True, True): "hebi", ("serif", False, False): "tiro", ("serif", True, False): "tibo",
          ("serif", False, True): "tiit", ("serif", True, True): "tibi", ("mono", False, False): "cour",
          ("mono", True, False): "cobo", ("mono", False, True): "coit", ("mono", True, True): "cobi"}


def _font_dirs() -> list[Path]:
    home = Path.home()
    if sys.platform == "darwin":
        dirs = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), home / "Library/Fonts"]
    elif os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        dirs = [windir / "Fonts", Path(os.environ.get("LOCALAPPDATA", home)) / "Microsoft/Windows/Fonts"]
    else:
        dirs = [Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), home / ".local/share/fonts", home / ".fonts"]
    extra = os.environ.get("PHIPDF_FONT_DIRS", "")
    return [Path(p) for p in extra.split(os.pathsep) if p] + dirs + [Path(__file__).resolve().parent / "fonts"]


_index: dict[str, Path] | None = None
_index_lock = threading.Lock()


def system_fonts() -> dict[str, Path]:
    """{clean font name: file} of the TrueType/OpenType fonts installed on this computer (built once)."""
    global _index
    with _index_lock:
        if _index is not None:
            return _index
        found: dict[str, Path] = {}
        for folder in _font_dirs():
            if not folder.is_dir():
                continue
            for path in folder.rglob("*"):
                if path.suffix.lower() not in (".ttf", ".otf") or len(found) > 5000:
                    continue
                found.setdefault(_clean_name(path.stem), path)
                try:
                    font = pymupdf.Font(fontfile=str(path))
                except Exception:  # a font MuPDF cannot read
                    continue
                found.setdefault(_clean_name(font.name), path)
        _index = found
        return found


def find_system_font(name: str) -> Path | None:
    """The installed file of a PDF font name ("ABCDEF+Georgia-Bold" -> Georgia Bold.ttf), or None."""
    want = _clean_name(name)
    if not want or len(want) < 3:
        return None
    fonts = system_fonts()
    for key in (want, re.sub(r"(mt|ps|psmt|regular|roman|book)$", "", want), want + "regular"):
        if key in fonts:
            return fonts[key]
    return None


def covers(font: pymupdf.Font, text: str) -> bool:
    return all(ch.isspace() or font.has_glyph(ord(ch)) for ch in text)


# ---------------------------------------------------------------------------------------------- writing
def _rect(op: dict, key: str = "") -> pymupdf.Rect:
    src = op.get(key) if key else op
    src = src if isinstance(src, dict) else {}
    x, y = T._float(src, "x", 0, -10000, 20000), T._float(src, "y", 0, -10000, 20000)
    w, h = T._float(src, "w", 0, 0, 20000), T._float(src, "h", 0, 0, 20000)
    return pymupdf.Rect(x, y, x + w, y + h)


def _lines(op: dict) -> list[dict]:
    lines = op.get("lines") if isinstance(op.get("lines"), list) else []
    out = []
    for ln in lines[:400]:
        if not isinstance(ln, dict):
            continue
        rect = pymupdf.Rect(T._float(ln, "x", 0, -10000, 20000), T._float(ln, "y", 0, -10000, 20000), 0, 0)
        rect.x1 = rect.x0 + T._float(ln, "w", 0, 0, 20000)
        rect.y1 = rect.y0 + T._float(ln, "h", 0, 0, 20000)
        if rect.is_empty:
            continue
        words = []
        for w in (ln.get("words") if isinstance(ln.get("words"), list) else [])[:500]:
            if isinstance(w, list) and len(w) == 5 and all(isinstance(v, (int, float)) for v in w[:4]):
                words.append((pymupdf.Rect(*w[:4]), str(w[4])))
        out.append({"rect": rect, "ox": T._float(ln, "ox", rect.x0, -10000, 20000),
                    "oy": T._float(ln, "oy", rect.y1, -10000, 20000), "size": T._float(ln, "size", 0, 0, 500),
                    "words": words})
    return out


def _ocr_pieces(op: dict, text: str) -> list[dict]:
    """What to cover and write again for a changed OCR paragraph. Only the changed part of a line is replaced:
    from its first changed word to the line's end; the letters before it, and lines not changed, stay the pixels
    they were (so words OCR misread but nobody changed are never written wrong). A paragraph that was restyled,
    or whose lines were joined or split, is replaced whole."""
    lines = _lines(op)
    if not lines:
        return []
    block = pymupdf.Rect(lines[0]["rect"])
    for ln in lines[1:]:
        block |= ln["rect"]
    orig = str(op.get("orig", ""))
    whole = [{"cover": block, "old": block, "orig": orig, "text": text, "start": 0, "single": len(lines) == 1}]
    old_lines, new_lines = orig.split("\n"), text.split("\n")
    if T._bool(op, "restyled") or not text.strip() or len(old_lines) != len(new_lines) \
            or len(lines) != len(old_lines) or any(not ln["words"] for ln in lines):
        return whole
    from difflib import SequenceMatcher
    pieces, offset = [], 0
    for ln, before, after in zip(lines, old_lines, new_lines):
        if before != after:
            was, now = before.split(" "), after.split(" ")
            if len(was) != len(ln["words"]) or "" in was or "" in now and after:
                return whole
            boxes = [box for box, _ in ln["words"]]
            steps = [step for step in SequenceMatcher(None, was, now, autojunk=False).get_opcodes()]
            first = next(i for i, step in enumerate(steps) if step[0] != "equal")
            i1 = steps[first][1]
            x0 = boxes[i1].x0 if i1 < len(boxes) else boxes[-1].x1
            gaps = sorted(b.x0 - a.x1 for a, b in zip(boxes, boxes[1:]) if b.x0 > a.x1) or [ln["rect"].height * 0.3]
            items = []
            space = gaps[len(gaps) // 2]
            for tag, a0, a1, b0, b1 in steps[first:]:   # the space before each part: as it was on the page
                gap = (boxes[a0].x0 - boxes[a0 - 1].x1) if 0 < a0 < len(boxes) and tag != "insert" else space
                if tag == "equal":                      # unchanged words: moved as they look
                    span = pymupdf.Rect(boxes[a0].x0, ln["rect"].y0, boxes[a1 - 1].x1, ln["rect"].y1)
                    items.append({"kind": "pixels", "rect": span, "gap": max(0.0, gap)})
                elif b1 > b0:                           # new or changed words: written
                    items.append({"kind": "text", "text": " ".join(now[b0:b1]), "gap": max(0.0, gap),
                                  "start": offset + len(" ".join(now[:b0])) + (1 if b0 else 0)})
            pieces.append({"cover": pymupdf.Rect(x0, ln["rect"].y0, max(x0, ln["rect"].x1), ln["rect"].y1),
                           "old": ln["rect"], "orig": before, "text": after, "items": items, "x": x0,
                           "single": True})
        offset += len(after) + 1
    return pieces


def remove_old(page: pymupdf.Page, ops: list[dict], plans: dict[int, list[dict]] | None = None,
               words: dict[int, dict] | None = None) -> None:
    """Take the old lines' letters out (text only: pictures, drawings and fills stay). A paragraph read by OCR is
    letters in a picture: its place is covered with the paper colour (and the invisible OCR text under it goes).
    A paragraph with a word plan loses only the letters of its changed words (and of the words moved after them)."""
    for op in ops:
        if id(op) in (words or {}):
            for rect in words[id(op)]["cover"]:
                page.add_redact_annot(rect, fill=False)
            continue
        if op.get("ocr"):
            for piece in (plans or {}).get(id(op)) or _ocr_pieces(op, _text_of(op)):
                box = piece["cover"]
                pad = max(1.0, min(3.0, box.height * 0.06))
                page.add_redact_annot(box + (-pad, -pad, pad, pad), fill=T.color(T._text(op, "bg"), (1, 1, 1)))
            continue
        for ln in _lines(op):
            r = ln["rect"]
            cut = r.height * SHRINK
            page.add_redact_annot(pymupdf.Rect(r.x0 - 0.3, r.y0 + cut, r.x1 + 0.3, r.y1 - cut), fill=False)
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE,
                          text=pymupdf.PDF_REDACT_TEXT_REMOVE)


def _text_of(op: dict) -> str:
    return str(op.get("text", ""))[:20000].replace("\r\n", "\n").replace("\r", "\n")


def write_new(page: pymupdf.Page, op: dict, plan: list[dict] | None = None) -> None:
    text = _text_of(op)
    if not text.strip():
        return                                                    # emptied: the paragraph is just removed
    lines = _lines(op)
    box = _rect(op)
    if not lines or box.is_empty:
        raise T.ToolError("An edited paragraph lost its position: reload the editor and try again.")
    old_size = T._float(op, "old_size", lines[0]["size"] or 12, 1, 500)
    size = T._float(op, "size", old_size, 2, 500)
    rgb = T.color(T._text(op, "color"), (0, 0, 0))
    bold, italic = T._bool(op, "bold"), T._bool(op, "italic")
    key = T._text(op, "font", 80) or "original"
    chosen = None if key == "original" or F.is_generic(key) else F.get(key)
    own = key == "original" or (chosen is None and not F.is_generic(key))
    if own:
        family = family_of(T._text(op, "fontname", 200), 0)
    else:
        family = chosen.generic if chosen else key
    align = T._choice(op, "align", ("left", "center", "right", "justify"), "left")
    font_file = find_system_font(T._text(op, "fontname", 200)) if own else chosen.face(bold, italic) if chosen else None
    new_lines = text.split("\n")
    room = pymupdf.Rect(box.x0, box.y0, max(box.x1, min(page.rect.x1 - 12, box.x1 + size * 4)),
                        max(box.y1, min(page.rect.y1 - 12, box.y1 + size * 1.4 * max(0, len(new_lines) - len(lines)))))

    runs = _runs(op, text)
    spacing = max(1.0, min(2.5, (lines[1]["oy"] - lines[0]["oy"]) / old_size if len(lines) > 1 else 1.25))
    if op.get("ocr"):                                            # letters that were pixels: match what was seen
        style = {"bold": bold, "italic": italic, "kit": F.kit(chosen.key) if chosen else T.font_kit(family)}
        for piece in plan if plan is not None else _ocr_pieces(op, text):
            if "items" in piece:                        # the changed stretch of one line
                _write_line(page, piece, size, old_size, rgb, runs, spacing, style)
            elif piece["text"].strip():
                _write_like_before(page, room + (0, -size * 0.6, 4, size * 0.8), piece["old"], piece["orig"],
                                   piece["text"], size, old_size, rgb, runs, single=piece["single"], align=align,
                                   line_height=spacing, **style)
        return
    if COMPLEX.search(text) or runs:                             # shaping (Bengali, Arabic…) or several colours
        kit = F.kit(chosen.key) if chosen else \
            T.font_kit(family, font_file if font_file and covers(pymupdf.Font(fontfile=str(font_file)), text) else None)
        T.draw_text(page, room + (0, -size * 0.12, 2, size * 0.4), text, size=size, rgb=rgb, family=family,
                    align=align, bold=bold, italic=italic, kit=kit, line_height=spacing, runs=runs)
        return

    font = None
    if font_file is not None:
        try:
            candidate = pymupdf.Font(fontfile=str(font_file))
            if covers(candidate, text):
                font = candidate
        except Exception:
            font = None
    if font is None:
        font = pymupdf.Font(BASE14[(family, bold, italic)])
    writer = pymupdf.TextWriter(page.rect)
    k = size / old_size
    if len(new_lines) == len(lines):                              # same lines: each on its old baseline
        for ln, line in zip(lines, new_lines):
            line_size = (ln["size"] or old_size) * k
            width = font.text_length(line, fontsize=line_size)
            left, right = ln["rect"].x0, ln["rect"].x1
            limit = max(right, room.x1) - left
            if width > limit > 0:
                line_size *= max(MIN_SCALE, limit / width)
                width = font.text_length(line, fontsize=line_size)
            x = ln["ox"]
            if align == "center":
                x = (left + right) / 2 - width / 2
            elif align == "right":
                x = right - width
            writer.append((x, ln["oy"]), line, font=font, fontsize=line_size)
    else:                                                         # a different number of lines: flow in the box
        spacing = (lines[1]["oy"] - lines[0]["oy"]) / old_size if len(lines) > 1 else 1.2
        code = {"left": 0, "center": 1, "right": 2, "justify": 3}[align]
        trial = size
        while True:
            test = pymupdf.TextWriter(page.rect)
            rest = test.fill_textbox(room, text, font=font, fontsize=trial, align=code,
                                     lineheight=max(1.0, min(2.5, spacing)))
            if not rest or trial <= size * MIN_SCALE:
                writer = test
                break
            trial *= 0.94
    writer.write_text(page, color=rgb)


def _runs(op: dict, text: str) -> list[tuple[str, tuple]] | None:
    """The paragraph's colours, run by run ([{t, c}] from the editor), when it has more than one."""
    raw = op.get("runs")
    if not isinstance(raw, list) or not raw or len(raw) > 5000:
        return None
    runs = []
    for run in raw:
        if not isinstance(run, dict) or not re.fullmatch(r"#[0-9a-fA-F]{6}", str(run.get("c", ""))):
            return None
        runs.append((str(run.get("t", "")).replace("\r\n", "\n").replace("\r", "\n"), T.color(str(run["c"]))))
    if "".join(t for t, _ in runs) != text or len({c for _, c in runs}) < 2:
        return None
    return runs


def _ink_box(page: pymupdf.Page, area: pymupdf.Rect, text: str, size: float, **style) -> pymupdf.Rect | None:
    """Where the ink of text drawn in `area` lands (drawn on a scratch page, the real one is not touched)."""
    import numpy as np
    zoom = 2
    with pymupdf.open() as doc:
        scratch = doc.new_page(width=page.rect.width, height=page.rect.height)
        T.draw_text(scratch, area, text, size=size, rgb=(0, 0, 0), **style)
        clip = (area + (-size, -size, size, size * 2)) & scratch.rect
        if clip.is_empty:
            return None
        pix = scratch.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csGRAY, alpha=False)
    ink = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.stride)[:, :pix.width] < 160
    rows, cols = np.flatnonzero(ink.any(axis=1)), np.flatnonzero(ink.any(axis=0))
    if not len(rows):
        return None
    return pymupdf.Rect(clip.x0 + cols[0] / zoom, clip.y0 + rows[0] / zoom, clip.x0 + (cols[-1] + 1) / zoom,
                        clip.y0 + (rows[-1] + 1) / zoom)


def _write_like_before(page, area, old, orig, text, size, old_size, rgb, runs, *, single, **style):
    """Write changed OCR text so it sits where the old letters were (`old`, their ink on the page) and is as big:
    the old text is first drawn on a scratch page in the same font, and the new text is moved and scaled by how far
    that lands from the letters seen."""
    scale, dx, dy = 1.0, 0.0, 0.0
    orig = orig.strip()
    first = _ink_box(page, area, orig, old_size, **style) if orig else None
    if first is not None and first.width > 1 and first.height > 1:
        scale = max(0.8, min(1.25, old.width / first.width if single else old.height / first.height))
        seen = _ink_box(page, area, orig, old_size * scale, **style) if abs(scale - 1) > 0.01 else first
        if seen is not None:
            align = style.get("align", "left")
            if align == "right":
                dx = old.x1 - seen.x1
            elif align == "center":
                dx = (old.x0 + old.x1) / 2 - (seen.x0 + seen.x1) / 2
            else:
                dx = old.x0 - seen.x0
            dy = (old.y0 + old.y1) / 2 - (seen.y0 + seen.y1) / 2
    T.draw_text(page, area + (dx, dy, dx, dy), text, size=size * scale, rgb=rgb, runs=runs, **style)
    return _ink_box(page, area + (dx, dy, dx, dy), text, size * scale, **style)


def _colored(runs, rgb, start: int, end: int):
    """(runs, colour) for a stretch of the paragraph: its own colours, or the one colour it has."""
    if not runs:
        return None, rgb
    part = _slice_runs(runs, start, end)
    if len({c for _, c in part}) < 2:
        return None, part[0][1] if part else rgb
    return part, rgb


def _write_line(page, piece, size, old_size, rgb, runs, spacing, style) -> None:
    """Lay out the changed stretch of one OCR line left to right: unchanged words as their own pixels, changed
    words written in the matched font, scaled and raised like the line's old letters."""
    line = piece["old"]
    st = dict(style, align="left", nowrap=True, line_height=spacing)
    area = pymupdf.Rect(line.x0, line.y0, page.rect.x1 - 6, line.y1) + (0, -size * 0.6, 0, size * 0.8)
    scale, dy = 1.0, 0.0
    first = _ink_box(page, area, piece["orig"], old_size, **st)
    if first is not None and first.width > 1:
        scale = max(0.8, min(1.25, line.width / first.width))
        seen = _ink_box(page, area, piece["orig"], old_size * scale, **st) if abs(scale - 1) > 0.01 else first
        if seen is not None:
            dy = (line.y0 + line.y1) / 2 - (seen.y0 + seen.y1) / 2
    cursor = piece["x"]
    for n, item in enumerate(piece["items"]):
        gap = item["gap"] if n else 0.0
        if item["kind"] == "pixels":
            moved = item.get("png")
            if moved:
                box, rect = item["box"], item["rect"]
                x = cursor + gap - (rect.x0 - box.x0)
                page.insert_image(pymupdf.Rect(x, box.y0, x + box.width, box.y1), stream=moved, keep_proportion=False)
            cursor += gap + item["rect"].width
            continue
        part, ink = _colored(runs, rgb, item["start"], item["start"] + len(item["text"]))
        where = pymupdf.Rect(cursor + gap, area.y0 + dy, page.rect.x1 - 6, area.y1 + dy)
        probe = _ink_box(page, where, item["text"], size * scale, **st)
        shift = (cursor + gap - probe.x0) if probe is not None else 0.0
        T.draw_text(page, where + (shift, 0, shift, 0), item["text"], size=size * scale, rgb=ink, runs=part, **st)
        cursor = (probe.x1 + shift) if probe is not None else cursor + gap + size * scale * len(item["text"]) * 0.5


def _slice_runs(runs: list[tuple[str, tuple]], start: int, end: int) -> list[tuple[str, tuple]]:
    out, pos = [], 0
    for text, rgb in runs:
        a, b = max(start, pos), min(end, pos + len(text))
        if a < b:
            out.append((text[a - pos:b - pos], rgb))
        pos += len(text)
    return out


# ---------------------------------------------------------------------------------------------- only the changed words
TOKENS = re.compile(r"\s+|\S+")
SLACK = 0.25          # of the font size: a changed word this much narrower than the old one leaves the rest in place
SQUEEZE = 0.86        # the narrowest a changed word (or a line's changed end) may be squeezed to fit its old place


class PageFonts:
    """The fonts a page's text is printed in, ready to print other letters in, best first: the font embedded in the
    PDF (PDF makers usually drop its character map: it is rebuilt from the letters the page shows, so only letters
    used somewhere on the page are there), the installed font of that name, the closest standard font."""

    def __init__(self, page: pymupdf.Page):
        self.page = page
        self._tried: dict[tuple, list[pymupdf.Font]] = {}
        self._glyphs: dict[str, dict[int, int]] | None = None
        self._xrefs: dict[str, list[tuple[int, str]]] | None = None

    def glyphs(self, name: str) -> dict[int, int]:
        """{character: glyph number} of a font, as the page uses it."""
        if self._glyphs is None:
            self._glyphs = {}
            try:
                trace = self.page.get_texttrace()
            except Exception:  # an odd page MuPDF cannot trace: no embedded fonts then
                trace = []
            for span in trace:
                known = self._glyphs.setdefault(_clean_name(span.get("font", "")), {})
                for char in span.get("chars", ()):
                    if char[0] >= 32 and char[1] > 0:
                        known.setdefault(char[0], char[1])
        return self._glyphs.get(_clean_name(name), {})

    def _embedded(self, name: str) -> pymupdf.Font | None:
        if self._xrefs is None:
            self._xrefs = {}
            for xref, ext, kind, basefont, *_ in self.page.get_fonts():
                if kind != "Type3":
                    self._xrefs.setdefault(_clean_name(basefont), []).append((xref, ext))
        found = self._xrefs.get(_clean_name(name), [])
        glyphs = self.glyphs(name)
        if len(found) != 1 or found[0][1] not in ("ttf", "otf") or not glyphs:
            return None                          # not embedded, or two cuts of one name (their glyphs may differ)
        try:
            buffer = self.page.parent.extract_font(found[0][0])[3]
            font = pymupdf.Font(fontbuffer=buffer)
        except Exception:
            return None
        sample = list(glyphs.items())[:60]
        if all(font.has_glyph(u) == g for u, g in sample):
            return font
        font = _with_cmap(buffer, glyphs)
        return font if font is not None and all(font.has_glyph(u) == g for u, g in sample) else None

    def font(self, name: str, flags: int, text: str) -> pymupdf.Font | None:
        """The best font of that name (and look) that has every letter of text, or None."""
        key = (name, flags)
        if key not in self._tried:
            tries = [self._embedded(name)]
            installed = find_system_font(name)
            if installed is not None:
                try:
                    tries.append(pymupdf.Font(fontfile=str(installed)))
                except Exception:
                    pass
            bold, italic = bool(flags & 16) or _is_bold(name), bool(flags & 2) or _is_italic(name)
            tries.append(pymupdf.Font(BASE14[(family_of(name, flags), bold, italic)]))
            self._tried[key] = [f for f in tries if f is not None]
        return next((f for f in self._tried[key] if covers(f, text)), None)


def _with_cmap(buffer: bytes, glyphs: dict[int, int]) -> pymupdf.Font | None:
    """An embedded font with its character map put back ({character: glyph number}, from the page)."""
    try:
        import io

        from fontTools.ttLib import TTFont, newTable
        from fontTools.ttLib.tables._c_m_a_p import CmapSubtable
        font = TTFont(io.BytesIO(buffer))
        order = font.getGlyphOrder()
        wide = max(glyphs) > 0xFFFF
        sub = CmapSubtable.newSubtable(12 if wide else 4)
        sub.platformID, sub.platEncID, sub.language = 3, 10 if wide else 1, 0
        sub.cmap = {u: order[g] for u, g in glyphs.items() if 0 < g < len(order)}
        table = newTable("cmap")
        table.tableVersion = 0
        table.tables = [sub]
        font["cmap"] = table
        out = io.BytesIO()
        font.save(out)
        return pymupdf.Font(fontbuffer=out.getvalue())
    except Exception:  # fontTools missing, or a font it cannot read: the next best font is used
        return None


def _page_lines(page: pymupdf.Page) -> list[dict]:
    """Every level line of the page with its letters (place, baseline, font, size, colour), as page_blocks reads them."""
    out = []
    raw = page.get_text("rawdict", flags=TEXT_FLAGS)
    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            dx, dy = line.get("dir", (1, 0))
            if abs(dy) > 0.05 or dx <= 0:
                continue
            chars = []
            for span in line.get("spans", []):
                style = (span["font"], span["flags"], span["size"], span["color"])
                for ch in span.get("chars", []):
                    chars.append({"c": ch["c"], "box": pymupdf.Rect(ch["bbox"]), "x": ch["origin"][0],
                                  "y": ch["origin"][1], "style": style})
            while chars and chars[0]["c"].isspace():
                chars.pop(0)
            while chars and chars[-1]["c"].isspace():
                chars.pop()
            for n, ch in enumerate(chars):
                ch["i"] = n
            if chars:
                box = pymupdf.Rect(chars[0]["box"])
                for ch in chars[1:]:
                    box |= ch["box"]
                out.append({"text": "".join(ch["c"] for ch in chars), "chars": chars, "box": box})
    return out


def _find_line(page_lines: list[dict], rect: pymupdf.Rect, text: str) -> dict | None:
    """The page's line with this text at (about) this place."""
    best, most = None, 0.0
    for line in page_lines:
        if line["text"] == text:
            shared = abs(line["box"] & rect)
            if shared > most:
                best, most = line, shared
    return best if best is not None and most > 0.5 * min(abs(rect), abs(best["box"])) else None


def _word_plan(op: dict, text: str, fonts: PageFonts, page_lines: list[dict], page_rect) -> dict | None:
    """What to take out and write for a paragraph whose words changed but not its look or number of lines: only the
    changed words are replaced, each in the font, size, colour and baseline of the word it replaces; the other words
    stay the letters they were. When a new word is much wider or narrower, the words after it on that line are
    written again (same fonts) moved by the difference. None when that cannot be done (then the whole paragraph is
    written again)."""
    if op.get("ocr") or T._bool(op, "restyled") or not text.strip() or (T._text(op, "font", 80) or "original") != "original":
        return None
    lines = _lines(op)
    old_lines, new_lines = str(op.get("orig", "")).split("\n"), text.split("\n")
    if not lines or len(old_lines) != len(new_lines) or len(lines) != len(old_lines):
        return None
    box = _rect(op)
    size = T._float(op, "old_size", lines[0]["size"] or 12, 1, 500)
    limit = max(box.x1, min(page_rect.x1 - 12, box.x1 + size * 4)) if len(lines) == 1 else box.x1 + size * 0.3
    plan = {"cover": [], "write": []}
    justified = T._choice(op, "align", ("left", "center", "right", "justify"), "left") == "justify"
    for n, (ln, before, after) in enumerate(zip(lines, old_lines, new_lines)):
        if before == after:
            continue
        if COMPLEX.search(before + after) or UNREADABLE.search(before):
            return None                                  # shaped scripts are written whole (HTML layout)
        line = _find_line(page_lines, ln["rect"], before)
        flush = justified and n < len(lines) - 1          # a justified line (not the last) keeps its right edge
        if line is None or not _line_plan(line, before, after, fonts, max(limit, line["box"].x1), plan, flush):
            return None
    return plan if plan["cover"] or plan["write"] else None


def _line_plan(line: dict, before: str, after: str, fonts: PageFonts, limit: float, plan: dict,
               flush: bool = False) -> bool:
    from difflib import SequenceMatcher
    chars = line["chars"]
    old_t, new_t = TOKENS.findall(before), TOKENS.findall(after)
    starts = [0]
    for tok in old_t:
        starts.append(starts[-1] + len(tok))
    groups = []
    for tag, a0, a1, b0, b1 in SequenceMatcher(None, old_t, new_t, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        if tag == "replace" and a1 - a0 == b1 - b0:      # word for word: each new word takes its old word's look
            groups += [(a, a + 1, b, b + 1) for a, b in zip(range(a0, a1), range(b0, b1)) if old_t[a] != new_t[b]]
        else:
            groups.append((a0, a1, b0, b1))
    edits = []
    for a0, a1, b0, b1 in groups:
        s, e = starts[a0], starts[a1]
        new = "".join(new_t[b0:b1])
        look = [ch for ch in chars[s:e] if not ch["c"].isspace()] or \
            [ch for ch in chars[:s][::-1] if not ch["c"].isspace()][:1] or [ch for ch in chars[e:] if not ch["c"].isspace()][:1]
        name, flags, size, color = look[0]["style"]
        font = fonts.font(name, flags, new)
        if font is None:
            return False
        x = chars[s]["x"] if s < len(chars) else chars[-1]["box"].x1
        edits.append({"s": s, "e": e, "new": new, "x": x, "y": look[0]["y"], "font": font, "size": size,
                      "color": color, "old_w": (chars[e - 1]["box"].x1 - x) if e > s else 0.0,
                      "new_w": font.text_length(new, fontsize=size)})
    if not edits:
        return True
    moved_from = None                                    # the first change after which the line's words move
    for n, ed in enumerate(edits):
        ed["sx"] = 1.0
        room = (limit - ed["x"]) if ed["e"] >= len(chars) else ed["old_w"] + 0.4
        if ed["new_w"] <= room and (ed["e"] >= len(chars) or ed["old_w"] - ed["new_w"] <= SLACK * ed["size"]):
            continue
        if room < ed["new_w"] <= room / SQUEEZE and ed["e"] > ed["s"]:
            ed["sx"] = room / ed["new_w"]
            continue
        moved_from = n
        break
    for ed in edits[:moved_from]:                        # changed in place
        _cover(plan, chars[ed["s"]:ed["e"]])
        if ed["new"].strip():
            plan["write"].append({k: ed[k] for k in ("new", "x", "y", "font", "size", "color", "sx")})
    if moved_from is None:
        return True
    start = edits[moved_from]["s"]                       # from here to the line's end: written again, moved
    _cover(plan, chars[start:])
    writes, shift, pos = [], 0.0, start
    for ed in edits[moved_from:] + [None]:
        stop = ed["s"] if ed else len(chars)
        for run in _runs_between(chars, pos, stop):      # unchanged words: their own letters' look, moved
            name, flags, size, color = run[0]["style"]
            word = "".join(ch["c"] for ch in run)
            font = fonts.font(name, flags, word)
            if font is None:
                return False
            writes.append({"new": word, "x": run[0]["x"] + shift, "y": run[0]["y"], "font": font, "size": size,
                           "color": color, "sx": 1.0, "gap": run[0]["i"] > 0 and chars[run[0]["i"] - 1]["c"].isspace()})
        if ed is None:
            break
        if ed["new"].strip():
            writes.append({k: ed[k] for k in ("new", "y", "font", "size", "color")} |
                          {"x": ed["x"] + shift, "sx": 1.0, "gap": ed["s"] > 0 and chars[ed["s"] - 1]["c"].isspace()})
        shift += ed["new_w"] - ed["old_w"]
        pos = ed["e"]
    end = line["box"].x1 + shift
    x0 = edits[moved_from]["x"]
    gaps = sum(1 for w in writes[1:] if w["gap"])
    size = edits[moved_from]["size"]
    if flush and gaps and (line["box"].x1 - end) / gaps > -0.15 * size:   # the difference spread over the spaces
        each, k = (line["box"].x1 - end) / gaps, 0
        for w in writes[1:]:
            k += w["gap"]
            w["x"] += each * k
        end = line["box"].x1
    if end > limit:                                      # too long for its place: the moved part is squeezed
        squeeze = (limit - x0) / max(1e-3, end - x0)
        if squeeze < SQUEEZE:
            return False
        for w in writes:
            w["x"] = x0 + (w["x"] - x0) * squeeze
            w["sx"] = squeeze
    plan["write"] += writes
    return True


def _runs_between(chars: list[dict], start: int, stop: int) -> list[list[dict]]:
    """The letters from start to stop as runs to write: one per word and look (spaces are left out)."""
    runs: list[list[dict]] = []
    for n in range(start, stop):
        ch = chars[n]
        if ch["c"].isspace():
            runs.append([])
            continue
        if runs and runs[-1] and runs[-1][-1]["style"] == ch["style"] and abs(runs[-1][-1]["y"] - ch["y"]) < 0.1:
            runs[-1].append(ch)
        else:
            runs.append([ch])
    return [r for r in runs if r]


def _cover(plan: dict, chars: list[dict]) -> None:
    """Rects that take out exactly these letters: the middle of each letter (MuPDF removes a letter its box touches,
    so a rect must not reach the letters beside them)."""
    for ch in chars:
        r = ch["box"]
        if r.width > 0.2 and r.height > 0.2:
            plan["cover"].append(pymupdf.Rect(r.x0 + r.width * 0.3, r.y0 + r.height * 0.3,
                                              r.x1 - r.width * 0.3, r.y1 - r.height * 0.3))


def _write_words(page: pymupdf.Page, plan: dict) -> None:
    for w in plan["write"]:
        writer = pymupdf.TextWriter(page.rect)
        writer.append((w["x"], w["y"]), w["new"], font=w["font"], fontsize=w["size"])
        morph = (pymupdf.Point(w["x"], w["y"]), pymupdf.Matrix(w["sx"], 1)) if abs(w["sx"] - 1) > 1e-3 else None
        writer.write_text(page, color=T.color(_hex(w["color"])), morph=morph)


def apply_text_edits(page: pymupdf.Page, ops: list[dict]) -> int:
    """Swap every edited paragraph of one (upright) page. Returns how many changed."""
    ops = [op for op in ops if isinstance(op, dict) and str(op.get("text", "")) != str(op.get("orig", "\x00"))
           or isinstance(op, dict) and op.get("restyled")]
    if not ops:
        return 0
    plans, words = {}, {}
    fonts, page_lines = PageFonts(page), None
    for op in ops:
        if not op.get("ocr"):                    # only the changed words, when that can be done
            if page_lines is None:
                page_lines = _page_lines(page)
            plan = _word_plan(op, _text_of(op), fonts, page_lines, page.rect)
            if plan is not None:
                words[id(op)] = plan
            continue
        plans[id(op)] = _ocr_pieces(op, _text_of(op))
        for piece in plans[id(op)]:
            pad = max(1.0, min(3.0, piece["cover"].height * 0.06))
            for item in piece.get("items", []):
                if item["kind"] == "pixels":             # copied before anything is covered
                    box = (item["rect"] + (-0.5, -pad, 0.5, pad)) & page.rect
                    item["box"] = box
                    item["png"] = page.get_pixmap(dpi=300, clip=box, alpha=False).tobytes("png") \
                        if not box.is_empty else b""
    remove_old(page, ops, plans, words)
    for op in ops:
        if id(op) in words:
            _write_words(page, words[id(op)])
        else:
            write_new(page, op, plans.get(id(op)))
    return len(ops)
