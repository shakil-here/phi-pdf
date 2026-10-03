"""The fonts the editor offers for new and changed text.

- the three standard families (sans / serif / mono: Helvetica, Times, Courier, with MuPDF's Noto for other scripts);
- the fonts bundled in pdfstudio/fonts (SIL Open Font Licence, from Google Fonts): sans, serif, mono, display,
  handwriting, Bengali, Devanagari and Arabic families, so every computer and server has them;
- well-known fonts installed on this computer (Arial, Georgia, Times New Roman, Verdana…), when they are there.

A family has up to four faces (regular, bold, italic, bold italic). Text drawn through HTML layout (`kit`) gets every
face, so bold and italic pick the right file; text written on baselines takes one file (`face`).
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

BUNDLED_DIR = Path(__file__).parent / "fonts"
STYLES = ("Regular", "Bold", "Italic", "BoldItalic")
GENERIC = {"sans": "Sans (Helvetica + Noto)", "serif": "Serif (Times + Noto Serif)", "mono": "Monospace (Courier)"}
CSS_STACK = {"sans": "sans-serif", "serif": "serif", "mono": "monospace"}

# family, group, generic fallback (the file names are the family without spaces: "Open Sans" -> OpenSans-Bold.ttf)
BUNDLED = [
    ("Roboto", "Sans", "sans"), ("Open Sans", "Sans", "sans"), ("Lato", "Sans", "sans"),
    ("Montserrat", "Sans", "sans"), ("Poppins", "Sans", "sans"),
    ("Merriweather", "Serif", "serif"), ("Lora", "Serif", "serif"), ("Playfair Display", "Serif", "serif"),
    ("Libre Baskerville", "Serif", "serif"),
    ("Roboto Mono", "Monospace", "mono"), ("JetBrains Mono", "Monospace", "mono"),
    ("Oswald", "Display", "sans"), ("Bebas Neue", "Display", "sans"),
    ("Dancing Script", "Handwriting", "serif"), ("Caveat", "Handwriting", "sans"),
    ("Great Vibes", "Handwriting", "serif"), ("Pacifico", "Handwriting", "sans"), ("Satisfy", "Handwriting", "serif"),
    ("Homemade Apple", "Handwriting", "serif"),
    ("Hind Siliguri", "বাংলা", "sans"), ("Noto Sans Bengali", "বাংলা", "sans"),
    ("Noto Serif Bengali", "বাংলা", "serif"), ("Tiro Bangla", "বাংলা", "serif"), ("Baloo Da 2", "বাংলা", "sans"),
    ("Galada", "বাংলা", "sans"), ("Atma", "বাংলা", "sans"),
    ("Noto Sans Devanagari", "हिन्दी & Arabic", "sans"), ("Noto Naskh Arabic", "हिन्दी & Arabic", "serif"),
]

# installed fonts looked for: family, generic, Windows file names (regular, bold, italic, bold italic)
SYSTEM = [
    ("Arial", "sans", ("arial", "arialbd", "ariali", "arialbi")),
    ("Arial Narrow", "sans", ("arialn", "arialnb", "arialni", "arialnbi")),
    ("Arial Black", "sans", ("ariblk", "", "", "")),
    ("Verdana", "sans", ("verdana", "verdanab", "verdanai", "verdanaz")),
    ("Tahoma", "sans", ("tahoma", "tahomabd", "", "")),
    ("Trebuchet MS", "sans", ("trebuc", "trebucbd", "trebucit", "trebucbi")),
    ("Calibri", "sans", ("calibri", "calibrib", "calibrii", "calibriz")),
    ("Segoe UI", "sans", ("segoeui", "segoeuib", "segoeuii", "segoeuiz")),
    ("Times New Roman", "serif", ("times", "timesbd", "timesi", "timesbi")),
    ("Georgia", "serif", ("georgia", "georgiab", "georgiai", "georgiaz")),
    ("Cambria", "serif", ("cambria", "cambriab", "cambriai", "cambriaz")),
    ("Garamond", "serif", ("gara", "garabd", "garait", "")),
    ("Book Antiqua", "serif", ("bkant", "antquab", "antquai", "antquabi")),
    ("Courier New", "mono", ("cour", "courbd", "couri", "courbi")),
    ("Consolas", "mono", ("consola", "consolab", "consolai", "consolaz")),
    ("Comic Sans MS", "sans", ("comic", "comicbd", "comici", "comicz")),
    ("Impact", "sans", ("impact", "", "", "")),
    ("Nirmala UI", "sans", ("nirmala", "nirmalab", "", "")),
    ("Vrinda", "sans", ("vrinda", "vrindab", "", "")),
    ("SolaimanLipi", "sans", ("solaimanlipi", "", "", "")),
    ("Kalpurush", "sans", ("kalpurush", "", "", "")),
    ("Liberation Sans", "sans", ("", "", "", "")),
    ("Liberation Serif", "serif", ("", "", "", "")),
    ("DejaVu Sans", "sans", ("", "", "", "")),
    ("DejaVu Serif", "serif", ("", "", "", "")),
]


@dataclass
class Family:
    key: str
    label: str
    group: str
    generic: str
    css: str                                   # the CSS font-family name the browser shows it with
    faces: dict[str, Path] = field(default_factory=dict)
    bundled: bool = False

    def face(self, bold: bool, italic: bool) -> Path | None:
        """The file for this weight and slant: exact, else the nearest one there is."""
        want = ("Bold" if bold else "") + ("Italic" if italic else "") or "Regular"
        for name in (want, "Bold" if bold else "Italic" if italic else "Regular", "Regular"):
            if name in self.faces:
                return self.faces[name]
        return next(iter(self.faces.values()), None)

    def has(self, bold: bool, italic: bool) -> bool:
        return (("Bold" if bold else "") + ("Italic" if italic else "") or "Regular") in self.faces


def _clean(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def _font_dirs() -> list[Path]:
    from .edit_text import _font_dirs as dirs
    return dirs()


def _installed_files() -> dict[str, Path]:
    """{clean file name: path} of the TrueType/OpenType fonts installed here (names only: quick)."""
    found: dict[str, Path] = {}
    for folder in _font_dirs():
        if not folder.is_dir():
            continue
        try:
            for path in folder.rglob("*"):
                if path.suffix.lower() in (".ttf", ".otf") and len(found) < 20000:
                    found.setdefault(_clean(path.stem), path)
        except OSError:
            continue
    return found


_catalog: dict[str, Family] | None = None
_lock = threading.Lock()


def catalog() -> dict[str, Family]:
    """Every font family on offer, by key (built once)."""
    global _catalog
    with _lock:
        if _catalog is not None:
            return _catalog
        out: dict[str, Family] = {}
        for key, label in GENERIC.items():
            out[key] = Family(key, label, "Standard", key, "")
        for family, group, generic in BUNDLED:
            slug = family.replace(" ", "")
            faces = {s: BUNDLED_DIR / f"{slug}-{s}.ttf" for s in STYLES if (BUNDLED_DIR / f"{slug}-{s}.ttf").is_file()}
            if faces:
                key = _clean(family)
                out[key] = Family(key, family, group, generic, f"PHI {family}", faces, bundled=True)
        if os.environ.get("PHIPDF_SYSTEM_FONTS", "1") != "0":
            files = _installed_files()
            for family, generic, win in SYSTEM:
                base = _clean(family)
                faces: dict[str, Path] = {}
                for style, win_name in zip(STYLES, win):
                    suffix = "" if style == "Regular" else _clean(style)
                    extra = (base + "regular", base + "mt") if style == "Regular" else (base + suffix + "mt",)
                    for cand in (base + suffix, *extra, win_name):
                        if cand and cand in files:
                            faces[style] = files[cand]
                            break
                if "Regular" in faces:
                    key = "sys-" + base
                    out[key] = Family(key, family, "On this computer", generic, family, faces)
        _catalog = out
        return out


def get(key: str) -> Family | None:
    return catalog().get(str(key or ""))


def is_generic(key: str) -> bool:
    return key in GENERIC


def groups() -> list[tuple[str, list[tuple[str, str]]]]:
    """[(group, [(key, label)])] for a <select>, in a fixed order."""
    order: dict[str, list[tuple[str, str]]] = {}
    for fam in catalog().values():
        order.setdefault(fam.group, []).append((fam.key, fam.label))
    return list(order.items())


def browser_map() -> dict[str, dict[str, str]]:
    """{key: {css, generic}} for the editor's screen."""
    return {k: {"css": f.css, "generic": f.generic} for k, f in catalog().items()}


def stylesheet() -> str:
    """@font-face rules for the bundled fonts (the browser loads one only when it is used)."""
    rules = []
    for fam in catalog().values():
        if not fam.bundled:
            continue
        for style, path in fam.faces.items():
            rules.append(f"@font-face {{ font-family: \"{fam.css}\"; src: url(/fonts/{path.name}) format(\"truetype\"); "
                         f"font-weight: {700 if 'Bold' in style else 400}; "
                         f"font-style: {'italic' if 'Italic' in style else 'normal'}; font-display: swap; }}")
    return "\n".join(rules) + "\n"


def bundled_file(name: str) -> Path | None:
    """A bundled font file by its name (for serving it to the browser), or None."""
    if not re.fullmatch(r"[A-Za-z0-9]+-(Regular|Bold|Italic|BoldItalic)\.ttf", name or ""):
        return None
    path = BUNDLED_DIR / name
    return path if path.is_file() else None


def kit(key: str):
    """A FontKit (tools.draw_text) with every face of the family, or None for a standard one."""
    from . import tools as T
    import pymupdf

    fam = get(key)
    if fam is None or not fam.faces:
        return None
    archive = pymupdf.Archive()
    folders: set[Path] = set()
    rules = []
    for style, path in fam.faces.items():
        if path.parent not in folders:
            archive.add(str(path.parent))
            folders.add(path.parent)
        rules.append(f"@font-face {{font-family: own; src: url(\"{path.name}\"); "
                     f"font-weight: {'bold' if 'Bold' in style else 'normal'}; "
                     f"font-style: {'italic' if 'Italic' in style else 'normal'};}}")
    stack = CSS_STACK.get(fam.generic, "sans-serif")
    return T.FontKit("\n".join(rules) + f"\n* {{font-family: own, {stack};}}", archive)
