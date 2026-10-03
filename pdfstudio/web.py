"""The web app: the tools hub, My files, one page per tool, Organize pages, Edit PDF, Settings, Developers, and the
JSON API behind them, plus the stateless REST API (/api/v1) for scripts and other programs.

Safety (it may face the internet):
- every visitor works in their own workspace (a random id in a signed cookie); files of other workspaces are
  never found, so ids cannot be guessed across visitors;
- every state-changing request needs the CSRF token of the page (header X-CSRF-Token);
- on a personal computer only 127.0.0.1 / localhost Host headers are accepted (stops DNS-rebinding pages);
- a strict Content-Security-Policy (no inline scripts or styles); downloads are sandboxed and never served as
  HTML; uploads are checked by their first bytes;
- optional password for the whole site, optional bearer key for the REST API.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import zipfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import APP_NAME, APP_TAGLINE, __version__
from . import ai, capabilities, fonts, ocr, tools
from .config import Settings
from .desk import FILE_ID, Desk, QuotaError, clean_name

log = logging.getLogger("pdfstudio")
HERE = Path(__file__).resolve().parent
LOCAL_WS = "local"
WS_COOKIE, AUTH_COOKIE = "phipdf_ws", "phipdf_auth"
CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")
MEDIA = {"pdf": "application/pdf", "zip": "application/zip", ".png": "image/png", ".jpg": "image/jpeg",
         ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif", ".bmp": "image/bmp", ".tif": "image/tiff",
         ".tiff": "image/tiff",
         ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
         ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
         ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
         ".odt": "application/vnd.oasis.opendocument.text", ".ods": "application/vnd.oasis.opendocument.spreadsheet",
         ".odp": "application/vnd.oasis.opendocument.presentation", ".doc": "application/msword",
         ".xls": "application/vnd.ms-excel", ".ppt": "application/vnd.ms-powerpoint", ".rtf": "application/rtf",
         ".csv": "text/csv; charset=utf-8"}
ACCEPT = {"pdf": ".pdf,application/pdf", "image": ".png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,.pdf,image/*",
          "any": ".pdf,.png,.jpg,.jpeg,.webp,.gif,.bmp,.tif,.tiff,application/pdf,image/*",
          "office": ",".join(sorted(tools.OFFICE_SUFFIXES)), "text": ",".join(sorted(tools.TEXT_SUFFIXES)),
          "all": ",".join([".pdf", ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"]
                          + sorted(tools.OFFICE_SUFFIXES) + sorted(tools.TEXT_SUFFIXES))}
CLEANUP_DAYS = (7, 30, 90)
# Downloads: a PDF may open in the browser's own viewer (which needs no page scripts); anything else is sandboxed.
DOWNLOAD_CSP = {"pdf": "frame-ancestors 'self'",
                "other": "sandbox; default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'"}


def fmt_size(num: int | float) -> str:
    return tools.human_size(int(num or 0))


def fmt_time(ts: float | None) -> str:
    return time.strftime("%d %b %Y, %H:%M", time.localtime(ts)) if ts else ""


def _secret(data_dir: Path) -> bytes:
    path = data_dir / "secret.key"
    if path.is_file() and len(path.read_bytes()) >= 32:
        return path.read_bytes()
    data_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    tmp = path.with_suffix(".tmp")
    tmp.write_bytes(key)
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    return key


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.load()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    secret = _secret(settings.data_dir)
    desk = Desk(settings.data_dir, workers=settings.workers, keep_hours=settings.keep_hours,
                max_workspace_mb=settings.max_workspace_mb, runs_per_workspace=settings.runs_per_workspace)
    ocr.TESSDATA = capabilities.Tessdata(settings.data_dir / "tessdata")
    ai.USE_SERVER_KEYS = settings.server_ai_keys and not settings.public
    api_slots = asyncio.Semaphore(max(1, settings.workers))
    max_bytes = settings.max_upload_mb * 1024 * 1024

    app = FastAPI(title=APP_NAME, version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.desk, app.state.settings = desk, settings
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["size"] = fmt_size
    templates.env.filters["dt"] = fmt_time
    templates.env.globals.update(APP_NAME=APP_NAME, APP_TAGLINE=APP_TAGLINE, VERSION=__version__)

    # ------------------------------------------------------------------ signing, workspaces, sign-in
    def sign(value: str) -> str:
        return hmac.new(secret, value.encode(), hashlib.sha256).hexdigest()[:32]

    def ws_from_cookie(request: Request) -> str | None:
        raw = request.cookies.get(WS_COOKIE, "")
        ws, _, mac = raw.partition(".")
        if len(ws) == 32 and all(c in "0123456789abcdef" for c in ws) and hmac.compare_digest(mac, sign("ws:" + ws)):
            return ws
        return None

    def csrf_for(ws: str) -> str:
        return sign("csrf:" + ws)

    auth_value = sign("auth:" + hashlib.sha256(settings.password.encode()).hexdigest()) if settings.password else ""

    def signed_in(request: Request) -> bool:
        return not settings.password or hmac.compare_digest(request.cookies.get(AUTH_COOKIE, ""), auth_value)

    def host_ok(request: Request) -> bool:
        host = (request.headers.get("host") or "").lower()
        name = host.rsplit(":", 1)[0] if not host.startswith("[") else host.split("]")[0] + "]"
        if settings.allowed_hosts:
            return name in settings.allowed_hosts or host in settings.allowed_hosts or name in ("127.0.0.1", "localhost")
        if settings.public:
            return True
        return name in ("127.0.0.1", "localhost", "[::1]", "testserver")

    def cookie_args() -> dict[str, Any]:
        return {"httponly": True, "samesite": "lax", "secure": settings.https, "path": "/"}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if not host_ok(request):
            return Response("This address is not allowed. Open the app at http://127.0.0.1 instead.", 421)
        new_ws = None
        if path.startswith("/api/v1/"):
            ws = "api"                                       # the REST API keeps nothing: no workspace, no cookie
        elif settings.public:
            ws = ws_from_cookie(request)
            if ws is None:
                ws = new_ws = secrets.token_hex(16)
        else:
            ws = LOCAL_WS
        request.state.ws = ws
        if request.method == "POST" and settings.public:   # refuse an oversized body before it reaches the disk
            try:
                length = int(request.headers.get("content-length") or 0)
            except ValueError:
                length = 0
            if length > max_bytes * 5 + 1_000_000:
                return JSONResponse({"error": f"That is too large: up to {settings.max_upload_mb} MB per file."},
                                    status_code=413)
        open_paths = path.startswith(("/static/", "/login", "/healthz", "/api/v1/"))
        if not open_paths and not signed_in(request):
            if path.startswith("/api/"):
                return JSONResponse({"error": "Sign in first."}, status_code=401)
            return RedirectResponse(f"/login?next={quote(path)}", status_code=303)
        if request.method == "POST" and path.startswith("/api/") and not path.startswith("/api/v1/"):
            token = request.headers.get("x-csrf-token", "")
            if new_ws is not None or not hmac.compare_digest(token, csrf_for(ws)):
                return JSONResponse({"error": "The page is out of date: reload it and try again."}, status_code=403)
        response = await call_next(request)
        if new_ws is not None:
            response.set_cookie(WS_COOKIE, f"{new_ws}.{sign('ws:' + new_ws)}", max_age=86400 * 30, **cookie_args())
        response.headers.setdefault("Content-Security-Policy", CSP)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if path.startswith("/static/"):
            response.headers["Cache-Control"] = "public, max-age=3600"
        return response

    # ------------------------------------------------------------------ helpers
    def caps() -> dict:
        return capabilities.report(ocr.TESSDATA)

    def page(request: Request, name: str, **context: Any) -> HTMLResponse:
        ws = request.state.ws
        context.update(request=request, csrf=csrf_for(ws), path=request.url.path, settings=settings,
                       public=settings.public, keep_hours=settings.keep_hours, signed=bool(settings.password),
                       groups_list=tool_groups(), tools_json=tools_json())
        return templates.TemplateResponse(request, name, context)

    def tool_groups() -> list[tuple[str, str, list[tools.Tool]]]:
        return [(gid, label, [t for t in tools.TOOLS.values() if t.group == gid]) for gid, label in tools.GROUPS.items()]

    def tools_json() -> str:
        report = {"tesseract": bool(capabilities.tesseract_path()), "libreoffice": bool(capabilities.libreoffice_path())}
        data = {t.id: {"label": t.label, "icon": t.icon, "accepts": t.accepts, "min": t.min_files, "max": t.max_files,
                       "page": t.page, "preview": t.preview, "group": t.group, "needs": t.needs,
                       "ready": report.get(t.needs, True)} for t in tools.TOOLS.values()}
        return script_json(data)

    def file_or_404(request: Request, file_id: str) -> dict[str, Any]:
        row = desk.get(request.state.ws, file_id)
        if row is None:
            raise StarletteHTTPException(404, "That file is no longer in My files.")
        return row

    def ids_of(value: Any) -> list[str]:
        items = value if isinstance(value, list) else str(value or "").split(",")
        return [str(i).strip() for i in items if FILE_ID.match(str(i).strip())][:500]

    async def json_body(request: Request) -> dict:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError):
            raise StarletteHTTPException(400, "The request is not valid JSON.")
        if not isinstance(body, dict):
            raise StarletteHTTPException(400, "The request is not valid.")
        return body

    async def save_upload(upload: UploadFile, target: Path) -> None:
        total = 0
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("wb") as out:
            while chunk := await upload.read(1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    out.close()
                    target.unlink(missing_ok=True)
                    raise tools.ToolError(f"{upload.filename}: larger than {settings.max_upload_mb} MB.")
                out.write(chunk)

    # ------------------------------------------------------------------ pages
    @app.get("/healthz")
    def healthz():
        return {"ok": True, "version": __version__}

    @app.get("/")
    def home(request: Request):
        ws = request.state.ws
        return page(request, "home.html", recent=desk.files(ws, limit=12), runs=desk.runs(ws, 8), totals=desk.totals(ws),
                    max_mb=settings.max_upload_mb, accept=ACCEPT["all"])

    @app.get("/files")
    def files_page(request: Request, q: str = "", kind: str = "", sort: str = "new", starred: int = 0):
        ws = request.state.ws
        kind = kind if kind in ("pdf", "image", "office", "text", "zip") else ""
        rows = desk.files(ws, q=q, kind=kind, sort=sort, starred=bool(starred), limit=1000)
        return page(request, "files.html", rows=rows, q=q, kind=kind, sort=sort, starred=starred,
                    totals=desk.totals(ws), max_mb=settings.max_upload_mb, cleanup_days=CLEANUP_DAYS,
                    accept=ACCEPT["all"])

    @app.get("/tools/{tool_id}")
    def tool_page(request: Request, tool_id: str, files: str = ""):
        tool = tools.TOOLS.get(tool_id)
        if tool is None:
            raise StarletteHTTPException(404, "There is no such tool.")
        ws = request.state.ws
        chosen = desk.files(ws, ids=ids_of(files))
        if tool.page == "edit" and len(chosen) == 1 and chosen[0]["kind"] == "pdf":
            return RedirectResponse(f"/edit/{chosen[0]['id']}", status_code=303)
        if tool.page == "organize" and chosen and all(f["kind"] == "pdf" for f in chosen):
            return RedirectResponse(f"/organize?files={','.join(f['id'] for f in chosen)}", status_code=303)
        report = caps()
        return page(request, "tool.html", tool=tool, group=tools.GROUPS[tool.group], chosen=chosen,
                    chosen_json=script_json(chosen), languages=report["tesseract"]["languages"],
                    language_names=capabilities.LANGUAGES, report=report, fonts=tools.FONTS,
                    paper=list(tools.PAPER), number_formats=tools.NUMBER_FORMATS,
                    permissions={k: v[0] for k, v in tools.PERMISSIONS.items()},
                    redact_patterns={k: v[0] for k, v in tools.REDACT_PATTERNS.items()}, sanitize=tools.SANITIZE,
                    ai_providers=ai.PROVIDERS, ai_models=ai.MODELS, ai_models_json=script_json(ai.MODELS),
                    server_keys={p: bool(ai.USE_SERVER_KEYS and os.environ.get(k))
                                 for p, k in ai.ENV_KEYS.items()},
                    max_mb=settings.max_upload_mb, accept=ACCEPT[tool.accepts])

    @app.get("/organize")
    def organize_page(request: Request, files: str = ""):
        ws = request.state.ws
        chosen = [desk.details(ws, i) for i in ids_of(files)]
        chosen = [f for f in chosen if f and f["kind"] == "pdf" and not f["locked"]]
        if not chosen:
            return RedirectResponse("/tools/organize", status_code=303)
        return page(request, "organize.html", chosen=chosen, chosen_json=script_json(chosen))

    @app.get("/edit/{file_id}")
    def edit_page(request: Request, file_id: str):
        ws = request.state.ws
        row = desk.details(ws, file_id)
        if row is None or row["kind"] != "pdf":
            raise StarletteHTTPException(404, "That PDF is no longer in My files.")
        if row["locked"]:
            return RedirectResponse(f"/tools/unlock?files={file_id}", status_code=303)
        langs = caps()["tesseract"].get("languages") or []
        return page(request, "edit.html", file=row, file_json=script_json(row), font_groups=fonts.groups(),
                    fonts_json=script_json(fonts.browser_map()), images=desk.files(ws, kind="image", limit=60),
                    ocr_languages=[(code, capabilities.language_name(code)) for code in langs],
                    ocr_default="ben+eng" if {"ben", "eng"} <= set(langs) else (langs[0] if langs else ""))

    @app.get("/fonts.css")
    def fonts_css():
        return Response(fonts.stylesheet(), media_type="text/css; charset=utf-8",
                        headers={"Cache-Control": "public, max-age=86400"})

    @app.get("/fonts/{name}")
    def font_file(name: str):
        path = fonts.bundled_file(name)
        if path is None:
            raise StarletteHTTPException(404, "No such font.")
        return FileResponse(path, media_type="font/ttf", headers={"Cache-Control": "public, max-age=604800"})

    @app.get("/runs/{run_id}")
    def run_page(request: Request, run_id: str):
        run = desk.run(request.state.ws, run_id)
        if run is None:
            raise StarletteHTTPException(404, "That run is not known (on a public server, runs are kept for a day).")
        return page(request, "run.html", run=run, tool=tools.TOOLS.get(run["tool"]), run_json=script_json(run))

    @app.get("/settings")
    def settings_page(request: Request):
        report = caps()
        installed = set(report["tesseract"]["languages"])
        offer = [(code, name) for code, name in capabilities.LANGUAGES.items() if code not in installed]
        return page(request, "settings.html", report=report, offer=offer, language_names=capabilities.LANGUAGES,
                    ai_providers=ai.PROVIDERS, ai_models=ai.MODELS, totals=desk.totals(request.state.ws),
                    data_dir=str(settings.data_dir) if not settings.public else "")

    @app.get("/developers")
    def developers_page(request: Request):
        base = str(request.base_url).rstrip("/")
        return page(request, "developers.html", base=base, tool_list=list(tools.TOOLS.values()))

    @app.get("/login")
    def login_page(request: Request, next: str = "/", error: int = 0):
        if not settings.password:
            return RedirectResponse("/", status_code=303)
        return templates.TemplateResponse(request, "login.html", {"next": next if next.startswith("/") and
                                                                   not next.startswith("//") else "/",
                                                                   "error": bool(error)})

    failed: dict[str, list[float]] = {}

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        target = str(form.get("next") or "/")
        target = target if target.startswith("/") and not target.startswith("//") else "/"
        who = request.client.host if request.client else "?"
        recent = [t for t in failed.get(who, []) if t > time.time() - 600]
        if len(recent) >= 10:
            return Response("Too many wrong passwords: wait 10 minutes.", 429)
        if not settings.password or not hmac.compare_digest(str(form.get("password", "")), settings.password):
            if len(failed) > 10000:                       # many addresses: forget the oldest attempts
                failed.clear()
            failed[who] = recent + [time.time()]
            await asyncio.sleep(0.6)
            return RedirectResponse(f"/login?error=1&next={quote(target)}", status_code=303)
        failed.pop(who, None)
        response = RedirectResponse(target, status_code=303)
        response.set_cookie(AUTH_COOKIE, auth_value, max_age=86400 * 30, **cookie_args())
        return response

    @app.post("/logout")
    def logout():
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(AUTH_COOKIE, path="/")
        return response

    # ------------------------------------------------------------------ files
    @app.get("/files/{file_id}/download")
    def download(request: Request, file_id: str, inline: int = 0):
        row = file_or_404(request, file_id)
        path = desk.path(row)
        if not path.is_file():
            raise StarletteHTTPException(404, "The file is missing on disk.")
        suffix = Path(row["name"]).suffix.lower()
        if row["kind"] == "text":
            media = "text/plain; charset=utf-8"         # never served as HTML: an uploaded page cannot run here
        else:
            media = MEDIA.get(row["kind"]) or MEDIA.get(suffix) or "application/octet-stream"
        show = bool(inline) and row["kind"] in ("pdf", "image", "text")
        return FileResponse(path, media_type=media, filename=row["name"],
                            content_disposition_type="inline" if show else "attachment",
                            headers={"Content-Security-Policy": DOWNLOAD_CSP.get(row["kind"], DOWNLOAD_CSP["other"]),
                                     "Cache-Control": "private, no-store"})

    @app.get("/files/{file_id}/page/{number}.png")
    async def page_png(request: Request, file_id: str, number: int, w: int = 240):
        file_or_404(request, file_id)
        try:
            png = await run_in_threadpool(desk.thumb, request.state.ws, file_id, number - 1, w)
        except Exception:  # missing page, locked, or a page MuPDF cannot draw: the grid shows an empty tile
            raise StarletteHTTPException(404)
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=86400",
                                                              "Content-Security-Policy": DOWNLOAD_CSP["other"]})

    @app.get("/zip")
    async def zip_files(request: Request, ids: str = "", name: str = ""):
        rows = desk.files(request.state.ws, ids=ids_of(ids))
        if not rows:
            raise StarletteHTTPException(404, "Nothing to download.")
        target = desk.work_dir / f"zip-{secrets.token_hex(8)}.zip"

        def build() -> None:
            seen: set[str] = set()
            with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
                for row in rows:
                    fname, n = row["name"], 2
                    while fname in seen:
                        fname = f"{Path(row['name']).stem} ({n}){Path(row['name']).suffix}"
                        n += 1
                    seen.add(fname)
                    z.write(desk.path(row), fname)
        await run_in_threadpool(build)
        filename = (tools._slug(name, 80) if name else "PDF files") + ".zip"
        return FileResponse(target, media_type="application/zip", filename=filename,
                            background=BackgroundTask(target.unlink, True))

    # ------------------------------------------------------------------ JSON API (the pages)
    @app.post("/api/files")
    async def api_upload(request: Request):
        form = await request.form(max_files=300, max_fields=50)
        uploads = [f for f in form.getlist("files") if isinstance(f, UploadFile) and f.filename]
        if not uploads:
            return JSONResponse({"error": "Choose files to add."}, status_code=400)
        added, errors = [], []
        for upload in uploads[:300]:
            tmp = desk.work_dir / f"up-{secrets.token_hex(8)}"
            try:
                await save_upload(upload, tmp)
                added.append(await run_in_threadpool(desk.add, request.state.ws, tmp, upload.filename or "file"))
            except (ValueError, tools.ToolError) as exc:
                errors.append(str(exc))
            finally:
                tmp.unlink(missing_ok=True)
        return {"files": added, "errors": errors}

    @app.post("/api/files/text")
    async def api_typed(request: Request):
        """Text typed or pasted on the Text → PDF page, kept as a .md / .txt / .html file."""
        body = await json_body(request)
        text = str(body.get("text", ""))
        if not text.strip():
            return JSONResponse({"error": "Type or paste some text first."}, status_code=400)
        if len(text) > 5_000_000:
            return JSONResponse({"error": "That is too much text at once."}, status_code=400)
        ext = {"markdown": ".md", "html": ".html"}.get(str(body.get("format", "")), ".txt")
        name = clean_name(str(body.get("name") or "Typed text"))
        name = name if name.lower().endswith(ext) else name + ext
        tmp = desk.work_dir / f"typed-{secrets.token_hex(8)}"
        tmp.write_text(text, encoding="utf-8")
        try:
            row = await run_in_threadpool(desk.add, request.state.ws, tmp, name, origin="typed", kind="text")
        except tools.ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        finally:
            tmp.unlink(missing_ok=True)
        return {"file": row}

    @app.get("/api/files")
    def api_files(request: Request, kind: str = "", q: str = "", ids: str = ""):
        ws = request.state.ws
        if ids:
            return {"files": desk.files(ws, ids=ids_of(ids))}
        kind = kind if kind in ("pdf", "image", "office", "text", "zip") else ""
        return {"files": desk.files(ws, kind=kind, q=q, limit=500)}

    @app.get("/api/files/{file_id}")
    def api_file(request: Request, file_id: str):
        row = desk.details(request.state.ws, file_id)
        if row is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return row

    @app.post("/api/files/{file_id}")
    async def api_file_update(request: Request, file_id: str):
        body = await json_body(request)
        ws = request.state.ws
        file_or_404(request, file_id)
        if "name" in body and str(body["name"]).strip():
            desk.rename(ws, file_id, str(body["name"]))
        if "starred" in body:
            desk.star(ws, file_id, bool(body["starred"]))
        return {"file": desk.get(ws, file_id)}

    @app.get("/api/files/{file_id}/text/{number}")
    async def api_text_blocks(request: Request, file_id: str, number: int):
        """The paragraphs of one page, for the editor's "Edit text" tool."""
        row = file_or_404(request, file_id)
        if row["kind"] != "pdf" or row["locked"]:
            return JSONResponse({"error": "Not an open PDF."}, status_code=400)
        from .edit_text import page_blocks

        def read() -> dict:
            import pymupdf
            with pymupdf.open(desk.path(row), filetype="pdf") as doc:
                if not 1 <= number <= doc.page_count:
                    raise IndexError(number)
                return page_blocks(doc, number - 1)
        try:
            return await run_in_threadpool(read)
        except IndexError:
            return JSONResponse({"error": "No such page."}, status_code=404)

    ocr_gate = threading.BoundedSemaphore(max(1, min(4, (os.cpu_count() or 2) // 2)))

    @app.post("/api/files/{file_id}/ocr/{number}")
    async def api_ocr_blocks(request: Request, file_id: str, number: int):
        """Read one page that is a picture with OCR into editable paragraphs (the editor's "Read text")."""
        row = file_or_404(request, file_id)
        if row["kind"] != "pdf" or row["locked"]:
            return JSONResponse({"error": "Not an open PDF."}, status_code=400)
        body = await json_body(request)
        language = str(body.get("language") or "")[:100]
        from .edit_text import ocr_blocks

        def read() -> dict:
            import pymupdf
            if not ocr_gate.acquire(timeout=180):
                raise tools.ToolError("OCR is busy with other pages: try again in a minute.")
            try:
                with pymupdf.open(desk.path(row), filetype="pdf") as doc:
                    if not 1 <= number <= doc.page_count:
                        raise IndexError(number)
                    return ocr_blocks(doc, number - 1, language)
            finally:
                ocr_gate.release()
        try:
            return await run_in_threadpool(read)
        except IndexError:
            return JSONResponse({"error": "No such page."}, status_code=404)
        except tools.ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)

    @app.post("/api/delete")
    async def api_delete(request: Request):
        body = await json_body(request)
        ws = request.state.ws
        if body.get("everything"):
            ids = [f["id"] for f in desk.files(ws, limit=100000)]
        elif body.get("older_than"):
            try:
                days = float(body["older_than"])
            except (TypeError, ValueError):
                days = 30
            ids = desk.old_files(ws, max(0.0, days))
        else:
            ids = ids_of(body.get("ids"))
        return {"removed": desk.delete(ws, ids)}

    @app.post("/api/run")
    async def api_run(request: Request):
        body = await json_body(request)
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        try:
            run_id = desk.start(request.state.ws, str(body.get("tool", "")), ids_of(body.get("files")), params)
        except QuotaError as exc:
            return JSONResponse({"error": str(exc)}, status_code=429)
        except tools.ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"run": run_id}

    @app.get("/api/runs/{run_id}")
    def api_run_status(request: Request, run_id: str):
        run = desk.run(request.state.ws, run_id)
        if run is None:
            return JSONResponse({"error": "not found"}, status_code=404)
        return run

    @app.post("/api/preview")
    async def api_preview(request: Request):
        body = await json_body(request)
        ws = request.state.ws
        params = body.get("params") if isinstance(body.get("params"), dict) else {}
        tool_id = str(body.get("tool", ""))
        rows = desk.files(ws, ids=ids_of(body.get("files")))
        if not rows:
            return JSONResponse({"error": "Choose a PDF first."}, status_code=400)
        try:
            index = int(body.get("page", 1)) - 1
            width = int(body.get("width", 700))
        except (TypeError, ValueError):
            index, width = 0, 700

        def make() -> bytes:
            extras = desk.resolve_extras(ws, tool_id, params)
            return tools.preview(tool_id, [desk.input(r) for r in rows], params, index, width, extras)
        try:
            png = await run_in_threadpool(make)
        except tools.ToolError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return Response(png, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.get("/api/capabilities")
    def api_capabilities():
        report = caps()
        report["tesseract"].pop("path", None) if settings.public else None
        report["libreoffice"].pop("path", None) if settings.public else None
        return report

    @app.post("/api/ocr/languages")
    async def api_add_language(request: Request):
        if not settings.allow_downloads:
            return JSONResponse({"error": "Adding OCR languages is turned off on this server (its owner can add "
                                          "them)."}, status_code=403)
        body = await json_body(request)
        code = str(body.get("code", "")).strip()
        quality = "fast" if body.get("quality") == "fast" else "best"
        try:
            await run_in_threadpool(ocr.TESSDATA.download, code, quality)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"languages": ocr.TESSDATA.languages(), "added": code}

    # ------------------------------------------------------------------ REST API v1 (stateless)
    def api_allowed(request: Request) -> bool:
        if not settings.api_key:
            return signed_in(request) or not settings.password
        given = request.headers.get("authorization", "")
        return hmac.compare_digest(given, f"Bearer {settings.api_key}")

    @app.get("/api/v1/tools")
    def v1_tools(request: Request):
        if not api_allowed(request):
            return JSONResponse({"error": "Send Authorization: Bearer <API key>."}, status_code=401)
        report = {"tesseract": bool(capabilities.tesseract_path()), "libreoffice": bool(capabilities.libreoffice_path())}
        return {"version": __version__, "tools": [
            {"id": t.id, "label": t.label, "group": t.group, "description": t.blurb, "accepts": t.accepts,
             "min_files": t.min_files, "max_files": t.max_files, "available": report.get(t.needs, True),
             "endpoint": f"/api/v1/tools/{t.id}"} for t in tools.TOOLS.values() if t.page != "organize"]}

    @app.post("/api/v1/tools/{tool_id}")
    async def v1_run(request: Request, tool_id: str):
        """multipart/form-data: files (1+), params (JSON object) or plain fields, image_file / source_file for the
        tools that take a second file. Answers with the result file, or a ZIP when there are several."""
        if not api_allowed(request):
            return JSONResponse({"error": "Send Authorization: Bearer <API key>."}, status_code=401)
        tool = tools.TOOLS.get(tool_id)
        if tool is None or tool.page == "organize":
            return JSONResponse({"error": "Unknown tool. GET /api/v1/tools lists them."}, status_code=404)
        try:
            form = await request.form(max_files=300, max_fields=200)
        except Exception:
            return JSONResponse({"error": "Send the files as multipart/form-data."}, status_code=400)
        work = desk.work_dir / f"api-{secrets.token_hex(8)}"
        work.mkdir(parents=True)

        def cleanup() -> None:
            import shutil
            shutil.rmtree(work, ignore_errors=True)

        try:
            params: dict[str, Any] = {}
            if form.get("params"):
                try:
                    loaded = json.loads(str(form.get("params")))
                except ValueError:
                    raise tools.ToolError("params is not valid JSON.")
                if not isinstance(loaded, dict):
                    raise tools.ToolError("params must be a JSON object.")
                params.update(loaded)
            for key, value in form.multi_items():
                if isinstance(value, str) and key not in ("params",):
                    params.setdefault(key, value)
            inputs, extras = [], {}
            for key, value in form.multi_items():
                if not isinstance(value, UploadFile) or not value.filename:
                    continue
                target = work / f"in-{len(inputs) + len(extras)}{Path(value.filename).suffix.lower()[:10]}"
                await save_upload(value, target)
                with target.open("rb") as handle:
                    kind = tools.kind_of(value.filename, handle.read(4096))
                if kind is None:
                    raise tools.ToolError(f"{value.filename}: this kind of file is not accepted.")
                item = tools.Input(target, clean_name(value.filename), kind)
                if key in ("image_file", "source_file"):
                    extras[key] = item
                    params[key] = "api"
                else:
                    inputs.append(item)
            async with api_slots:
                result = await run_in_threadpool(tools.run, tool_id, inputs, params, work / "out", None, extras)
        except tools.ToolError as exc:
            cleanup()
            return JSONResponse({"error": str(exc)}, status_code=400)
        except Exception as exc:  # unexpected: logged, short message back
            cleanup()
            log.exception("API tool %s failed", tool_id)
            return JSONResponse({"error": f"Something went wrong: {exc}"}, status_code=500)
        headers = {"X-PHIPDF-Summary": quote(result.summary)[:2000], "Cache-Control": "no-store"}
        if len(result.outputs) == 1:
            out = result.outputs[0]
            suffix = out.path.suffix.lower()
            media = MEDIA.get("pdf" if suffix == ".pdf" else suffix) or ("text/plain; charset=utf-8"
                                                                        if suffix in (".txt", ".md") else
                                                                        "application/octet-stream")
            return FileResponse(out.path, media_type=media, filename=out.name, headers=headers,
                                background=BackgroundTask(cleanup))
        bundle = work / "result.zip"
        with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
            seen: set[str] = set()
            for out in result.outputs:
                name, n = out.name, 2
                while name in seen:
                    name = f"{Path(out.name).stem} ({n}){Path(out.name).suffix}"
                    n += 1
                seen.add(name)
                z.write(out.path, name)
        return FileResponse(bundle, media_type="application/zip", filename=f"{tool_id}-result.zip", headers=headers,
                            background=BackgroundTask(cleanup))

    # ------------------------------------------------------------------ errors, housekeeping
    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": exc.detail or "Not found."}, status_code=exc.status_code)
        if not hasattr(request.state, "ws"):
            return Response(str(exc.detail), status_code=exc.status_code)
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail != "Not Found" else ""
        response = page(request, "error.html", status=exc.status_code,
                        message=detail or ("This page does not exist." if exc.status_code == 404 else "Something "
                                           "went wrong."))
        response.status_code = exc.status_code
        return response

    if settings.keep_hours:
        stop = threading.Event()

        def sweeper() -> None:
            while not stop.wait(300):
                try:
                    removed = desk.cleanup()
                    if removed:
                        log.info("deleted %d old file(s)", removed)
                except Exception:  # never let the sweeper die
                    log.exception("cleanup failed")
        threading.Thread(target=sweeper, name="cleanup", daemon=True).start()
        app.state.stop_sweeper = stop

    return app


def script_json(data: Any) -> str:
    """JSON safe inside <script type="application/json">."""
    return json.dumps(data, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
