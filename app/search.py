"""Document search.

SEARCH_BACKEND=db      title, document type and exact document code, straight from the database.
SEARCH_BACKEND=engine  the same, plus ranked full-text search over the text inside files via adaptive-search-engine
                       (a separate private service, SEARCH_ENGINE_URL + SEARCH_ENGINE_TOKEN).

The engine never decides who may see a document: matching_ids() returns candidates and every caller keeps only the
documents the user is allowed to see. The engine holds the text of the CURRENT version only; a published document
cannot receive a new version, so a participant never matches text they could not download.

Indexing runs after the database commit, in one background thread (pushes for a document stay in order). A failed
push is logged and repaired by sync_all() on start, or sync_if_needed() from the hourly worker.
"""
import io
import json
import logging
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from html import unescape

from sqlalchemy import and_, event, or_
from sqlalchemy.orm import Session

from app.core import SessionLocal, settings
from app.models import Document, DocumentVersion

log = logging.getLogger("oncm")

MAX_TEXT = 300_000  # characters of file text sent per document
CODE_RE = re.compile(r"ONCM-([A-Z]{2,3})-(\d{4})-V\d+-(\d+)", re.I)
_pool = ThreadPoolExecutor(max_workers=1)


def enabled() -> bool:
    return settings.search_backend == "engine"


# =============================================================== text extraction
def _xml_text(xml: bytes, para_tag: bytes) -> str:
    xml = xml.replace(para_tag, b"\n" + para_tag)
    return unescape(re.sub(rb"<[^>]+>", b" ", xml).decode("utf-8", errors="ignore"))


def ocr_image(image: bytes) -> str:
    """Text in a PNG/JPEG image via the tesseract program ("" if OCR is off or tesseract is not installed)."""
    exe = shutil.which("tesseract") if settings.ocr_enabled else None
    if not exe:
        return ""
    r = subprocess.run([exe, "stdin", "stdout", "-l", settings.ocr_languages], input=image, capture_output=True, timeout=180)
    if r.returncode != 0:
        log.warning("OCR failed: %s", r.stderr.decode(errors="ignore")[:300])
        return ""
    return r.stdout.decode("utf-8", errors="ignore")


def _pdf_text(data: bytes) -> str:
    """Text layer of each page; a page with (almost) none is a scan and is read with OCR instead."""
    from pypdf import PdfReader
    parts, size, ocr_pages, pdfium_doc = [], 0, 0, None
    for i, page in enumerate(PdfReader(io.BytesIO(data)).pages):
        t = page.extract_text() or ""
        if len(t.strip()) < 25 and ocr_pages < settings.ocr_max_pages and settings.ocr_enabled and shutil.which("tesseract"):
            import pypdfium2
            pdfium_doc = pdfium_doc or pypdfium2.PdfDocument(data)
            buf = io.BytesIO()
            pdfium_doc[i].render(scale=300 / 72).to_pil().save(buf, "PNG")  # 300 dpi, what tesseract reads best
            t = ocr_image(buf.getvalue()) or t
            ocr_pages += 1
        parts.append(t)
        size += len(t)
        if size > MAX_TEXT:
            break
    return "\n".join(parts)


def extract_text(data: bytes, filename: str) -> str:
    """Plain text of a PDF (scanned pages via OCR), Word (.docx), Excel (.xlsx), .txt, .csv, or a PNG/JPEG image (OCR).
    Old .doc/.xls and damaged files give "" - extraction never fails an upload or an index push."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "pdf":
            text = _pdf_text(data)
        elif ext in ("png", "jpg", "jpeg"):
            text = ocr_image(data)
        elif ext == "docx":
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = [n for n in z.namelist() if re.match(r"word/(document|header\d*|footer\d*)\.xml$", n)]
                text = "\n".join(_xml_text(z.read(n), b"<w:p") for n in names)
        elif ext == "xlsx":
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            lines = []
            for ws in wb.worksheets:
                lines.append(ws.title)
                for row in ws.iter_rows(values_only=True):
                    lines.append(" ".join(str(v) for v in row if v is not None))
                    if len(lines) > 50_000:
                        break
            text = "\n".join(lines)
        elif ext in ("txt", "csv"):
            text = data.decode("utf-8", errors="ignore")
        else:
            return ""
    except Exception:
        log.warning("Could not extract text from %s", filename, exc_info=True)
        return ""
    return re.sub(r"\s+", " ", text).strip()[:MAX_TEXT]


# =============================================================== engine client
def _call(method: str, path: str, body: dict | None = None, timeout: float = 10):
    # raw UTF-8 (the engine turns \\u escapes outside ASCII into "?"); docId and category before content (the
    # engine's JSON reader takes the first occurrence of a key, and content is free text)
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(settings.search_engine_url.rstrip("/") + path, data=data, method=method,
                                 headers={"Authorization": f"Bearer {settings.search_engine_token}",
                                          "Content-Type": "application/json; charset=utf-8"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        out = json.loads(r.read() or b"null")
    if isinstance(out, dict) and out.get("error"):
        raise RuntimeError(f"search engine: {out['error']}")
    return out


def push(doc_id: int):
    """Send the document's current text to the engine (replacing what it had), or remove it if it no longer exists."""
    from app.services import doc_code, storage
    db = SessionLocal()
    try:
        d = db.get(Document, doc_id)
        if not d:
            _call("DELETE", "/document", {"docId": doc_id})
            return
        v = db.query(DocumentVersion).filter_by(document_id=d.id, version=d.current_version).first()
        text = extract_text(storage().read(v.storage_key), v.filename or "") if v else ""
        head = " ".join(filter(None, [doc_code(d), d.title, d.doc_type, d.category, v.filename if v else None]))
        _call("POST", "/document", {"docId": d.id, "category": d.repo_category or "", "content": f"{head}\n{text}"})
    finally:
        db.close()


def _push_logged(doc_id: int):
    try:
        push(doc_id)
    except Exception:
        log.warning("Search index update failed for document %s (repaired by the next sync)", doc_id, exc_info=True)


def sync_all():
    """Push every document. Runs in the background on start, so a new or emptied engine catches up by itself."""
    db = SessionLocal()
    try:
        ids = [i for (i,) in db.query(Document.id).order_by(Document.id)]
    finally:
        db.close()
    for i in ids:
        _push_logged(i)
    log.warning("Search index synced: %s documents", len(ids))


def sync_if_needed():
    """Hourly: re-sync when the engine's document count differs from ours (engine data lost, or pushes failed)."""
    if not enabled():
        return
    try:
        have = _call("GET", "/health").get("totalDocs")
    except Exception:
        log.warning("Search engine unreachable at %s", settings.search_engine_url)
        return
    db = SessionLocal()
    try:
        want = db.query(Document.id).count()
    finally:
        db.close()
    if have != want:
        sync_all()


# Collect changed documents during the transaction; push them only once it commits (like the email queue).
@event.listens_for(Session, "after_flush")
def _collect(session, _ctx):
    if not enabled():
        return
    dirty = session.info.setdefault("search_dirty", set())
    for o in list(session.new) + list(session.dirty) + list(session.deleted):
        if isinstance(o, Document):
            dirty.add(o.id)
        elif isinstance(o, DocumentVersion):
            dirty.add(o.document_id)


@event.listens_for(Session, "after_commit")
def _after_commit(session):
    if session.in_nested_transaction():
        return
    for doc_id in sorted(session.info.pop("search_dirty", ())):
        _pool.submit(_push_logged, doc_id)


@event.listens_for(Session, "after_rollback")
def _after_rollback(session):
    if not session.in_nested_transaction():
        session.info.pop("search_dirty", None)


# =============================================================== searching
def matching_ids(db, q: str, limit: int = 200, full_text: bool = True) -> list[int]:
    """Document ids matching q, best first: exact document code, then title / document type, then the engine's
    full-text ranking. NOT permission-filtered - callers keep only documents the user may see.
    full_text=False for lists where the user may know a document exists but not read it (request-only catalogue)."""
    q = q.strip()[:200]
    if not q:
        return []
    ids: list[int] = []
    m = CODE_RE.search(q)
    if m:
        ids += [i for (i,) in db.query(Document.id).filter(and_(
            Document.repo_category == m.group(1).upper(), Document.year == int(m.group(2)), Document.serial == int(m.group(3))))]
    like = f"%{q}%"
    ids += [i for (i,) in db.query(Document.id).filter(or_(Document.title.ilike(like), Document.doc_type.ilike(like)))
            .order_by(Document.updated_at.desc()).limit(limit)]
    if full_text and enabled():
        try:
            r = _call("GET", "/search?" + urllib.parse.urlencode({"q": q, "mode": "hybrid", "k": limit, "content": 0}), timeout=5)
            ids += [hit["docId"] for hit in r.get("results", [])]
        except Exception as e:
            log.warning("Search engine unavailable (%s); searching titles and codes only", e)
    return list(dict.fromkeys(ids))  # keep the first (best) position of each id
