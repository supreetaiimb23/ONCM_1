"""End-to-end workflow test on a live deployment, with clearly labelled TEST data.

Runs INSIDE the oncm-workbench container (deploy/aws/seed-test-workflow.sh copies it in) and drives the real HTTP API
on localhost:8000, so every step goes through the same code as a person would: virus scan, S3 storage, search
indexing, permission checks. Sign-ins are replaced by tokens minted with the app's own key (no passwords typed);
test accounts get the passwords from /tmp/accounts.json so people can sign in as them afterwards.

Expects join requests created by the public form test (contact emails supreetpatil79+oncm...@gmail.com).
"""
import io
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile

from openpyxl import Workbook
from PIL import Image, ImageDraw, ImageFont

from app.core import SessionLocal, make_token, session_token
from app.models import JoinRequest, User

BASE = "http://localhost:8000/api"
ACC = json.load(open("/tmp/accounts.json"))
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


def scan(text, fmt="PDF"):
    img = Image.new("RGB", (1700, 500), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(text.split("|")):
        d.text((80, 100 + i * 110), line.strip(), fill="black", font=ImageFont.load_default(size=56))
    buf = io.BytesIO()
    img.save(buf, fmt, resolution=200) if fmt == "PDF" else img.save(buf, fmt, quality=92)
    return buf.getvalue()


def docx(text):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f"<w:document><w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>")
    return buf.getvalue()


def xlsx(rows):
    wb = Workbook()
    for r in rows:
        wb.active.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


MB = 1024 * 1024
EICAR = rb"X5O!P%@AP[4\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"

# ------------------------------------------------------------------ accounts
db = SessionLocal()
master = db.query(User).filter_by(role="master_admin").order_by(User.id).first()
M = session_token(master)
print(f"== Master Admin: {master.email}")

st, r = call("POST", "/master/admins", M, {"name": "TEST Reviewer (Secondary Admin)", "email": ACC["admin2"]["email"],
                                            "password": ACC["admin2"]["password"], "role": "secondary_admin",
                                            "can_share": True, "can_export": True,
                                            "repo_access": ["CM", "POL", "RES", "PPD", "CDP"]})
check("create TEST secondary admin", st in (201, 409), r)
admin2 = db.query(User).filter_by(email=ACC["admin2"]["email"]).one()
A2 = session_token(admin2)

print("== Onboarding decisions")
jr = {j.contact_email: j for j in db.query(JoinRequest).filter(JoinRequest.contact_email.like("supreetpatil79+oncm%"))}
ids = {k: jr[ACC[k]["email"]].id for k in ("pd", "vvb")}
for key in ("pd", "vvb"):
    st, r = call("POST", f"/master/join-requests/{ids[key]}/decision", M, {"decision": "approve", "assigned_admin_id": admin2.id})
    check(f"approve join request #{ids[key]} ({key})", st == 200 or (st == 409), r)
st, r = call("POST", f"/master/join-requests/{jr['supreetpatil79+oncm-obligated@gmail.com'].id}/decision", M, {"decision": "reject"})
check("reject without a reason is refused", st == 400, r)
st, r = call("POST", f"/master/join-requests/{jr['supreetpatil79+oncm-obligated@gmail.com'].id}/decision", M,
             {"decision": "reject", "reason": "TEST: compliance buyers join in phase 2 of the pilot."})
check("reject with a reason", st in (200, 409), r)
st, r = call("POST", f"/master/join-requests/{jr['supreetpatil79+oncm-tsp@gmail.com'].id}/decision", M,
             {"decision": "more_info", "reason": "TEST: please share your dMRV methodology note."})
check("request more information", st in (200, 409), r)

db.expire_all()
tok = {}
for key in ("pd", "vvb"):
    u = db.query(User).filter_by(email=ACC[key]["email"]).one()
    st, r = call("POST", "/auth/accept-invite", json_body={"token": make_token(u.id, typ="invite", sv=u.session_version or 0), "password": ACC[key]["password"]})
    check(f"{key} accepts invite and sets password", st == 200, r)
    st, r = call("POST", "/auth/login", json_body={"email": ACC[key]["email"], "password": ACC[key]["password"], "return_token": True})
    check(f"{key} signs in with email + password", st == 200, r)
    tok[key] = r["token"] if st == 200 else session_token(u)
    ACC[key]["org_id"] = u.organization_id
PD, VVB = tok["pd"], tok["vvb"]

# ------------------------------------------------------------------ participant uploads, variable sizes and types
print("== Participant uploads")
ONB, AGR, IMP, MEET = "01 Onboarding documents", "02 Agreements", "03 Implementation", "04 Meetings and decisions"
uploads = [
    (PD, "pd", ONB, "Project Design Document - Agroforestry Karnataka", "PDD.pdf",
     pdf("Project design document: agroforestry on smallholder farms, soil organic carbon and tree biomass", 1 * MB)),
    (PD, "pd", ONB, "Land ownership records (scanned)", "land-records.pdf",
     scan("Land ownership records | Survey 112 and 114, Tumakuru district | Mangrove and agroforestry parcels")),
    (PD, "pd", IMP, "Farmer enrolment list", "farmers.xlsx",
     xlsx([["Farmer", "Village", "Hectares"]] + [[f"Farmer {i}", f"Village {i % 9}", round(random.uniform(0.5, 4), 2)] for i in range(400)])),
    (PD, "pd", IMP, "Baseline survey photo", "baseline.jpg", scan("Baseline survey 2026 | Soil sampling plot 7", "JPEG")),
    (PD, "pd", AGR, "Signed MoU with ONCM", "mou.pdf", pdf("Memorandum of understanding between ONCM and Green Carbon Demo", 6 * MB)),
    (PD, "pd", MEET, "Site monitoring report Q3", "monitoring-q3.pdf", pdf("Quarterly monitoring report: survival rate of saplings", 20 * MB)),
    (VVB, "vvb", ONB, "Validation report template", "validation.docx",
     docx("Validation report template for third party verification and audit of carbon projects")),
    (VVB, "vvb", ONB, "Accreditation certificate", "accreditation.pdf", pdf("Accreditation certificate for validation and verification body", 3 * MB)),
]
docs = {}
for token, who, folder, title, fname, content in uploads:
    t0 = time.time()
    st, r = call("POST", "/me/documents", token, form={"category": folder, "title": f"TEST - {title}"}, files={"file": (fname, content)})
    check(f"{who} uploads {fname} ({len(content) / MB:.2f} MB) in {time.time() - t0:.1f}s -> {r.get('code') if isinstance(r, dict) else r}",
          st == 201, r)
    if st == 201:
        docs[title] = r
st, r = call("POST", "/me/documents", PD, form={"category": ONB, "title": "TEST - duplicate"}, files={"file": ("PDD-copy.pdf", uploads[0][5])})
check("identical file again is refused (409)", st == 409, r)
st, r = call("POST", "/me/documents", PD, form={"category": ONB, "title": "TEST - virus"}, files={"file": ("eicar.txt", EICAR)})
check("EICAR test virus is refused by ClamAV (400)", st == 400 and "security scan" in str(r), r)
st, r = call("POST", "/me/documents", PD, form={"category": ONB, "title": "TEST - too big"}, files={"file": ("huge.pdf", pdf("big", 26 * MB))})
check("file over 25 MB is refused (413)", st == 413, r)
st, r = call("POST", "/me/documents", PD, form={"category": ONB, "title": "TEST - exe"}, files={"file": ("tool.exe", b"MZ" + b"0" * 100)})
check("disallowed file type is refused (400)", st == 400, r)

# ------------------------------------------------------------------ review workflow on participant documents
print("== Review workflow (participant documents)")
pdd, val, mou = docs["Project Design Document - Agroforestry Karnataka"], docs["Validation report template"], docs["Signed MoU with ONCM"]
st, r = call("POST", f"/admin/documents/{pdd['id']}/review", A2, {"decision": "start_review"})
check("reviewer starts review of the PDD", st == 200 and r["status"] == "under_review", r)
st, r = call("POST", f"/me/documents/{pdd['id']}/versions", PD, files={"file": ("PDD-v2.pdf", pdf("changed during review", 100000))})
check("participant cannot replace a document while it is under review (409)", st == 409, r)
st, r = call("POST", f"/admin/documents/{pdd['id']}/review", A2, {"decision": "changes", "feedback": "TEST: please add the leakage assessment in section 4."})
check("reviewer requests changes with feedback", st == 200 and r["status"] == "changes_requested", r)
st, r = call("GET", "/me/documents", PD)
check("participant sees the reviewer's feedback on the document", st == 200 and "leakage" in json.dumps(r), r if st != 200 else "")
st, r = call("POST", f"/me/documents/{pdd['id']}/versions", PD,
             files={"file": ("PDD-v2.pdf", pdf("Project design document v2 with leakage assessment for agroforestry", 2 * MB))})
check("participant uploads the corrected version (v2)", st == 201 and r["current_version"] == 2, r)
for step in ({"decision": "start_review"}, {"decision": "recommend", "comment": "TEST: v2 is complete"}):
    st, r = call("POST", f"/admin/documents/{pdd['id']}/review", A2, step)
    check(f"reviewer: {step['decision']}", st == 200, r)
st, r = call("POST", f"/admin/documents/{pdd['id']}/review", A2, {"decision": "approve", "confirm_classification": True})
check("secondary admin cannot give final approval (two-person rule, 403)", st == 403, r)
st, r = call("POST", f"/admin/documents/{pdd['id']}/review", M, {"decision": "approve", "confirm_classification": True, "access_level": "restricted"})
check("Master Admin gives final approval", st == 200 and r["status"] == "internally_approved", r)
st, r = call("POST", f"/admin/documents/{val['id']}/review", A2, {"decision": "start_review"})
st, r = call("POST", f"/admin/documents/{val['id']}/review", M, {"decision": "reject", "feedback": "TEST: use the ONCM template instead."})
check("Master Admin rejects the VVB template with a reason", st == 200 and r["status"] == "rejected", r)
st, r = call("GET", f"/me/documents/{mou['id']}/download", PD)
ok = False
if st == 200:
    url = r["url"]
    with urllib.request.urlopen(url if url.startswith("http") else "http://localhost:8000" + url, timeout=120) as f:
        ok = len(f.read()) == len(uploads[4][5])
check("participant downloads their 6 MB MoU from S3 intact", ok, r)

# ------------------------------------------------------------------ repository documents and publication
print("== Repository documents and publication")
repo = [
    ("Pilot operating guidelines", "CM", "pilot_shared", "", "Operating guidelines for all pilot participants: registration, issuance and retirement", 500000),
    ("VVB audit checklist", "CM", "role_specific", "VVB", "Audit checklist for validation and verification bodies, sampling and site visits", 300000),
    ("Carbon market notification 2026", "POL", "public", "", "Public notification on the Indian carbon market compliance mechanism 2026", 200000),
    ("Soil carbon research paper", "RES", "request_only", "", "Research paper on soil organic carbon measurement in semi arid agroforestry", 4 * MB),
    ("Term sheet for Green Carbon Demo", "CM", "restricted", "", "Commercial term sheet for credit offtake, confidential", 150000),
    ("Internal draft - pricing note", "CDP", "internal", "", "Internal pricing note, not for participants", 100000),
]
rdocs = {}
for title, cat, level, actors, text, size in repo:
    st, r = call("POST", "/admin/documents", A2, form={"title": f"TEST - {title}", "repo_category": cat, "access_level": level,
                                                       "target_actor_types": actors}, files={"file": (title.replace(" ", "-") + ".pdf", pdf(text, size))})
    check(f"secondary admin uploads '{title}' ({level}) -> {r.get('code') if isinstance(r, dict) else r}", st == 201, r)
    rdocs[title] = r
    if level == "internal" or st != 201:
        continue
    call("POST", f"/admin/documents/{r['id']}/review", M, {"decision": "start_review"})  # the maker cannot review
    st, r2 = call("POST", f"/admin/documents/{r['id']}/review", A2, {"decision": "approve", "confirm_classification": True})
    check("maker cannot approve their own document (403)", st == 403, r2)
    st, r2 = call("POST", f"/admin/documents/{r['id']}/review", M, {"decision": "approve", "confirm_classification": True})
    check(f"Master Admin approves '{title}'", st == 200, r2)
    pub = {"organization_ids": [ACC["pd"]["org_id"]]} if level == "restricted" else ({"actor_types": ["VVB"]} if level == "role_specific" else {})
    st, r2 = call("POST", f"/admin/documents/{r['id']}/publish", M, pub)
    check(f"published '{title}' as {level}", st == 201, r2)
st, r = call("POST", f"/admin/documents/{rdocs['Internal draft - pricing note']['id']}/publish", M, {})
check("an unapproved internal draft cannot be published (409)", st == 409, r)

print("== Who sees what")
def titles(token, path="/me/resources"):
    st, r = call("GET", path, token)
    return {d["title"].replace("TEST - ", "") for d in r} if st == 200 else set()
pd_res, vvb_res = titles(PD), titles(VVB)
check("Project Developer sees pilot guidelines + its own term sheet", {"Pilot operating guidelines", "Term sheet for Green Carbon Demo"} <= pd_res, pd_res)
check("Project Developer does NOT see the VVB-only checklist", "VVB audit checklist" not in pd_res, pd_res)
check("VVB sees pilot guidelines + VVB checklist", {"Pilot operating guidelines", "VVB audit checklist"} <= vvb_res, vvb_res)
check("VVB does NOT see the other organisation's term sheet", "Term sheet for Green Carbon Demo" not in vvb_res, vvb_res)
check("nobody outside ONCM sees the internal draft", "Internal draft - pricing note" not in pd_res | vvb_res)
st, r = call("GET", f"/me/documents/{docs['Accreditation certificate']['id']}/download", PD)
check("Project Developer cannot download the VVB's document (404)", st == 404, r)
st, r = call("GET", "/public/resources")
check("public notification is visible without signing in", st == 200 and "Carbon market notification" in json.dumps(r), r if st != 200 else "")
st, r = call("GET", "/admin/overview", PD)
check("participant cannot open admin pages (403)", st == 403, r)

print("== Request-only access")
soil = rdocs["Soil carbon research paper"]
check("research paper is listed in the catalogue, not yet in resources", "Soil carbon research paper" in titles(PD, "/me/catalogue") and "Soil carbon research paper" not in pd_res)
st, r = call("POST", "/me/access-requests", PD, {"document_id": soil["id"], "purpose": "TEST: need the SOC method for our baseline"})
check("Project Developer requests access", st == 201, r)
st, reqs = call("GET", "/admin/access-requests", A2)
rid = next((x["id"] for x in reqs if x.get("document_id") == soil["id"] and x["status"] == "pending"), None) if st == 200 else None
st, r = call("POST", f"/admin/access-requests/{rid}/decision", A2, {"decision": "approve", "note": "TEST approved"})
check("secondary admin (can_share) approves the request", st == 200, r)
check("research paper now appears in the Project Developer's resources", "Soil carbon research paper" in titles(PD))

print("== Search (waits for background indexing)")
time.sleep(25)
def admin_find(q):
    st, r = call("GET", "/admin/documents?" + urllib.parse.urlencode({"q": q}), M)
    return [d["title"] for d in r] if st == 200 else []
check("search by document code", any("Project Design Document" in t for t in admin_find(pdd["code"])))
check("search finds text inside a scanned PDF (OCR): 'Tumakuru'", any("Land ownership" in t for t in admin_find("Tumakuru")))
check("search by meaning: 'auditor sampling visits' finds the VVB checklist", any("VVB audit checklist" in t for t in admin_find("auditor sampling visits")))
st, r = call("GET", "/me/resources?" + urllib.parse.urlencode({"q": "term sheet offtake"}), VVB)
check("VVB's search never returns the other organisation's term sheet", st == 200 and not any("Term sheet" in d["title"] for d in r), r)

print("== Reports and notifications")
st, r = call("GET", "/admin/reports/export", M)
check("Excel report downloads", st == 200 and isinstance(r, bytes), r if st != 200 else "")
st, r = call("GET", "/admin/notifications", M)
check("notifications were recorded (email sending not configured: 'logged')", st == 200 and len(r) > 5, r if st != 200 else "")

print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
