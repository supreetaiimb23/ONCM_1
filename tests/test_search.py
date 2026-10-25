"""Search: text extraction, indexing after commit, code/title search, permission filtering, engine fallback.

Most tests use an in-process fake engine; test_real_engine_round_trip runs the adaptive-search-engine binary when it is
built next to this repo (or ADAPTIVE_SEARCH_SERVER points at it).
"""
import io
import os
import re
import secrets
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from app import search
from app.core import Settings, check_secrets, settings
from tests.conftest import _free_port, admin_doc, approve, auth, start_review, uid


def text_pdf(text: str) -> bytes:
    """A real one-page PDF whose text pypdf can extract."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(out)


def docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p>'
                                        f'<w:p><w:r><w:t>second &amp; para</w:t></w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def xlsx(text: str) -> bytes:
    wb = Workbook()
    wb.active.append(["Project", text])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def scan(text: str, fmt="PDF") -> bytes:
    """An image of text, as a scanner produces it: a PDF without a text layer, or a PNG."""
    img = Image.new("RGB", (1700, 400), "white")
    ImageDraw.Draw(img).text((80, 150), text, fill="black", font=ImageFont.load_default(size=60))
    buf = io.BytesIO()
    img.save(buf, fmt, resolution=200)
    return buf.getvalue()


needs_tesseract = pytest.mark.skipif(not shutil.which("tesseract"), reason="tesseract (OCR) is not installed")


class FakeEngine:
    """Stands in for adaptive-search-engine's HTTP API: any query word found in a document's text is a hit."""
    def __init__(self):
        self.docs, self.up = {}, True

    def __call__(self, method, path, body=None, timeout=10):
        if not self.up:
            raise OSError("connection refused")
        if path == "/document":
            if method == "POST":
                assert list(body) == ["docId", "category", "content"]  # content last (engine reads first key match)
                self.docs[body["docId"]] = body["content"]
            else:
                self.docs.pop(body["docId"], None)
            return {"status": "ok"}
        if path == "/health":
            return {"totalDocs": len(self.docs)}
        q = re.search(r"q=([^&]*)", path).group(1).lower().split("+")
        return {"results": [{"docId": i} for i, t in self.docs.items() if any(w in t.lower().split() for w in q)]}


@pytest.fixture
def engine(monkeypatch):
    fake = FakeEngine()
    monkeypatch.setattr(settings, "search_backend", "engine")
    monkeypatch.setattr(search, "_call", fake)
    monkeypatch.setattr(search._pool, "submit", lambda fn, *a: fn(*a))  # index synchronously
    return fake


def upload(client, user, data: bytes, name="f.pdf", category="CM", level="internal", title=None):
    r = client.post("/api/admin/documents", headers=auth(user),
                    data={"title": title or f"Doc {uid()}", "repo_category": category, "access_level": level},
                    files={"file": (name, data, "application/octet-stream")})
    assert r.status_code == 201, r.text
    return r.json()


def ids(resp):
    assert resp.status_code == 200, resp.text
    return [x["id"] for x in resp.json()]


# --------------------------------------------------------------------------- extraction
def test_extract_text_from_each_format():
    assert "zebrafinch registry" in search.extract_text(text_pdf("zebrafinch registry"), "a.pdf")
    t = search.extract_text(docx("methodology kestrel"), "a.docx")
    assert "methodology kestrel" in t and "second & para" in t
    assert "Project heronbay" in search.extract_text(xlsx("heronbay"), "a.xlsx")
    assert search.extract_text("plain words".encode(), "a.txt") == "plain words"
    assert search.extract_text(b"%PDF-1.4 broken", "bad.pdf") == ""   # damaged file: no text, no error
    assert search.extract_text(b"\x89PNG....", "a.png") == ""


@needs_tesseract
def test_ocr_reads_scanned_pdfs_and_images(monkeypatch):
    assert "Tidal wetland survey" in search.extract_text(scan("Tidal wetland survey"), "scan.pdf")
    assert "Tidal wetland survey" in search.extract_text(scan("Tidal wetland survey", "PNG"), "scan.png")
    monkeypatch.setattr(settings, "ocr_enabled", False)
    assert search.extract_text(scan("Tidal wetland survey"), "scan.pdf") == ""


def test_engine_backend_needs_a_token():
    with pytest.raises(RuntimeError, match="SEARCH_ENGINE_TOKEN"):
        check_secrets(Settings(_env_file=None, secret_key="x" * 32, master_admin_password="Other!Pass1",
                               search_backend="engine", search_engine_token="short"))


# --------------------------------------------------------------------------- db backend (default)
def test_search_by_document_code_and_title(client, master):
    doc = upload(client, master, text_pdf(f"x{uid()}"), title=f"Interaction deck {uid()}")
    other = upload(client, master, text_pdf(f"y{uid()}"))
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": doc["code"]}))[0] == doc["id"]
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": doc["code"].lower()}))[0] == doc["id"]
    found = ids(client.get("/api/admin/documents", headers=auth(master), params={"q": doc["title"][-14:]}))
    assert doc["id"] in found and other["id"] not in found


# --------------------------------------------------------------------------- engine backend
def test_file_text_is_indexed_after_commit_and_replaced_by_new_versions(client, master, engine):
    word, word2 = f"kestrel{uid()}", f"plover{uid()}"
    doc = upload(client, master, text_pdf(f"carbon {word} registry"))
    assert word in engine.docs[doc["id"]] and doc["code"] in engine.docs[doc["id"]]
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": word})) == [doc["id"]]

    r = client.post(f"/api/admin/documents/{doc['id']}/versions", headers=auth(master),
                    files={"file": ("v2.docx", docx(f"new text {word2}"), "application/octet-stream")})
    assert r.status_code == 201
    assert word2 in engine.docs[doc["id"]] and word not in engine.docs[doc["id"]]
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": word})) == []


@needs_tesseract
def test_scanned_upload_is_searchable(client, master, engine):
    doc = upload(client, master, scan("Mangrove baseline survey"), name="scan.pdf")
    assert "Mangrove baseline survey" in engine.docs[doc["id"]]
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": "baseline"})) == [doc["id"]]


def test_failed_request_does_not_reach_the_index(client, master, engine, monkeypatch):
    doc = upload(client, master, text_pdf(f"first {uid()}"))
    before = engine.docs[doc["id"]]

    def boom(*a, **k):
        raise HTTPException(500, "simulated failure")
    monkeypatch.setattr("app.routers.admin.audit", boom)
    r = client.post(f"/api/admin/documents/{doc['id']}/versions", headers=auth(master),
                    files={"file": ("v2.pdf", text_pdf(f"never {uid()}"), "application/pdf")})
    assert r.status_code == 500
    assert engine.docs[doc["id"]] == before


def test_participants_only_find_what_they_can_open(client, master, make_admin, make_org, engine):
    word = f"heron{uid()}"
    o1, p1 = make_org()
    _, p2 = make_org()
    shared = upload(client, make_admin(), text_pdf(f"{word} shared"), level="restricted")
    internal = upload(client, master, text_pdf(f"{word} internal"))
    start_review(client, master, shared["id"])
    approve(client, master, shared["id"])
    assert client.post(f"/api/admin/documents/{shared['id']}/publish", headers=auth(master),
                       json={"organization_ids": [o1.id]}).status_code == 201
    assert {shared["id"], internal["id"]} <= set(engine.docs)  # the engine has both...

    assert ids(client.get("/api/me/resources", headers=auth(p1), params={"q": word})) == [shared["id"]]  # ...ONCM filters
    assert ids(client.get("/api/me/resources", headers=auth(p2), params={"q": word})) == []
    assert shared["id"] in ids(client.get("/api/me/resources", headers=auth(p1)))  # no q: unchanged


def test_request_only_catalogue_does_not_search_file_text(client, master, make_admin, make_org, engine):
    word, title = f"secretterm{uid()}", f"Pilot terms {uid()}"
    _, p = make_org()
    doc = upload(client, make_admin(), text_pdf(f"{word} inside"), level="request_only", title=title)
    start_review(client, master, doc["id"])
    approve(client, master, doc["id"])
    assert client.post(f"/api/admin/documents/{doc['id']}/publish", headers=auth(master), json={}).status_code == 201
    assert ids(client.get("/api/me/catalogue", headers=auth(p), params={"q": word})) == []
    assert ids(client.get("/api/me/catalogue", headers=auth(p), params={"q": title})) == [doc["id"]]


def test_engine_down_falls_back_to_title_and_code(client, master, engine):
    doc = upload(client, master, text_pdf(f"z {uid()}"), title=f"Fallback {uid()}")
    engine.up = False
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": doc["title"]})) == [doc["id"]]
    assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": "nothing-matches-this"})) == []


def test_sync_restores_an_emptied_engine(client, master, engine):
    doc = upload(client, master, text_pdf(f"sync {uid()}"))
    engine.docs.clear()  # engine lost its data
    search.sync_if_needed()
    assert doc["id"] in engine.docs


# --------------------------------------------------------------------------- the real engine
def _engine_binary():
    p = os.environ.get("ADAPTIVE_SEARCH_SERVER") or str(ENGINE_DIR / "build/adaptive-search-server")
    return p if Path(p).is_file() else None


ENGINE_DIR = Path(__file__).resolve().parents[2] / "adaptive-search-engine"
MODEL = ENGINE_DIR / "models/all-MiniLM-L6-v2-st.onnx"  # upstream sentence-transformers ONNX export


@pytest.mark.slow
@pytest.mark.skipif(not _engine_binary(), reason="adaptive-search-engine is not built")
def test_real_engine_round_trip(client, master, monkeypatch, tmp_path):
    port, token = _free_port(), secrets.token_urlsafe(32)
    env = {**os.environ, "SEARCH_API_TOKEN": token, "SEARCH_JOURNAL": str(tmp_path / "journal.jsonl"),
           "SEARCH_MODEL": str(MODEL), "SEARCH_VOCAB": str(ENGINE_DIR / "models/tokenizer_config/vocab.txt")}
    proc = subprocess.Popen([_engine_binary(), str(port), "none"], env=env, cwd=ENGINE_DIR,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        monkeypatch.setattr(settings, "search_backend", "engine")
        monkeypatch.setattr(settings, "search_engine_url", f"http://127.0.0.1:{port}")
        monkeypatch.setattr(settings, "search_engine_token", token)
        for _ in range(50):
            try:
                search._call("GET", "/health")
                break
            except OSError:
                time.sleep(0.1)
        word = f"kingfisher{uid()}"
        doc = upload(client, master, docx(f"Méthode \"quoted\" {word}"), name="m.docx")
        search._pool.submit(lambda: None).result(timeout=30)  # wait for the background push
        assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": word})) == [doc["id"]]

        if search._call("GET", "/health")["semanticAvailable"]:  # meaning, not words: no term in common
            ppa = upload(client, master, docx("Power purchase agreement for a 50 MW solar photovoltaic plant, "
                                              f"electricity tariff and grid connection {uid()}"), name="ppa.docx")
            search._pool.submit(lambda: None).result(timeout=60)
            assert ppa["id"] in ids(client.get("/api/admin/documents", headers=auth(master), params={"q": "renewable energy contract"}))
            assert ppa["id"] not in ids(client.get("/api/admin/documents", headers=auth(master), params={"q": "football match score"}))

        settings.search_engine_token = "wrong-token-" + "x" * 30  # the engine refuses; ONCM falls back
        assert ids(client.get("/api/admin/documents", headers=auth(master), params={"q": word})) == []
    finally:
        proc.terminate()
        proc.wait(10)
