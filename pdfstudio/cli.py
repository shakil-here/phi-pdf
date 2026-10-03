"""`phi-pdf`: open the app, serve it to many people, or run any tool straight from the terminal.

    phi-pdf                       open the app on this computer (picks a free port, opens the browser)
    phi-pdf serve [--public]      a server (public mode: private workspaces, files deleted after 2 h)
    phi-pdf tools                 list the tools
    phi-pdf run TOOL FILES… -o DIR [--set key=value]…
    phi-pdf doctor                what is installed (OCR, LibreOffice…) and how to add the rest
    phi-pdf add-language ben      download an OCR language
    phi-pdf shortcut              put the app's icon on the Desktop (one click opens it)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import time
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__


def _free_port(host: str, wanted: int) -> int:
    for port in [wanted] + list(range(wanted + 1, wanted + 40)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind((host, port))
                return port
            except OSError:
                continue
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _serve(settings, open_browser: bool) -> None:
    import uvicorn

    from .web import create_app

    app = create_app(settings)
    shown = "127.0.0.1" if settings.host in ("0.0.0.0", "::") else settings.host
    url = f"http://{shown}:{settings.port}/"
    print(f"\n  {APP_NAME} {__version__}  ·  {'public server' if settings.public else 'on this computer'}")
    print(f"  Open {url}")
    print(f"  Files: {settings.data_dir}")
    if settings.public:
        print(f"  Files are deleted after {settings.keep_hours:g} h · uploads up to {settings.max_upload_mb} MB"
              + (" · password required" if settings.password else ""))
    print("  Stop with Ctrl+C\n")
    if open_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    if not settings.public:                    # so the desktop icon finds it instead of starting a second copy
        from .desktop import mark_running
        threading.Timer(0.8, lambda: mark_running(settings.data_dir, settings.port)).start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning", proxy_headers=settings.public,
                    forwarded_allow_ips=os.environ.get("FORWARDED_ALLOW_IPS", "127.0.0.1"))
    finally:
        if not settings.public:
            from .desktop import mark_stopped
            mark_stopped(settings.data_dir)


def cmd_start(args) -> None:
    from .config import Settings

    from .desktop import running_url

    settings = Settings.load("local", data_dir=Path(args.data).expanduser().resolve() if args.data else None)
    already = None if args.port else running_url(settings.data_dir)
    if already:
        print(f"{APP_NAME} is already running: {already}")
        if not args.no_browser:
            webbrowser.open(already)
        return
    settings.port = _free_port(settings.host, args.port or settings.port)
    _serve(settings, open_browser=not args.no_browser)


def _local_data(args) -> Path:
    from .config import Settings

    return Path(args.data).expanduser().resolve() if args.data else Settings.load("local").data_dir


def cmd_launch(args) -> None:
    from .desktop import launch

    sys.exit(launch(_local_data(args)))


def cmd_shortcut(args) -> None:
    from .desktop import install_shortcuts

    try:
        made = install_shortcuts(_local_data(args))
    except OSError as exc:
        sys.exit(f"✗ {exc}")
    for path in made:
        print(f"  ✓ {path}")
    print(f"Double-click “{APP_NAME}” on the Desktop to open the app.")


def cmd_serve(args) -> None:
    from .config import Settings

    settings = Settings.load("server" if args.public else "local",
                             host=args.host, port=args.port,
                             data_dir=Path(args.data).expanduser().resolve() if args.data else None)
    if not args.public and settings.host not in ("127.0.0.1", "localhost", "::1"):
        print("Listening beyond this computer needs --public (private workspaces, size limits, files deleted "
              "after a while) — or set PHIPDF_PASSWORD and use --public.", file=sys.stderr)
        sys.exit(2)
    _serve(settings, open_browser=False)


def cmd_tools(args) -> None:
    from . import capabilities, tools

    ready = {"tesseract": bool(capabilities.tesseract_path()), "libreoffice": bool(capabilities.libreoffice_path())}
    for gid, label in tools.GROUPS.items():
        print(f"\n{label}")
        for t in tools.TOOLS.values():
            if t.group == gid:
                flag = "" if ready.get(t.needs, True) else f"   (needs {t.needs})"
                print(f"  {t.id:<15} {t.label}{flag}")
    print("\nOptions: see each tool's page in the app (“Use this from code”), or the README.")


def _value(raw: str):
    low = raw.lower()
    if low in ("on", "true", "yes"):
        return True
    if low in ("off", "false", "no"):
        return False
    if raw[:1] in "[{":
        try:
            return json.loads(raw)
        except ValueError:
            pass
    return raw


def cmd_run(args) -> None:
    from . import tools
    from .config import Settings

    settings = Settings.load("local")
    _ready_engines(settings)
    tool = tools.TOOLS.get(args.tool)
    if tool is None:
        sys.exit(f"Unknown tool “{args.tool}”. See: phi-pdf tools")
    params: dict = {}
    for item in args.set or []:
        key, sep, value = item.partition("=")
        if not sep:
            sys.exit(f"--set needs key=value, not “{item}”.")
        params[key.strip()] = _value(value)
    if args.password:
        params["password"] = args.password
    if isinstance(params.get("language"), str):
        params["language"] = params["language"].split("+")
    if isinstance(params.get("find"), str):
        params["find"] = params["find"].split("+")
    inputs, extras = [], {}

    def item(path_text: str) -> tools.Input:
        path = Path(path_text).expanduser()
        if not path.is_file():
            sys.exit(f"No such file: {path}")
        with path.open("rb") as handle:
            kind = tools.kind_of(path.name, handle.read(4096))
        if kind is None:
            sys.exit(f"{path.name}: this kind of file is not accepted.")
        return tools.Input(path, path.name, kind)

    inputs = [item(p) for p in args.files]
    if args.image_file:
        extras["image_file"], params["image_file"] = item(args.image_file), "cli"
    if args.source_file:
        extras["source_file"], params["source_file"] = item(args.source_file), "cli"
    out = Path(args.output).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="phipdf-run-") as tmp:
        last = [0.0]

        def progress(done: int, total: int, label: str) -> None:
            if time.time() - last[0] > 0.4 or done == total:
                last[0] = time.time()
                print(f"\r  {label} {done}/{total}".ljust(60), end="", file=sys.stderr, flush=True)
        try:
            result = tools.run(args.tool, inputs, params, Path(tmp), progress, extras)
        except tools.ToolError as exc:
            print(file=sys.stderr)
            sys.exit(f"✗ {exc}")
        print("\r".ljust(62), end="\r", file=sys.stderr)
        for o in result.outputs:
            target = out / o.name
            n = 2
            while target.exists() and not args.overwrite:
                target = out / f"{Path(o.name).stem} ({n}){Path(o.name).suffix}"
                n += 1
            shutil.move(str(o.path), target)
            print(f"  → {target}")
    print(f"✓ {result.summary}")


def _ready_engines(settings) -> None:
    from . import ai, capabilities, ocr

    ocr.TESSDATA = capabilities.Tessdata(settings.data_dir / "tessdata")
    ai.USE_SERVER_KEYS = True


def cmd_doctor(args) -> None:
    from . import capabilities, ocr
    from .config import Settings

    _ready_engines(Settings.load("local"))
    r = capabilities.report(ocr.TESSDATA)
    ok = lambda b: "✓" if b else "✗"  # noqa: E731
    print(f"{APP_NAME} {__version__} · {r['platform']} · Python {r['python']} · PyMuPDF {r['pymupdf']}\n")
    print(f"{ok(True)} PDF engine (every tool)")
    t = r["tesseract"]
    print(f"{ok(t['ok'])} Tesseract OCR {t['version']}  {t['path']}")
    if t["ok"]:
        print(f"    languages: {', '.join(t['languages']) or 'none'}   (add more: phi-pdf add-language ben)")
    else:
        print(f"    install: {t['install']}")
    lo = r["libreoffice"]
    print(f"{ok(lo['ok'])} LibreOffice (Office → PDF)  {lo['path']}")
    if not lo["ok"]:
        print(f"    install: {lo['install']}")
    print(f"{ok(r['exact_word'])} pdf2docx (PDF → Word keeping the layout)" + ("" if r["exact_word"] else
                                                                              "   install: pip install pdf2docx"))
    keys = [k for k in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(k)]
    print(f"{ok(bool(keys))} AI reading keys in the environment: {', '.join(keys) or 'none (they can be typed in the app)'}")


def cmd_add_language(args) -> None:
    from . import capabilities, ocr
    from .config import Settings

    _ready_engines(Settings.load("local"))
    if not capabilities.tesseract_path():
        sys.exit(f"Install Tesseract first: {capabilities.INSTALL['tesseract'][capabilities.SYSTEM]}")
    for code in args.codes:
        try:
            path = ocr.TESSDATA.download(code, "fast" if args.fast else "best")
        except ValueError as exc:
            sys.exit(f"✗ {code}: {exc}")
        print(f"✓ {code} → {path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="phi-pdf", description=f"{APP_NAME}: free, open-source PDF tools.")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="open the app on this computer (the default)")
    start.add_argument("--port", type=int, default=0)
    start.add_argument("--data", help="folder for files (default: your user data folder)")
    start.add_argument("--no-browser", action="store_true")
    start.set_defaults(func=cmd_start)

    serve = sub.add_parser("serve", help="run as a server")
    serve.add_argument("--public", action="store_true", help="for many people: private workspaces, limits, clean-up")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--data", help="folder for files (default ./data in public mode)")
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("tools", help="list the tools").set_defaults(func=cmd_tools)

    run = sub.add_parser("run", help="run a tool on files")
    run.add_argument("tool")
    run.add_argument("files", nargs="+")
    run.add_argument("-o", "--output", default=".", help="folder for the results")
    run.add_argument("--set", action="append", metavar="KEY=VALUE", help="a tool option (repeat it)")
    run.add_argument("--password", help="for protect / unlock")
    run.add_argument("--image-file", help="watermark picture")
    run.add_argument("--source-file", help="the file to insert (insert tool)")
    run.add_argument("--overwrite", action="store_true")
    run.set_defaults(func=cmd_run)

    sub.add_parser("doctor", help="what is installed").set_defaults(func=cmd_doctor)
    lang = sub.add_parser("add-language", help="download OCR languages (Tesseract codes: ben hin ara…)")
    lang.add_argument("codes", nargs="+")
    lang.add_argument("--fast", action="store_true", help="smaller, faster, a little less accurate")
    lang.set_defaults(func=cmd_add_language)

    launch = sub.add_parser("launch", help="open the app, starting it in the background if needed (the icon runs this)")
    launch.add_argument("--data", help="folder for files (default: your user data folder)")
    launch.set_defaults(func=cmd_launch)
    shortcut = sub.add_parser("shortcut", help="put the app's icon on the Desktop")
    shortcut.add_argument("--data", help="folder for files (default: your user data folder)")
    shortcut.set_defaults(func=cmd_shortcut)

    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        args = parser.parse_args(["start"] + (argv or []))
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
