"""A desktop icon that opens the app with one click (`phi-pdf shortcut`), and what that icon runs (`phi-pdf launch`).

- macOS: "PHI PDF Studio.app" on the Desktop and in ~/Applications (it can be dragged to the Dock).
- Windows: "PHI PDF Studio" shortcuts on the Desktop and in the Start menu (no console window).
- Linux: a .desktop entry in the applications menu and on the Desktop.

The icon runs `launch`: when the app is already running (on this computer, for this user) it only opens the browser
at it; otherwise it starts it in the background and then opens the browser. The running app is noted in
<data>/running.json, so a second click never starts a second copy.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import APP_NAME, __version__

HERE = Path(__file__).resolve().parent
ICON_SVG = HERE / "static" / "favicon.svg"
BUNDLE_ID = "org.physicshunters.phipdfstudio"


# ---------------------------------------------------------------------------------------------- the running app
def _note(data_dir: Path) -> Path:
    return data_dir / "running.json"


def mark_running(data_dir: Path, port: int) -> None:
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        _note(data_dir).write_text(json.dumps({"port": port, "pid": os.getpid(), "version": __version__}))
    except OSError:
        pass


def mark_stopped(data_dir: Path) -> None:
    try:
        note = json.loads(_note(data_dir).read_text())
        if note.get("pid") == os.getpid():
            _note(data_dir).unlink()
    except (OSError, ValueError):
        pass


def running_url(data_dir: Path) -> str | None:
    """The address of this user's app when it is running and answers, else None."""
    try:
        port = int(json.loads(_note(data_dir).read_text())["port"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    url = f"http://127.0.0.1:{port}/"
    try:
        with urllib.request.urlopen(url + "healthz", timeout=1.5) as reply:
            return url if json.loads(reply.read().decode()).get("ok") else None
    except (OSError, ValueError):
        return None


def launch(data_dir: Path) -> int:
    """Open the app in the browser, starting it in the background first when it is not running."""
    url = running_url(data_dir)
    if url is None:
        log = data_dir / "app.log"
        data_dir.mkdir(parents=True, exist_ok=True)
        cmd = [_python(console=False), "-m", "pdfstudio", "start", "--no-browser", "--data", str(data_dir)]
        options: dict = {"stdin": subprocess.DEVNULL, "close_fds": True}
        if os.name == "nt":
            options["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000   # detached, own group, no window
        else:
            options["start_new_session"] = True
        with open(log, "ab") as out:
            subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, **options)
        deadline = time.time() + 60
        while url is None and time.time() < deadline:
            time.sleep(0.4)
            url = running_url(data_dir)
        if url is None:
            print(f"{APP_NAME} did not start: see {log}", file=sys.stderr)
            return 1
    webbrowser.open(url)
    return 0


def _python(console: bool = True) -> str:
    """This Python (on Windows its window-less pythonw.exe for the icon)."""
    exe = Path(sys.executable)
    if os.name == "nt" and not console:
        quiet = exe.with_name("pythonw.exe")
        if quiet.exists():
            return str(quiet)
    return str(exe)


# ---------------------------------------------------------------------------------------------- the icon
def icon_png(size: int, margin: float = 0.0) -> bytes:
    """The app's icon (static/favicon.svg) as a transparent PNG, with an empty margin (a share of the size)."""
    import pymupdf
    from PIL import Image

    inner = max(8, round(size * (1 - 2 * margin)))
    with pymupdf.open(ICON_SVG) as svg:
        page = svg[0]
        k = inner / max(page.rect.width, page.rect.height)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(k, k), alpha=True)
    picture = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGBA")
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(picture, ((size - picture.width) // 2, (size - picture.height) // 2), picture)
    out = io.BytesIO()
    canvas.save(out, "PNG")
    return out.getvalue()


def write_icon(path: Path) -> Path:
    """The icon as .icns (macOS: with the margin macOS icons have), .ico (Windows) or .png (Linux)."""
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    suffix = path.suffix.lower()
    if suffix == ".png":
        path.write_bytes(icon_png(256))
        return path
    picture = Image.open(io.BytesIO(icon_png(1024, 0.1 if suffix == ".icns" else 0.0)))
    if suffix == ".icns":
        picture.save(path, "ICNS")
    else:
        picture.save(path, "ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    return path


# ---------------------------------------------------------------------------------------------- shortcuts
def install_shortcuts(data_dir: Path) -> list[Path]:
    """Put the app's icon on the Desktop (and in the system's app list). Returns what was made."""
    if sys.platform == "darwin":
        return _mac(data_dir)
    if os.name == "nt":
        return _windows(data_dir)
    return _linux(data_dir)


def _launch_command(data_dir: Path, console: bool = True) -> list[str]:
    return [_python(console), "-m", "pdfstudio", "launch", "--data", str(data_dir)]


def _desktop() -> Path:
    if os.name == "nt":
        try:                                      # the real Desktop (OneDrive may have moved it)
            found = subprocess.run(["powershell", "-NoProfile", "-Command", "[Environment]::GetFolderPath('Desktop')"],
                                   capture_output=True, text=True, timeout=30).stdout.strip()
            if found:
                return Path(found)
        except (OSError, subprocess.SubprocessError):
            pass
    else:
        try:                                      # Linux: the Desktop in the user's own language
            found = subprocess.run(["xdg-user-dir", "DESKTOP"], capture_output=True, text=True, timeout=10).stdout.strip()
            if found and Path(found) != Path.home():
                return Path(found)
        except (OSError, subprocess.SubprocessError):
            pass
    return Path.home() / "Desktop"


def _mac(data_dir: Path) -> list[Path]:
    import shlex
    import plistlib

    made = []
    for folder in (Path.home() / "Applications", _desktop()):
        app = folder / f"{APP_NAME}.app"
        if app.exists():
            shutil.rmtree(app)
        contents = app / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        write_icon(contents / "Resources" / "AppIcon.icns")
        runner = contents / "MacOS" / "phi-pdf-studio"
        runner.write_text("#!/bin/bash\n# Opens PHI PDF Studio (starts it in the background when it is not running).\n"
                          f"exec {shlex.join(_launch_command(data_dir))} >/dev/null 2>&1\n")
        runner.chmod(0o755)
        with open(contents / "Info.plist", "wb") as plist:
            plistlib.dump({"CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME, "CFBundleIdentifier": BUNDLE_ID,
                           "CFBundleVersion": __version__, "CFBundleShortVersionString": __version__,
                           "CFBundlePackageType": "APPL", "CFBundleExecutable": runner.name,
                           "CFBundleIconFile": "AppIcon", "LSUIElement": True, "NSHighResolutionCapable": True}, plist)
        os.utime(app)                             # Finder shows the new icon
        made.append(app)
    return made


def _windows(data_dir: Path) -> list[Path]:
    icon = write_icon(data_dir / "icon.ico")
    target, *args = _launch_command(data_dir, console=False)
    start_menu = Path(os.environ.get("APPDATA", Path.home())) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    made = []
    for folder in (_desktop(), start_menu):
        link = folder / f"{APP_NAME}.lnk"
        quote = lambda s: "'" + str(s).replace("'", "''") + "'"  # noqa: E731
        script = ("$s = (New-Object -ComObject WScript.Shell).CreateShortcut(" + quote(link) + "); "
                  f"$s.TargetPath = {quote(target)}; "
                  f"$s.Arguments = {quote(subprocess.list2cmdline(args))}; "
                  f"$s.IconLocation = {quote(str(icon) + ',0')}; "
                  f"$s.WorkingDirectory = {quote(Path.home())}; "
                  f"$s.Description = {quote(APP_NAME)}; $s.Save()")
        folder.mkdir(parents=True, exist_ok=True)
        done = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
                              capture_output=True, text=True, timeout=60)
        if done.returncode != 0 or not link.exists():
            raise OSError(f"Windows could not make the shortcut {link}: {done.stderr.strip()[:300]}")
        made.append(link)
    return made


def _linux(data_dir: Path) -> list[Path]:
    import shlex

    icon = write_icon(data_dir / "icon.png")
    entry = ("[Desktop Entry]\nType=Application\n"
             f"Name={APP_NAME}\nComment=Free PDF tools: edit, OCR, convert, merge, compress\n"
             f"Exec={shlex.join(_launch_command(data_dir))}\nIcon={icon}\nTerminal=false\n"
             "Categories=Office;Utility;\n")
    made = []
    apps = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "applications"
    for folder in (apps, _desktop()):
        if folder != apps and not folder.is_dir():
            continue
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "phi-pdf-studio.desktop"
        path.write_text(entry)
        path.chmod(0o755)
        if folder != apps and shutil.which("gio"):  # GNOME: allow launching without asking
            subprocess.run(["gio", "set", str(path), "metadata::trusted", "true"], capture_output=True)
        made.append(path)
    return made
