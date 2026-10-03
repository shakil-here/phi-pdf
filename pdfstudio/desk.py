"""Files and tool runs, kept per workspace.

A workspace is one person's private set of files: on a personal computer there is one ("local"); on a public
server each visitor gets their own, named by a random id in a signed cookie, and nobody can see another's files.
Files live in <data>/files/<workspace>/<id><ext> with a row in SQLite (shown name, kind, pages, info). Every tool
result is a new file, so tools chain: merge → compress → watermark. Runs work in a thread pool and report progress
in memory for the page to poll; a run cut off by a restart is marked "interrupted" at the next start.
On a public server, files older than `keep_hours` are deleted by `cleanup()` (called every few minutes).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from . import tools

log = logging.getLogger("pdfstudio")

FILE_ID = re.compile(r"^[0-9a-f]{16}$")
WS_ID = re.compile(r"^(local|[0-9a-f]{32})$")
KIND_EXT = {"pdf": ".pdf", "zip": ".zip"}
THUMB_WIDTHS = (160, 240, 360, 520, 760, 1000, 1400)
ORIGINS = {"upload": "Uploaded", "typed": "Typed in"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, ws TEXT NOT NULL, name TEXT NOT NULL, stored TEXT NOT NULL, kind TEXT NOT NULL,
  size INTEGER NOT NULL DEFAULT 0, pages INTEGER NOT NULL DEFAULT 0, info TEXT NOT NULL DEFAULT '{}',
  origin TEXT NOT NULL DEFAULT 'upload', run_id TEXT NOT NULL DEFAULT '', starred INTEGER NOT NULL DEFAULT 0,
  created_at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS files_ws ON files (ws, created_at);
CREATE TABLE IF NOT EXISTS runs (
  id TEXT PRIMARY KEY, ws TEXT NOT NULL, tool TEXT NOT NULL, status TEXT NOT NULL, inputs TEXT NOT NULL DEFAULT '[]',
  params TEXT NOT NULL DEFAULT '{}', outputs TEXT NOT NULL DEFAULT '[]', summary TEXT NOT NULL DEFAULT '',
  stats TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', started_at REAL NOT NULL, finished_at REAL);
CREATE INDEX IF NOT EXISTS runs_ws ON runs (ws, started_at);
"""


def loads(text: str | None, default: Any) -> Any:
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


class DB:
    """A small thread-safe SQLite wrapper (one connection, WAL, a lock)."""

    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA busy_timeout=5000")
            self.conn.executescript(SCHEMA)

    def execute(self, sql: str, *args: Any) -> int:
        with self.lock:
            return self.conn.execute(sql, args).rowcount

    def one(self, sql: str, *args: Any) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute(sql, args).fetchone()

    def all(self, sql: str, *args: Any) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute(sql, args).fetchall()

    def close(self) -> None:
        with self.lock:
            self.conn.close()


class QuotaError(tools.ToolError):
    pass


class Desk:
    def __init__(self, root: Path, *, workers: int = 2, keep_hours: float = 0, max_workspace_mb: int = 0,
                 runs_per_workspace: int = 0):
        self.root = root
        self.files_dir, self.thumbs_dir, self.work_dir = root / "files", root / "thumbs", root / "work"
        for folder in (self.files_dir, self.thumbs_dir, self.work_dir):
            folder.mkdir(parents=True, exist_ok=True)
        self.db = DB(root / "studio.db")
        self.pool = ThreadPoolExecutor(max_workers=max(1, workers), thread_name_prefix="pdftools")
        self.keep_hours = keep_hours
        self.max_bytes = max_workspace_mb * 1024 * 1024
        self.runs_per_workspace = runs_per_workspace
        self._progress: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self.db.execute("UPDATE runs SET status = 'interrupted', finished_at = ?, error = 'The app was restarted "
                        "while this ran: run it again.' WHERE status = 'running'", time.time())
        for leftover in self.work_dir.iterdir():
            shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)

    # ------------------------------------------------------------------ files
    def path(self, row: dict) -> Path:
        return self.files_dir / row["ws"] / row["stored"]

    def used(self, ws: str) -> int:
        row = self.db.one("SELECT COALESCE(SUM(size), 0) n FROM files WHERE ws = ?", ws)
        return int(row["n"]) if row else 0

    def add(self, ws: str, source: Path, name: str, *, origin: str = "upload", run_id: str = "", move: bool = True,
            kind: str | None = None) -> dict[str, Any]:
        """Take a file into a workspace (moved, or copied when move=False). Raises ToolError for unknown types."""
        name = clean_name(name)
        if kind is None:
            with source.open("rb") as handle:
                head = handle.read(4096)
            kind = tools.kind_of(name, head)
            if kind is None and origin in tools.TOOLS and Path(name).suffix.lower() == ".zip":
                kind = "zip"                                  # a tool's own ZIP result (never an upload)
            if kind is None:
                raise tools.ToolError(f"{name}: this kind of file is not accepted. Use PDFs, pictures (JPG, PNG, "
                                      "WEBP, TIFF…), Office files (Word, Excel, PowerPoint) or text/Markdown/HTML.")
        size = source.stat().st_size
        if self.max_bytes and self.used(ws) + size > self.max_bytes:
            raise QuotaError(f"Your space is full ({tools.human_size(self.max_bytes)}): delete some files first.")
        file_id = secrets.token_hex(8)
        suffix = Path(name).suffix.lower()
        ext = KIND_EXT.get(kind) or (suffix if re.fullmatch(r"\.[a-z0-9]{1,8}", suffix) else ".bin")
        if kind == "pdf" and not name.lower().endswith(".pdf"):
            name += ".pdf"
        stored = file_id + ext
        folder = self.files_dir / ws
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / stored
        if move:
            shutil.move(str(source), target)
        else:
            shutil.copyfile(source, target)
        try:
            target.chmod(0o600)
        except OSError:
            pass
        info = tools.describe(target, kind)
        self.db.execute("INSERT INTO files (id, ws, name, stored, kind, size, pages, info, origin, run_id, created_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", file_id, ws, name, stored, kind,
                        info.get("size", 0), info.get("pages", 0), json.dumps(info, ensure_ascii=False), origin,
                        run_id, time.time())
        return self.get(ws, file_id)  # type: ignore[return-value]

    def _row(self, ws: str, file_id: str):
        if not FILE_ID.match(file_id or "") or not WS_ID.match(ws or ""):
            return None
        return self.db.one("SELECT * FROM files WHERE id = ? AND ws = ?", file_id, ws)

    def get(self, ws: str, file_id: str) -> dict[str, Any] | None:
        row = self._row(ws, file_id)
        return self._file(row) if row else None

    def _file(self, row) -> dict[str, Any]:
        info = loads(row["info"], {}) or {}
        origin = row["origin"]
        tool = tools.TOOLS.get(origin)
        viewable = row["kind"] in ("pdf", "image") and not info.get("locked") and not info.get("broken")
        return {"id": row["id"], "ws": row["ws"], "name": row["name"], "stored": row["stored"], "kind": row["kind"],
                "size": row["size"], "pages": row["pages"], "ext": Path(row["name"]).suffix.lower().lstrip("."),
                "origin": origin, "origin_label": tool.label if tool else ORIGINS.get(origin, origin),
                "run_id": row["run_id"], "starred": bool(row["starred"]), "created_at": row["created_at"],
                "locked": bool(info.get("locked")), "encrypted": bool(info.get("encrypted")),
                "broken": info.get("broken", ""), "paper": info.get("paper", ""), "has_text": info.get("has_text"),
                "bookmarks": info.get("bookmarks", 0), "meta": info.get("meta", {}),
                "width": info.get("width"), "height": info.get("height"),
                "thumb": f"/files/{row['id']}/page/1.png?w=240" if viewable else ""}

    def files(self, ws: str, *, kind: str = "", q: str = "", starred: bool = False, sort: str = "new",
              limit: int = 500, ids: list[str] | None = None) -> list[dict[str, Any]]:
        where, args = ["ws = ?"], [ws]
        if kind:
            where.append("kind = ?")
            args.append(kind)
        if q:
            where.append("name LIKE ? ESCAPE '\\'")
            args.append("%" + re.sub(r"([%_\\])", r"\\\1", q.strip()[:100]) + "%")
        if starred:
            where.append("starred = 1")
        if ids is not None:
            ids = [i for i in ids if FILE_ID.match(i)]
            if not ids:
                return []
            where.append(f"id IN ({','.join('?' * len(ids))})")
            args += ids
        order = {"new": "created_at DESC", "old": "created_at", "name": "name COLLATE NOCASE", "size": "size DESC",
                 "pages": "pages DESC"}.get(sort, "created_at DESC")
        rows = self.db.all(f"SELECT * FROM files WHERE {' AND '.join(where)} ORDER BY {order} LIMIT ?", *args, limit)
        found = [self._file(r) for r in rows]
        if ids is not None:                        # in the order asked (merge order)
            by_id = {f["id"]: f for f in found}
            return [by_id[i] for i in ids if i in by_id]
        return found

    def details(self, ws: str, file_id: str) -> dict[str, Any] | None:
        """A file with every page's size (for the organize and edit pages)."""
        row = self._row(ws, file_id)
        if row is None:
            return None
        info = loads(row["info"], {}) or {}
        return {**self._file(row), "sizes": info.get("sizes", []), "version": info.get("version", ""),
                "forms": info.get("forms", False)}

    def input(self, row: dict[str, Any]) -> tools.Input:
        return tools.Input(self.path(row), row["name"], row["kind"])

    def rename(self, ws: str, file_id: str, name: str) -> dict[str, Any] | None:
        row = self.get(ws, file_id)
        if row is None:
            return None
        name = clean_name(name)
        old_suffix = Path(row["name"]).suffix
        if old_suffix and Path(name).suffix.lower() != old_suffix.lower():
            name += old_suffix
        self.db.execute("UPDATE files SET name = ? WHERE id = ? AND ws = ?", name, file_id, ws)
        return self.get(ws, file_id)

    def star(self, ws: str, file_id: str, on: bool) -> None:
        self.db.execute("UPDATE files SET starred = ? WHERE id = ? AND ws = ?", int(on), file_id, ws)

    def delete(self, ws: str, ids: list[str]) -> int:
        removed = 0
        for file_id in ids:
            row = self.get(ws, file_id)
            if row is None:
                continue
            self.path(row).unlink(missing_ok=True)
            for thumb in self.thumbs_dir.glob(f"{file_id}-*.png"):
                thumb.unlink(missing_ok=True)
            self.db.execute("DELETE FROM files WHERE id = ? AND ws = ?", file_id, ws)
            removed += 1
        return removed

    def old_files(self, ws: str, days: float) -> list[str]:
        """Files older than `days` that are not starred."""
        cutoff = time.time() - days * 86400
        return [r["id"] for r in self.db.all("SELECT id FROM files WHERE ws = ? AND starred = 0 AND created_at < ?",
                                             ws, cutoff)]

    def cleanup(self) -> int:
        """Public servers: delete every file older than keep_hours (starred or not) and runs older than a day."""
        if not self.keep_hours:
            return 0
        cutoff = time.time() - self.keep_hours * 3600
        removed = 0
        for row in self.db.all("SELECT id, ws FROM files WHERE created_at < ?", cutoff):
            removed += self.delete(row["ws"], [row["id"]])
        self.db.execute("DELETE FROM runs WHERE started_at < ? AND status != 'running'", time.time() - 86400)
        for folder in self.files_dir.iterdir():
            if folder.is_dir() and not any(folder.iterdir()):
                folder.rmdir()
        return removed

    def thumb(self, ws: str, file_id: str, index: int, width: int) -> bytes:
        row = self._row(ws, file_id)
        if row is None:
            raise FileNotFoundError(file_id)
        width = next((w for w in THUMB_WIDTHS if w >= width), THUMB_WIDTHS[-1])
        cached = self.thumbs_dir / f"{file_id}-{index}-{width}.png"
        if cached.is_file():
            return cached.read_bytes()
        png = tools.render_page(self.files_dir / ws / row["stored"], row["kind"], index, width)
        tmp = cached.with_name(f"{cached.stem}.{secrets.token_hex(4)}.tmp")
        tmp.write_bytes(png)
        tmp.replace(cached)
        return png

    def totals(self, ws: str) -> dict[str, Any]:
        row = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(size), 0) bytes, COALESCE(SUM(pages), 0) pages, "
                          "COALESCE(SUM(kind = 'pdf'), 0) pdfs, COALESCE(SUM(kind = 'image'), 0) images, "
                          "COALESCE(SUM(kind = 'office'), 0) office, COALESCE(SUM(starred), 0) starred "
                          "FROM files WHERE ws = ?", ws)
        runs = self.db.one("SELECT COUNT(*) n, COALESCE(SUM(status = 'done'), 0) done FROM runs WHERE ws = ?", ws)
        return {**dict(row), "runs": runs["n"], "runs_done": runs["done"]}

    # ------------------------------------------------------------------ runs
    def resolve_extras(self, ws: str, tool: str, params: dict[str, Any]) -> dict[str, tools.Input]:
        """The other files a tool's options point at: the watermark picture, the file to insert, pictures placed
        by the editor."""
        extras: dict[str, tools.Input] = {}

        def workspace(ref: str) -> tools.Input:
            row = self.get(ws, ref)
            if row is None:
                raise tools.ToolError("A file the options point at is no longer in My files.")
            return self.input(row)

        if tool == "watermark" and params.get("kind") == "image" and params.get("image_file"):
            extras["image_file"] = workspace(str(params["image_file"]))
        if tool == "insert" and params.get("what") == "file" and params.get("source_file"):
            extras["source_file"] = workspace(str(params["source_file"]))
        if tool == "edit":
            for op in params.get("ops") or []:
                if isinstance(op, dict) and op.get("type") == "image" and op.get("file"):
                    extras[f"file:{op['file']}"] = workspace(str(op["file"]))
        return extras

    def running(self, ws: str) -> int:
        row = self.db.one("SELECT COUNT(*) n FROM runs WHERE ws = ? AND status = 'running'", ws)
        return int(row["n"]) if row else 0

    def start(self, ws: str, tool_id: str, file_ids: list[str], params: dict[str, Any]) -> str:
        tool = tools.TOOLS.get(tool_id)
        if tool is None:
            raise tools.ToolError("Unknown tool.")
        if self.runs_per_workspace and self.running(ws) >= self.runs_per_workspace:
            raise QuotaError("Wait for your running tools to finish first.")
        rows = self.files(ws, ids=list(file_ids))
        if len(rows) != len([i for i in file_ids if i]):
            raise tools.ToolError("A chosen file is no longer in My files.")
        inputs = [self.input(r) for r in rows]
        tools.check_inputs(tool, inputs)
        for row in rows:
            if row["locked"] and tool_id != "unlock":
                raise tools.ToolError(f"{row['name']} is locked with a password. Unlock it first with “Unlock PDF”.")
        extras = self.resolve_extras(ws, tool_id, params)
        run_id = secrets.token_hex(8)
        self.db.execute("INSERT INTO runs (id, ws, tool, status, inputs, params, started_at) VALUES (?, ?, ?, "
                        "'running', ?, ?, ?)", run_id, ws, tool_id, json.dumps([r["id"] for r in rows]),
                        json.dumps(stored_params(params), ensure_ascii=False)[:20000], time.time())
        with self._lock:
            self._progress[run_id] = {"done": 0, "total": 0, "label": "waiting for a free worker"}
        self.pool.submit(self._work, ws, run_id, tool_id, inputs, params, extras)
        return run_id

    def _report(self, run_id: str, done: int, total: int, label: str) -> None:
        with self._lock:
            self._progress[run_id] = {"done": done, "total": total, "label": label}

    def _work(self, ws: str, run_id: str, tool_id: str, inputs, params, extras) -> None:
        out_dir = self.work_dir / run_id
        try:
            self._report(run_id, 0, 0, "starting")
            result = tools.run(tool_id, inputs, params, out_dir,
                               lambda d, t, label: self._report(run_id, d, t, label), extras)
            wanted = clean_name(str(params.get("output_name") or "")) if params.get("output_name") else ""
            if wanted and len(result.outputs) == 1:
                ext = Path(result.outputs[0].name).suffix
                result.outputs[0].name = wanted if wanted.lower().endswith(ext.lower()) else wanted + ext
            outputs = [self.add(ws, o.path, o.name, origin=tool_id, run_id=run_id)["id"] for o in result.outputs]
            self.db.execute("UPDATE runs SET status = 'done', outputs = ?, summary = ?, stats = ?, finished_at = ? "
                            "WHERE id = ?", json.dumps(outputs), result.summary, json.dumps(result.stats), time.time(),
                            run_id)
        except tools.ToolError as exc:
            self.db.execute("UPDATE runs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                            str(exc)[:1000], time.time(), run_id)
        except Exception as exc:  # anything unexpected: the page shows it, the log keeps the traceback
            log.exception("PDF tool %s failed", tool_id)
            self.db.execute("UPDATE runs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?",
                            f"Something went wrong: {exc}"[:1000], time.time(), run_id)
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)
            with self._lock:
                self._progress.pop(run_id, None)

    def run(self, ws: str, run_id: str) -> dict[str, Any] | None:
        if not FILE_ID.match(run_id or ""):
            return None
        row = self.db.one("SELECT * FROM runs WHERE id = ? AND ws = ?", run_id, ws)
        return self._run(row) if row else None

    def _run(self, row, with_files: bool = True) -> dict[str, Any]:
        tool = tools.TOOLS.get(row["tool"])
        outputs = loads(row["outputs"], []) or []
        inputs = loads(row["inputs"], []) or []
        with self._lock:
            progress = dict(self._progress.get(row["id"], {}))
        data = {"id": row["id"], "tool": row["tool"], "label": tool.label if tool else row["tool"],
                "icon": tool.icon if tool else "", "group": tool.group if tool else "organize",
                "status": row["status"], "summary": row["summary"], "params": loads(row["params"], {}),
                "error": row["error"], "started_at": row["started_at"], "finished_at": row["finished_at"],
                "stats": loads(row["stats"], {}) or {}, "progress": progress,
                "seconds": round((row["finished_at"] or time.time()) - row["started_at"], 1),
                "input_ids": inputs, "output_ids": outputs}
        if with_files:
            data["outputs"] = self.files(row["ws"], ids=outputs)
            data["inputs"] = self.files(row["ws"], ids=inputs)
        return data

    def runs(self, ws: str, limit: int = 20) -> list[dict[str, Any]]:
        rows = self.db.all("SELECT * FROM runs WHERE ws = ? ORDER BY started_at DESC LIMIT ?", ws, limit)
        ids = {i for r in rows for i in (loads(r["inputs"], []) or []) + (loads(r["outputs"], []) or [])}
        names = {f["id"]: f["name"] for f in self.files(ws, ids=sorted(ids))} if ids else {}
        out = []
        for row in rows:
            run = self._run(row, with_files=False)
            run["input_names"] = [names.get(i, "(removed)") for i in run["input_ids"]]
            run["output_count"] = len(run["output_ids"])
            out.append(run)
        return out

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        self.db.close()


def clean_name(name: str) -> str:
    name = re.sub(r"[\\/:*?\"<>|\r\n\t\x00]+", " ", Path(str(name or "").replace("\\", "/")).name).strip(" .")
    return name[:150] or "file"


def stored_params(params: dict[str, Any]) -> dict[str, Any]:
    """What a run keeps of its options: no passwords or keys, no pictures (the editor's ops only counted)."""
    kept = {k: v for k, v in params.items() if k not in tools.SECRET_PARAMS}
    if "ops" in kept:
        kept["ops"] = f"{len(kept['ops']) if isinstance(kept['ops'], list) else 0} edits"
    if "plan" in kept and isinstance(kept["plan"], list):
        kept["plan"] = f"{len(kept['plan'])} pages"
    for secret in tools.SECRET_PARAMS:
        if params.get(secret):
            kept[secret] = "•••"
    return kept
