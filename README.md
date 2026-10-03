# Φ PHI PDF Studio

**Free, open-source PDF tools that run on your own computer or your own server.**
Edit the text inside a PDF like in Word, read scans in 100+ languages (বাংলা too), convert PDF ⇄ Word / Excel /
PowerPoint, merge, split, compress, sign, protect and redact — from a web page, a REST API or the command line.

Made by [Physics Hunters](#credits). Licence: AGPL-3.0 (free to use, change and share; see [Licence](#licence)).

---

## Why another PDF tool?

Each popular free option leaves a gap. PHI PDF Studio was built to close them:

| The usual gap | Who has it | What PHI PDF Studio does |
|---|---|---|
| No real text editing: you can only cover text or stamp new text on top | Stirling-PDF, most web tools | **Edit text in place**: click a word, type, save. Only the words you change are replaced; every other letter of the paragraph stays exactly as it was. The new word goes on the same baseline in the same font, size and colour: the font embedded in the PDF when it has the letters, else the same font installed (or bundled), else the closest standard one. Pictures, lines and coloured boxes behind the text stay. Bengali, Hindi and Arabic are shaped correctly. |
| Heavy: needs Docker or Java, gigabytes of image, lots of RAM | Stirling-PDF | **Pure Python, one command or a double-click.** About 300 MB installed. Docker is optional. |
| Not open source, Windows only, no API | PDF24 Creator | **AGPL open source; Windows, macOS and Linux; a REST API and a command line** over the same engine. |
| Weak or no OCR; little control for mixed-language, complex pages | PDF24, browser (WASM) tools | **Tesseract with any of 100+ languages at once** (`ben+eng`), downloadable from inside the app; page layout modes; cleaning (contrast, noise, black-and-white), straightening of tilted pages and turning of sideways ones. Results: searchable PDF, Word or text. For the hardest pages (Bengali with math, handwriting) an optional **AI reader** (Gemini or Claude, with your own key). |
| No PDF → Word / Excel conversion | browser (WASM) tools | **PDF → Word** (layout kept, or flowing text), **PDF → Excel** (tables found; numbers stay numbers), **PDF → PowerPoint**, **Office → PDF** (with LibreOffice), **Text / Markdown / HTML → PDF**. |
| Big files freeze the browser | browser (WASM) tools | Work happens on the computer running the app, page by page; the browser only shows small page pictures. Uploads stream to disk. |
| No graphical interface; separate libraries to glue together | Python / CLI scripts | **One package**: a clean web interface, the same tools in the terminal, and an API. |

Being honest about the limits:
- Office → PDF needs [LibreOffice](https://www.libreoffice.org/) installed (free); the app tells you the exact
  install command for your system. Everything else works without it.
- Text editing needs a PDF with real text. Scans must go through OCR first. Some typeset Bengali PDFs store their
  letters in a way that cannot be read back; the editor marks those paragraphs (orange) so you retype them.
- Fonts embedded in PDFs usually contain only the letters used, so a new word keeps the PDF's own font when all its
  letters appear somewhere on that page; otherwise the same font when it is installed, otherwise the closest standard
  font (same weight, slant and serif/sans). A changed Bengali, Hindi or Arabic paragraph is still written again whole (for shaping).

## All tools

- **Organize**: merge (files and pictures, a bookmark per file), split (ranges, every N pages, each page, by
  chapters), organize pages (drag, rotate, duplicate, delete, blank pages, mix PDFs), remove, extract, rotate,
  insert pages.
- **Optimize & OCR**: compress (4 levels or your own dpi/quality, grey pictures), OCR, repair.
- **Convert**: PDF → Word, PDF → Excel, PDF → PowerPoint, Office → PDF, Text/Markdown/HTML → PDF,
  images → PDF, PDF → images, PDF → text, extract pictures.
- **Edit & stamp**: the editor (edit text in place, also on scanned pages: OCR finds their text so you can click
  and change it, or turn the whole page into real text; add text in 30+ bundled fonts — বাংলা, handwriting,
  serif, sans — or the fonts on your computer; date, sticky notes, stamps, links, pictures, signatures; pen,
  pencil, marker and a whitener brush; boxes, rounded boxes, circles, triangles, diamonds, stars, lines and
  arrows, solid, dashed or dotted; highlight, underline, strike-out, ticks, crosses and dots; whiteout in the
  paper's colour and true redaction; copy/paste, front/back), watermark (text or logo, tiled, angle, opacity), page numbers and
  header/footer (বাংলা digits, mirrored for books), find & replace, crop (also automatic), page size, pages per
  sheet and booklets.
- **Security & privacy**: protect (AES-256, permissions), unlock, auto-redact (emails, phone numbers, IDs,
  dates, your words or a pattern — really removed), clean hidden data (scripts, attachments, properties, form
  data, comments), title & properties, flatten.

Every result is a new file in **My files**, so tools chain: merge → compress → watermark → protect.

## Start it

### On your own computer (one click)

1. Install [Python 3.10 or newer](https://www.python.org/downloads/) (Windows: tick “Add python.exe to PATH”).
2. Download this project (green **Code** button → **Download ZIP**) and unzip it.
3. Double-click **`start-windows.bat`** (Windows) or **`start-mac.command`** (Mac; the first time: right-click →
   Open). On Linux run `./start-linux.sh`.

The first start installs everything (a few minutes, once) and puts a **PHI PDF Studio** icon on your Desktop
(macOS: also in ~/Applications, so it can go in the Dock; Windows: also in the Start menu). From then on just
double-click the icon: it opens the app in your browser at `http://127.0.0.1:8790`, starting it in the background
when it is not running yet (a second click never starts a second copy). When the icon was deleted, or the
project folder was moved, double-click the start file again: it installs again where needed and brings the icon
back (or run `phi-pdf shortcut`). Your files stay on your computer, in your user data folder.

For OCR also install Tesseract once:

| System | Command |
|---|---|
| Windows | `winget install UB-Mannheim.TesseractOCR` |
| macOS | `brew install tesseract` |
| Ubuntu / Debian | `sudo apt install tesseract-ocr` |

Then add languages in the app: **Settings → OCR languages** (বাংলা, हिन्दी, العربية, 中文… downloaded once).

### With pip

```bash
pip install ".[ai]"     # in this folder
phi-pdf                  # opens the app
phi-pdf doctor           # shows what is installed and how to add the rest
```

### As a server for many people

```bash
docker compose up -d          # http://localhost:8080
```

or without Docker: `phi-pdf serve --public` (put it behind HTTPS with nginx/Caddy and set `PHIPDF_HTTPS=1`).

Public mode is built for strangers sharing one server:
- every visitor gets a **private workspace** (a random id in a signed cookie): nobody can see anyone else's files;
- files are **deleted automatically** after `PHIPDF_KEEP_HOURS` (2 by default);
- upload size, space per visitor and running tools per visitor are limited;
- OCR language downloads and the server's own AI keys are off;
- strict security headers (Content-Security-Policy, no inline scripts), CSRF tokens on every change, downloads
  sandboxed, uploads checked by their content, an optional password for the whole site.

| Setting (environment) | Meaning | Default (public) |
|---|---|---|
| `PHIPDF_KEEP_HOURS` | delete files after this many hours (0 = keep) | 2 |
| `PHIPDF_MAX_UPLOAD_MB` | largest upload | 200 |
| `PHIPDF_MAX_SPACE_MB` | space per visitor | 1024 |
| `PHIPDF_RUNS_PER_VISITOR` | tools running at once per visitor | 2 |
| `PHIPDF_WORKERS` | tools running at once in total | CPU cores |
| `PHIPDF_PASSWORD` | a password everyone must type (a private server) | none |
| `PHIPDF_API_KEY` | the REST API needs `Authorization: Bearer <key>` | none |
| `PHIPDF_ALLOWED_HOSTS` | accepted host names, comma separated | any |
| `PHIPDF_HTTPS` | mark cookies Secure (behind HTTPS) | off |
| `PHIPDF_DATA` | data folder | `./data` (`/data` in Docker) |
| `PHIPDF_ALLOW_DOWNLOADS` | let visitors add OCR languages | off |

Docker build options: `--build-arg OFFICE=1` adds LibreOffice (Office → PDF); `--build-arg OCR_LANGS="eng ben hin"`
chooses the OCR languages baked in.

## Use it from code

**REST API** — one call per tool, nothing stored:

```bash
curl -f -o small.pdf   -F files=@big.pdf  -F 'params={"level":"high"}'                       http://localhost:8790/api/v1/tools/compress
curl -f -o merged.pdf  -F files=@a.pdf -F files=@b.pdf                                         http://localhost:8790/api/v1/tools/merge
curl -f -o scan.docx   -F files=@scan.pdf -F 'params={"language":"ben+eng","output":"docx"}'  http://localhost:8790/api/v1/tools/ocr
curl -f -o stamped.pdf -F files=@doc.pdf -F image_file=@logo.png -F 'params={"kind":"image"}' http://localhost:8790/api/v1/tools/watermark
```

`GET /api/v1/tools` lists the tools. Each tool page in the app has a **“Use this from code”** box that writes the
exact command for the options you picked.

**Command line**:

```bash
phi-pdf tools
phi-pdf run merge a.pdf b.pdf -o out/
phi-pdf run ocr scan.pdf --set language=ben+eng --set output=docx -o out/
phi-pdf run protect report.pdf --password secret -o out/
phi-pdf add-language ben hin
```

## বাংলায় সংক্ষেপে

**PHI PDF Studio** একটি সম্পূর্ণ ফ্রি ও ওপেন-সোর্স PDF টুল। নিজের কম্পিউটারে চলে, তাই ফাইল ইন্টারনেটে যায় না।

- PDF-এর ভেতরের লেখা Word-এর মতো ক্লিক করে বদলানো যায় (বাংলাও)।
- স্ক্যান করা পাতা থেকে লেখা বের করা (OCR) — বাংলা + ইংরেজি একসাথে; বাঁকা পাতা সোজা করে, ময়লা পরিষ্কার করে।
- PDF থেকে Word, Excel, PowerPoint; Word/Excel/PowerPoint থেকে PDF।
- Merge, split, compress, watermark, পাতা নম্বর (বাংলা সংখ্যায়), password, redact — সব এক জায়গায়।

**চালু করতে:** Python ইনস্টল করুন → এই প্রজেক্ট ডাউনলোড করে unzip করুন → Windows-এ `start-windows.bat`,
Mac-এ `start-mac.command` ডাবল-ক্লিক করুন। ব্রাউজারে অ্যাপ খুলে যাবে। বাংলা OCR-এর জন্য Tesseract ইনস্টল করে
অ্যাপের **Settings → OCR languages** থেকে “বাংলা” যোগ করুন।

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[ai,dev]"
.venv/bin/python -m pytest -q        # about 25 tests, a few seconds (OCR tests need Tesseract)
.venv/bin/phi-pdf start --port 8791 --data /tmp/phipdf-dev
```

| Path | What |
|---|---|
| `pdfstudio/tools.py` | every tool as a pure function over files, the tool registry, `run()` and previews |
| `pdfstudio/edit_text.py` | reading a page's paragraphs (or OCR-ing a scanned one) and changing them in place |
| `pdfstudio/fonts.py`, `fonts/` | the editor's fonts: bundled (SIL Open Font Licence) and the ones installed here |
| `pdfstudio/ocr.py`, `ai.py` | Tesseract OCR (cleaning, straightening, parallel pages) and AI reading |
| `pdfstudio/convert.py` | PDF → Word/Excel/PowerPoint, Office → PDF, Text/Markdown/HTML → PDF |
| `pdfstudio/capabilities.py` | finding Tesseract/LibreOffice, install hints, OCR language downloads |
| `pdfstudio/desk.py` | workspaces, files, runs (SQLite + files on disk), clean-up |
| `pdfstudio/web.py` | the web app, its JSON API, the REST API, security |
| `pdfstudio/cli.py` | the `phi-pdf` command |
| `pdfstudio/templates`, `static` | pages and scripts (no frameworks, no CDN: works offline) |

To add a tool: write `def mytool(inputs, params, out_dir, progress) -> Result` in `tools.py` (or another module
via `_elsewhere`), add a `Tool(...)` to `TOOLS` and the function to `RUNNERS`, and an options block in
`templates/tool.html`. The web page, the REST API and the command line pick it up.

## Licence

AGPL-3.0-or-later (see `LICENSE`), because PyMuPDF, the PDF engine, is AGPL. You may use, change and share it
freely. If you run a changed version as a public website, you must offer your changes' source code to its users.

## Credits

Built by Physics Hunters on [PyMuPDF](https://pymupdf.readthedocs.io/) (MuPDF), [Tesseract](https://github.com/tesseract-ocr/tesseract),
[pdf2docx](https://github.com/ArtifexSoftware/pdf2docx), python-docx, openpyxl, python-pptx, FastAPI and Pillow.
