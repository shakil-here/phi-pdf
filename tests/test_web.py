"""The web app, the REST API and the command line. Public mode: visitors never see each other's files."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from pdfstudio.cli import main as cli_main
from pdfstudio.config import Settings
from pdfstudio.web import create_app


def make_pdf(path: Path, pages: int = 3, words: str = "Hello") -> bytes:
    doc = pymupdf.open()
    for i in range(pages):
        doc.new_page().insert_text((72, 100), f"{words} page {i + 1}", fontsize=14)
    doc.save(path)
    return path.read_bytes()


def csrf_of(html: str) -> str:
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def wait(client: TestClient, run_id: str, timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        data = client.get(f"/api/runs/{run_id}").json()
        if data["status"] != "running":
            return data
        time.sleep(0.15)
    raise AssertionError("the tool did not finish")


@pytest.fixture
def local(tmp_path):
    app = create_app(Settings.load("local", data_dir=tmp_path / "data"))
    with TestClient(app) as client:
        yield client
    app.state.desk.close()


@pytest.fixture
def public(tmp_path):
    app = create_app(Settings.load("server", data_dir=tmp_path / "srv"))
    yield app
    app.state.desk.close()


def test_local_flow_upload_run_download_and_edit_text(local, tmp_path):
    page = local.get("/")
    assert page.status_code == 200 and "Merge PDF" in page.text and "PDF to Word" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    token = csrf_of(page.text)
    headers = {"X-CSRF-Token": token}
    a = make_pdf(tmp_path / "a.pdf", 4)
    files = [("files", ("Chapter A.pdf", a, "application/pdf")), ("files", ("b.pdf", make_pdf(tmp_path / "b.pdf", 2), "application/pdf")),
             ("files", ("evil.exe", b"MZ\x90\x00", "application/octet-stream")),
             ("files", ("notes.md", "# Title\n\nবাংলা".encode(), "text/markdown"))]
    assert local.post("/api/files", files=files).status_code == 403                 # no CSRF token
    data = local.post("/api/files", files=files, headers=headers).json()
    assert [f["kind"] for f in data["files"]] == ["pdf", "pdf", "text"] and len(data["errors"]) == 1
    fa, fb, notes = data["files"]
    for path in (f"/tools/merge?files={fa['id']},{fb['id']}", f"/edit/{fa['id']}", f"/organize?files={fa['id']}",
                 f"/tools/text2pdf?files={notes['id']}", "/settings", "/developers", "/files?kind=text"):
        assert local.get(path).status_code == 200, path
    assert local.get(f"/files/{fa['id']}/page/2.png?w=200").content.startswith(b"\x89PNG")

    run = wait(local, local.post("/api/run", json={"tool": "merge", "files": [fa["id"], fb["id"]], "params": {}},
                                 headers=headers).json()["run"])
    assert run["status"] == "done" and run["outputs"][0]["pages"] == 6
    got = local.get(f"/files/{run['outputs'][0]['id']}/download")
    assert got.content.startswith(b"%PDF") and "attachment" in got.headers["content-disposition"]

    shown = local.get(f"/files/{notes['id']}/download?inline=1")                       # text is never served as HTML
    assert shown.headers["content-type"].startswith("text/plain") and "sandbox" in shown.headers["content-security-policy"]

    blocks = local.get(f"/api/files/{fa['id']}/text/1").json()["blocks"]
    assert blocks[0]["text"] == "Hello page 1"
    b = blocks[0]
    op = {"type": "textedit", "page": 0, "x": b["bbox"][0], "y": b["bbox"][1], "w": b["bbox"][2] - b["bbox"][0],
          "h": b["bbox"][3] - b["bbox"][1], "text": "Edited page 1", "orig": b["text"], "size": b["size"],
          "old_size": b["size"], "color": b["color"], "font": "original", "fontname": b["font"],
          "lines": [{"x": ln["bbox"][0], "y": ln["bbox"][1], "w": ln["bbox"][2] - ln["bbox"][0],
                     "h": ln["bbox"][3] - ln["bbox"][1], "ox": ln["origin"][0], "oy": ln["origin"][1]} for ln in b["lines"]]}
    edited = wait(local, local.post("/api/run", json={"tool": "edit", "files": [fa["id"]], "params": {"ops": [op]}},
                                    headers=headers).json()["run"])
    with pymupdf.open(stream=local.get(f"/files/{edited['outputs'][0]['id']}/download").content) as doc:
        assert "Edited page 1" in doc[0].get_text() and "Hello page 1" not in doc[0].get_text()

    typed = local.post("/api/files/text", json={"text": "# Typed\n\nhello", "format": "markdown", "name": "Mine"},
                       headers=headers).json()["file"]
    assert typed["name"] == "Mine.md" and typed["kind"] == "text"
    made = wait(local, local.post("/api/run", json={"tool": "text2pdf", "files": [typed["id"], notes["id"]], "params": {}},
                                  headers=headers).json()["run"])
    assert made["status"] == "done" and made["outputs"][0]["pages"] == 2

    protected = wait(local, local.post("/api/run", json={"tool": "protect", "files": [fb["id"]],
                                                         "params": {"password": "topsecret"}}, headers=headers).json()["run"])
    assert protected["outputs"][0]["locked"] and "topsecret" not in json.dumps(protected)
    stored = local.app.state.desk.db.one("SELECT params FROM runs WHERE id = ?", protected["id"])["params"]
    assert "topsecret" not in stored
    r = local.post("/api/run", json={"tool": "rotate", "files": [protected["outputs"][0]["id"]], "params": {}}, headers=headers)
    assert r.status_code == 400 and "Unlock" in r.json()["error"]
    prev = local.post("/api/preview", json={"tool": "numbers", "files": [fa["id"]], "params": {}, "page": 2}, headers=headers)
    assert prev.content.startswith(b"\x89PNG")
    assert local.get(f"/zip?ids={fa['id']},{fb['id']}").content[:2] == b"PK"
    assert local.post("/api/delete", json={"ids": [fb["id"]]}, headers=headers).json()["removed"] == 1
    assert local.get(f"/files/{fb['id']}/download").status_code == 404
    assert local.post("/api/delete", json={"everything": True}, headers=headers).json()["removed"] >= 4
    assert local.get("/files").text.count('"id":') == 0


def test_local_mode_refuses_other_host_names(local):
    """A web page on another site that points its own name at 127.0.0.1 (DNS rebinding) is refused."""
    assert local.get("/", headers={"host": "evil.example.com"}).status_code == 421
    assert local.get("/", headers={"host": "127.0.0.1:8790"}).status_code == 200
    assert local.get("/", headers={"host": "localhost:8790"}).status_code == 200


def test_public_mode_keeps_visitors_apart_and_cleans_up(public, tmp_path):
    alice, bob = TestClient(public), TestClient(public)
    page = alice.get("/")
    assert page.status_code == 200 and "phipdf_ws" in page.headers.get("set-cookie", "") and "deleted" in page.text
    token_a = csrf_of(page.text)
    token_b = csrf_of(bob.get("/").text)
    assert token_a != token_b
    up = alice.post("/api/files", files=[("files", ("private.pdf", make_pdf(tmp_path / "p.pdf"), "application/pdf"))],
                    headers={"X-CSRF-Token": token_a}).json()["files"][0]
    assert alice.get(f"/files/{up['id']}/download").status_code == 200
    assert bob.get(f"/files/{up['id']}/download").status_code == 404                  # another visitor: not found
    assert bob.get(f"/files/{up['id']}/page/1.png").status_code == 404
    assert bob.get(f"/api/files/{up['id']}").status_code == 404
    assert bob.post("/api/delete", json={"ids": [up["id"]]}, headers={"X-CSRF-Token": token_b}).json()["removed"] == 0
    assert bob.post("/api/run", json={"tool": "rotate", "files": [up["id"]]}, headers={"X-CSRF-Token": token_b}).status_code == 400
    assert alice.post("/api/files", files=[], headers={"X-CSRF-Token": token_b}).status_code == 403  # Bob's token
    forged = TestClient(public, cookies={"phipdf_ws": alice.cookies["phipdf_ws"].split(".")[0] + ".0000"})
    assert forged.get(f"/files/{up['id']}/download").status_code == 404                 # a forged cookie is a new visitor
    assert public.state.settings.allow_downloads is False
    assert alice.post("/api/ocr/languages", json={"code": "ben"}, headers={"X-CSRF-Token": token_a}).status_code == 403
    assert "path" not in alice.get("/api/capabilities").json()["tesseract"]
    huge = alice.post("/api/files", headers={"X-CSRF-Token": token_a, "Content-Length": str(2 * 1024 ** 3)}, content=b"x")
    assert huge.status_code == 413
    desk = public.state.desk
    desk.db.execute("UPDATE files SET created_at = created_at - 3 * 3600")
    assert desk.cleanup() == 1 and alice.get(f"/files/{up['id']}/download").status_code == 404


def test_password_protected_server(tmp_path, monkeypatch):
    monkeypatch.setenv("PHIPDF_PASSWORD", "open sesame")
    app = create_app(Settings.load("server", data_dir=tmp_path / "pw"))
    c = TestClient(app)
    assert c.get("/", follow_redirects=False).status_code == 303
    assert c.get("/api/files").status_code == 401
    assert c.get("/api/v1/tools").status_code == 401
    assert "not right" in c.post("/login", data={"password": "nope", "next": "/files"}).text
    r = c.post("/login", data={"password": "open sesame", "next": "//evil.example.com"}, follow_redirects=False)
    assert r.headers["location"] == "/"
    assert c.get("/").status_code == 200 and c.get("/api/v1/tools").status_code == 200
    app.state.desk.close()


def test_rest_api(local, tmp_path, monkeypatch):
    tools = local.get("/api/v1/tools").json()["tools"]
    assert {"merge", "ocr", "pdf2word", "edit"} <= {t["id"] for t in tools}
    a, b = make_pdf(tmp_path / "a.pdf", 2), make_pdf(tmp_path / "b.pdf", 3)
    r = local.post("/api/v1/tools/merge", files=[("files", ("a.pdf", a)), ("files", ("b.pdf", b))])
    assert r.status_code == 200 and r.content.startswith(b"%PDF") and "joined" in r.headers["x-phipdf-summary"]
    with pymupdf.open(stream=r.content) as doc:
        assert doc.page_count == 5
    r = local.post("/api/v1/tools/split", files=[("files", ("a.pdf", a))], data={"params": '{"mode": "each"}'})
    assert r.headers["content-type"] == "application/zip" and r.content[:2] == b"PK"
    r = local.post("/api/v1/tools/rotate", files=[("files", ("a.pdf", a))], data={"angle": "180"})   # plain fields
    with pymupdf.open(stream=r.content) as doc:
        assert doc[0].rotation == 180
    from PIL import Image
    Image.new("RGB", (120, 60), "red").save(tmp_path / "logo.png")
    r = local.post("/api/v1/tools/watermark", files=[("files", ("a.pdf", a)), ("image_file", ("logo.png", (tmp_path / "logo.png").read_bytes()))],
                   data={"params": '{"kind": "image"}'})
    assert r.status_code == 200
    assert local.post("/api/v1/tools/rotate", files=[("files", ("x.exe", b"MZ"))]).status_code == 400
    assert local.post("/api/v1/tools/nope", files=[("files", ("a.pdf", a))]).status_code == 404
    assert local.post("/api/v1/tools/merge", files=[("files", ("a.pdf", a))]).json()["error"].startswith("Merge PDF needs")
    assert not list((local.app.state.settings.data_dir / "work").glob("api-*"))         # nothing left behind
    monkeypatch.setattr(local.app.state.settings, "api_key", "k123")
    assert local.get("/api/v1/tools").status_code == 401
    assert local.get("/api/v1/tools", headers={"Authorization": "Bearer k123"}).status_code == 200


def test_command_line_runs_tools(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("PHIPDF_DATA", str(tmp_path / "cli-data"))
    make_pdf(tmp_path / "a.pdf", 3)
    cli_main(["run", "rotate", str(tmp_path / "a.pdf"), "--set", "angle=90", "--set", "pages=1-2", "-o", str(tmp_path / "out")])
    out = capsys.readouterr().out
    assert "✓ 2 page(s) turned 90°." in out
    with pymupdf.open(tmp_path / "out" / "a (rotated).pdf") as doc:
        assert [p.rotation for p in doc] == [90, 90, 0]
    cli_main(["run", "protect", str(tmp_path / "a.pdf"), "--password", "abcd", "-o", str(tmp_path / "out")])
    with pymupdf.open(tmp_path / "out" / "a (protected).pdf") as doc:
        assert doc.needs_pass
    with pytest.raises(SystemExit):
        cli_main(["run", "remove", str(tmp_path / "a.pdf"), "--set", "pages=1-3", "-o", str(tmp_path / "out")])
    cli_main(["tools"])
    assert "pdf2word" in capsys.readouterr().out
    cli_main(["doctor"])
    assert "PDF engine" in capsys.readouterr().out


def test_editor_fonts_and_ocr_routes(local, tmp_path):
    css = local.get("/fonts.css")
    assert css.status_code == 200 and "PHI Roboto" in css.text and css.headers["content-type"].startswith("text/css")
    name = re.search(r"/fonts/([A-Za-z0-9]+-Regular\.ttf)", css.text).group(1)
    assert local.get(f"/fonts/{name}").content[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true")
    assert local.get("/fonts/..%2Fweb.py").status_code == 404
    token = csrf_of(local.get("/").text)
    fa = local.post("/api/files", files=[("files", ("a.pdf", make_pdf(tmp_path / "a.pdf", 1), "application/pdf"))],
                    headers={"X-CSRF-Token": token}).json()["files"][0]
    page = local.get(f"/edit/{fa['id']}").text
    assert 'id="ed-fonts"' in page and "Hind Siliguri" in page and 'data-tool="whitener"' in page
    assert local.post(f"/api/files/{fa['id']}/ocr/1", json={}).status_code == 403          # no CSRF token
    got = local.post(f"/api/files/{fa['id']}/ocr/9", json={"language": "eng"}, headers={"X-CSRF-Token": token})
    assert got.status_code in (400, 404)
