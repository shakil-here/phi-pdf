"""What this computer can do beyond the built-in tools, and how to add what is missing.

Everything except OCR and Office → PDF works with the Python packages alone. OCR uses Tesseract (a free program)
and Office → PDF uses LibreOffice (free); both are found automatically, and the app says how to install them on
the person's own system when they are missing. OCR languages can also be downloaded from inside the app.
"""
from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import threading
import time
from functools import lru_cache
from pathlib import Path

SYSTEM = "mac" if sys.platform == "darwin" else "windows" if os.name == "nt" else "linux"

INSTALL = {
    "tesseract": {
        "mac": "brew install tesseract",
        "windows": "winget install UB-Mannheim.TesseractOCR",
        "linux": "sudo apt install tesseract-ocr        (Fedora: sudo dnf install tesseract)",
    },
    "libreoffice": {
        "mac": "brew install --cask libreoffice",
        "windows": "winget install TheDocumentFoundation.LibreOffice",
        "linux": "sudo apt install libreoffice-core libreoffice-writer libreoffice-calc libreoffice-impress",
    },
}
LINKS = {"tesseract": "https://tesseract-ocr.github.io/tessdoc/Installation.html",
         "libreoffice": "https://www.libreoffice.org/download/download-libreoffice/"}

# Tesseract language codes people ask for most (any of the 100+ codes also works).
LANGUAGES = {
    "eng": "English", "ben": "বাংলা (Bengali)", "hin": "हिन्दी (Hindi)", "ara": "العربية (Arabic)", "urd": "اردو (Urdu)",
    "asm": "অসমীয়া (Assamese)", "nep": "नेपाली (Nepali)", "mar": "मराठी (Marathi)", "tam": "தமிழ் (Tamil)",
    "tel": "తెలుగు (Telugu)", "guj": "ગુજરાતી (Gujarati)", "pan": "ਪੰਜਾਬੀ (Punjabi)", "sin": "සිංහල (Sinhala)",
    "fas": "فارسی (Persian)", "chi_sim": "简体中文 (Chinese, simplified)", "chi_tra": "繁體中文 (Chinese, traditional)",
    "jpn": "日本語 (Japanese)", "kor": "한국어 (Korean)", "tha": "ไทย (Thai)", "vie": "Tiếng Việt (Vietnamese)",
    "ind": "Bahasa Indonesia", "msa": "Bahasa Melayu", "tur": "Türkçe (Turkish)", "rus": "Русский (Russian)",
    "ukr": "Українська (Ukrainian)", "fra": "Français (French)", "deu": "Deutsch (German)", "spa": "Español (Spanish)",
    "por": "Português (Portuguese)", "ita": "Italiano (Italian)", "nld": "Nederlands (Dutch)", "pol": "Polski (Polish)",
    "swe": "Svenska (Swedish)", "ell": "Ελληνικά (Greek)", "heb": "עברית (Hebrew)", "equ": "Math symbols",
}
SKIP_LANGS = {"osd", "snum"}


def language_name(code: str) -> str:
    return LANGUAGES.get(code, code)


# ---------------------------------------------------------------------------------------------- tesseract
def tesseract_path() -> str | None:
    found = shutil.which("tesseract")
    if found:
        return found
    candidates = [Path("/opt/homebrew/bin/tesseract"), Path("/usr/local/bin/tesseract"), Path("/usr/bin/tesseract")]
    if SYSTEM == "windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", ""),
                     os.environ.get("LOCALAPPDATA", "")):
            if base:
                candidates.append(Path(base) / "Tesseract-OCR" / "tesseract.exe")
                candidates.append(Path(base) / "Programs" / "Tesseract-OCR" / "tesseract.exe")
    return next((str(p) for p in candidates if p.is_file()), None)


@lru_cache(maxsize=4)
def _tesseract_info(path: str) -> tuple[str, str]:
    """(version, system tessdata folder) of a tesseract binary."""
    version, folder = "", ""
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        m = re.search(r"tesseract v?(\d+\.\d+(?:\.\d+)?)", out.stdout + out.stderr)
        version = m.group(1) if m else ""
        out = subprocess.run([path, "--list-langs"], capture_output=True, text=True, timeout=20)
        m = re.search(r'"([^"]+)"', out.stdout + out.stderr)
        folder = m.group(1) if m else ""
    except (OSError, subprocess.SubprocessError):
        pass
    return version, folder


class Tessdata:
    """The OCR language files: the system's (installed with Tesseract) plus the ones downloaded in the app.

    Downloads go to <data dir>/tessdata; as soon as it has any, the system's files are linked (or copied) in too,
    so Tesseract gets one folder holding every language."""

    SOURCES = {"fast": "https://github.com/tesseract-ocr/tessdata_fast/raw/main/{code}.traineddata",
               "best": "https://github.com/tesseract-ocr/tessdata_best/raw/main/{code}.traineddata"}

    def __init__(self, own: Path):
        self.own = own
        self._lock = threading.Lock()

    def system_dir(self) -> Path | None:
        binary = tesseract_path()
        if not binary:
            return None
        env = os.environ.get("TESSDATA_PREFIX")
        if env and Path(env).is_dir():
            return Path(env)
        folder = _tesseract_info(binary)[1]
        return Path(folder) if folder and Path(folder).is_dir() else None

    def folder(self) -> Path | None:
        """The folder to pass as --tessdata-dir (None = Tesseract's own default)."""
        if self.own.is_dir() and any(self.own.glob("*.traineddata")):
            self._link_system()
            return self.own
        return None

    def _link_system(self) -> None:
        system = self.system_dir()
        if system is None or system.resolve() == self.own.resolve():
            return
        # the language files, and the "configs" folder that holds Tesseract's output settings (pdf, txt…)
        for src in list(system.glob("*.traineddata")) + [system / "configs", system / "tessconfigs"]:
            if not src.exists():
                continue
            dst = self.own / src.name
            if dst.exists():
                continue
            try:
                os.symlink(src, dst)
            except OSError:
                try:
                    shutil.copytree(src, dst) if src.is_dir() else shutil.copyfile(src, dst)
                except OSError:
                    pass

    def languages(self) -> list[str]:
        codes: set[str] = set()
        for folder in (self.system_dir(), self.own if self.own.is_dir() else None):
            if folder is not None:
                codes |= {p.stem for p in folder.glob("*.traineddata") if p.exists()}
        return sorted(codes - SKIP_LANGS, key=lambda c: (c != "eng", c != "ben", language_name(c).lower()))

    def download(self, code: str, quality: str = "best", timeout: float = 120) -> Path:
        """Fetch a language file from the Tesseract project on GitHub into the app's own folder."""
        import urllib.request

        if not re.fullmatch(r"[a-z]{3}(?:_[a-z]{3,4})?", code or ""):
            raise ValueError("That is not a Tesseract language code (for example ben, hin, chi_sim).")
        url = self.SOURCES.get(quality, self.SOURCES["best"]).format(code=code)
        self.own.mkdir(parents=True, exist_ok=True)
        target = self.own / f"{code}.traineddata"
        tmp = self.own / f".{code}.{os.getpid()}.{int(time.time() * 1000)}.part"
        with self._lock:
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "phi-pdf-studio"})
                with urllib.request.urlopen(request, timeout=timeout) as response, tmp.open("wb") as out:
                    total = 0
                    while chunk := response.read(1 << 16):
                        total += len(chunk)
                        if total > 200 * 1024 * 1024:
                            raise ValueError("That download is unexpectedly large; stopped.")
                        out.write(chunk)
                if total < 10_000:
                    raise ValueError(f"“{code}” was not found on the Tesseract server.")
                if target.is_symlink() or target.exists():
                    target.unlink()
                tmp.replace(target)
            except OSError as exc:
                raise ValueError(f"The download failed ({exc}). Check the internet connection.") from exc
            finally:
                tmp.unlink(missing_ok=True)
        self._link_system()
        return target


# ---------------------------------------------------------------------------------------------- libreoffice
def libreoffice_path() -> str | None:
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    candidates = [Path("/Applications/LibreOffice.app/Contents/MacOS/soffice"), Path("/usr/bin/soffice"),
                  Path("/usr/lib/libreoffice/program/soffice"), Path("/opt/libreoffice/program/soffice")]
    if SYSTEM == "windows":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"), os.environ.get("ProgramFiles(x86)", "")):
            if base:
                candidates.append(Path(base) / "LibreOffice" / "program" / "soffice.exe")
    return next((str(p) for p in candidates if p.is_file()), None)


# ---------------------------------------------------------------------------------------------- summary
def report(tessdata: Tessdata) -> dict:
    """Everything the Settings page and `phi-pdf doctor` show."""
    tess = tesseract_path()
    office = libreoffice_path()
    import importlib.util

    import pymupdf

    exact_word = importlib.util.find_spec("pdf2docx") is not None    # optional: plain-text Word still works

    return {
        "system": SYSTEM, "platform": platform.platform(terse=True), "python": platform.python_version(),
        "pymupdf": pymupdf.VersionBind,
        "tesseract": {"ok": bool(tess), "path": tess or "", "version": _tesseract_info(tess)[0] if tess else "",
                      "languages": tessdata.languages() if tess else [], "install": INSTALL["tesseract"][SYSTEM],
                      "link": LINKS["tesseract"]},
        "libreoffice": {"ok": bool(office), "path": office or "", "install": INSTALL["libreoffice"][SYSTEM],
                        "link": LINKS["libreoffice"]},
        "exact_word": exact_word,
    }


def needs_met(need: str, tessdata: Tessdata) -> bool:
    if need == "ocr":
        return True          # AI reading works without Tesseract
    if need == "tesseract":
        return bool(tesseract_path())
    if need == "libreoffice":
        return bool(libreoffice_path())
    return True
