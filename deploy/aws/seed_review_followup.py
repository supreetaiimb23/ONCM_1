"""Follow-up to seed_test_workflow.py: assign the TEST secondary admin to TEST organisation #1 (approved earlier
without an assigned admin) and run the participant-document review workflow on its Project Design Document."""
import json
import sys
import urllib.error
import urllib.request
import uuid

from app.core import SessionLocal, session_token
from app.models import Document, Organization, User

ACC = json.load(open("/tmp/accounts.json"))
BASE = "http://localhost:8000/api"
results = []


def check(name, ok, detail=""):
    results.append(bool(ok))
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail and not ok else ""))


def call(method, path, token=None, json_body=None, form=None, files=None):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    data = None
    if json_body is not None:
        data, headers["Content-Type"] = json.dumps(json_body).encode(), "application/json"
    elif files is not None:
        b = "----oncm" + uuid.uuid4().hex
        parts = []
        for k, v in (form or {}).items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
        for k, (fname, content) in files.items():
            parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fname}"\r\n'
                         f"Content-Type: application/octet-stream\r\n\r\n".encode() + content + b"\r\n")
        data = b"".join(parts) + f"--{b}--\r\n".encode()
        headers["Content-Type"] = f"multipart/form-data; boundary={b}"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw[:1] in b"[{" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw[:200]


# ------------------------------------------------------------------ dummy files
def pdf(text, size=0):
    """A real PDF with extractable text, padded (inside its content stream) to roughly `size` bytes."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    if size:
        stream += b"\n%" + b"x" * max(size - 700, 0)  # PDF comment: ignored when rendering
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
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


MB = 1024 * 1024


db = SessionLocal()
master = db.query(User).filter_by(role="master_admin").order_by(User.id).first()
M = session_token(master)
admin2 = db.query(User).filter_by(email=ACC["admin2"]["email"]).one()
A2 = session_token(admin2)
pd_user = db.query(User).filter_by(email=ACC["pd"]["email"]).one()
PD = session_token(pd_user)
org = db.get(Organization, pd_user.organization_id)
doc = db.query(Document).filter(Document.organization_id == org.id, Document.title.like("TEST - Project Design Document%")).one()

st, r = call("PATCH", f"/admin/organizations/{org.id}", M, {"assigned_admin_id": admin2.id})
check(f"Master Admin assigns the TEST reviewer to '{org.name[:40]}'", st == 200, r)
st, r = call("POST", f"/admin/documents/{doc.id}/review", A2, {"decision": "start_review"})
check("reviewer starts review of the PDD", st == 200 and r["status"] == "under_review", r)
st, r = call("POST", f"/me/documents/{doc.id}/versions", PD, files={"file": ("PDD-x.pdf", pdf("changed during review", 100000))})
check("participant cannot replace a document while it is under review (409)", st == 409, r)
st, r = call("POST", f"/admin/documents/{doc.id}/review", A2, {"decision": "changes", "feedback": "TEST: please add the leakage assessment in section 4."})
check("reviewer requests changes with feedback", st == 200 and r["status"] == "changes_requested", r)
st, r = call("GET", "/me/documents", PD)
check("participant sees the reviewer's feedback on the document", st == 200 and "leakage" in json.dumps(r), r if st != 200 else "")
st, r = call("POST", f"/me/documents/{doc.id}/versions", PD,
             files={"file": ("PDD-final.pdf", pdf("Project design document final with leakage assessment for agroforestry", 2 * MB))})
check(f"participant uploads the corrected version (v{r.get('current_version') if isinstance(r, dict) else '?'})", st == 201, r)
for step in ({"decision": "start_review"}, {"decision": "recommend", "comment": "TEST: complete now"}):
    st, r = call("POST", f"/admin/documents/{doc.id}/review", A2, step)
    check(f"reviewer: {step['decision']}", st == 200, r)
st, r = call("POST", f"/admin/documents/{doc.id}/review", A2, {"decision": "approve", "confirm_classification": True})
check("secondary admin cannot give final approval (two-person rule, 403)", st == 403, r)
st, r = call("POST", f"/admin/documents/{doc.id}/review", M, {"decision": "approve", "confirm_classification": True, "access_level": "restricted"})
check("Master Admin gives final approval", st == 200 and r["status"] == "internally_approved", r)
st, r = call("GET", "/me/documents", PD)
check("participant sees the PDD as approved", st == 200 and any(d["id"] == doc.id and d["status"] == "internally_approved" for d in r), r)
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
