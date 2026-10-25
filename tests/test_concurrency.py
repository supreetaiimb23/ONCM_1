"""Concurrent uploads against a real single-process server, as deployed in Docker.

Regression test for the upload freeze: async upload routes made blocking database calls on the event loop, so two
uploads into the same repository category deadlocked on the document-code row and froze the whole server.
Run with TEST_DATABASE_URL pointing at PostgreSQL for production parity (SQLite serialises writes differently).
"""
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from tests.conftest import auth, pdf_bytes, uid

pytestmark = pytest.mark.slow
TIMEOUT = 60


def _upload(base, headers, category, size, title):
    return httpx.post(f"{base}/api/admin/documents", headers=headers, timeout=TIMEOUT,
                      data={"title": title, "repo_category": category, "access_level": "internal"},
                      files={"file": (f"{title}.pdf", pdf_bytes(size), "application/pdf")})


def _probe(base, stop: threading.Event, latencies: list):
    while not stop.is_set():
        t = time.perf_counter()
        try:
            httpx.get(f"{base}/api/health", timeout=10)
            latencies.append(time.perf_counter() - t)
        except httpx.TimeoutException:
            latencies.append(10.0)
        time.sleep(0.02)


@pytest.mark.parametrize("n,size_mb", [(2, 5), (10, 2), (5, 20)])
def test_concurrent_uploads_same_category(live_server, master, n, size_mb):
    headers = auth(master)
    tag = uid()
    stop, lat = threading.Event(), []
    probe = threading.Thread(target=_probe, args=(live_server, stop, lat), daemon=True)
    probe.start()
    with ThreadPoolExecutor(n) as pool:
        rs = list(pool.map(lambda i: _upload(live_server, headers, "PPD", size_mb * 1024 * 1024, f"conc-{tag}-{i}"), range(n)))
    stop.set()
    probe.join()

    assert [r.status_code for r in rs] == [201] * n, [r.text[:120] for r in rs if r.status_code != 201]
    codes = [r.json()["code"] for r in rs]
    assert len(set(codes)) == n, codes
    # other users keep being served while uploads run
    assert max(lat) < 2.0, f"health check stalled for {max(lat):.2f}s during uploads"
    assert httpx.get(f"{live_server}/api/health", timeout=5).status_code == 200


def test_concurrent_participant_submissions(live_server, make_org):
    """Folders 01, 03 and 04 all map to PPD, so partners uploading at the same time compete for one code sequence."""
    users = [make_org()[1] for _ in range(6)]
    folders = ["01 Onboarding documents", "03 Implementation", "04 Meetings and decisions"]

    def submit(i):
        return httpx.post(f"{live_server}/api/me/documents", headers=auth(users[i]), timeout=TIMEOUT,
                          data={"title": f"sub-{i}", "category": folders[i % 3]},
                          files={"file": (f"s{i}.pdf", pdf_bytes(1024 * 1024), "application/pdf")})

    with ThreadPoolExecutor(len(users)) as pool:
        rs = list(pool.map(submit, range(len(users))))
    assert [r.status_code for r in rs] == [201] * len(users), [r.text[:120] for r in rs]
    assert len({r.json()["code"] for r in rs}) == len(users)


def test_reads_continue_during_uploads(live_server, master):
    headers = auth(master)
    with ThreadPoolExecutor(12) as pool:
        ups = [pool.submit(_upload, live_server, headers, "CM", 5 * 1024 * 1024, f"mix-{uid()}") for _ in range(4)]
        reads = [pool.submit(httpx.get, f"{live_server}/api/admin/overview", headers=headers, timeout=TIMEOUT) for _ in range(8)]
        assert all(f.result().status_code == 201 for f in ups)
        assert all(f.result().status_code == 200 for f in reads)


def _together(fns):
    """Run the calls at the same moment (a barrier releases all threads at once)."""
    gate = threading.Barrier(len(fns))

    def run(f):
        gate.wait()
        return f()
    with ThreadPoolExecutor(len(fns)) as pool:
        return list(pool.map(run, fns))


def test_simultaneous_new_versions_get_distinct_numbers(live_server, master):
    headers = auth(master)
    for _ in range(3):
        doc = _upload(live_server, headers, "POL", 2048, f"ver-{uid()}").json()

        def new_version():
            return httpx.post(f"{live_server}/api/admin/documents/{doc['id']}/versions", headers=headers, timeout=TIMEOUT,
                              files={"file": ("v.pdf", pdf_bytes(512 * 1024), "application/pdf")}).status_code
        assert _together([new_version] * 4) == [201] * 4
        det = httpx.get(f"{live_server}/api/admin/documents/{doc['id']}", headers=headers, timeout=TIMEOUT).json()
        assert sorted(v["version"] for v in det["versions"]) == [1, 2, 3, 4, 5]


def test_conflicting_review_decisions_cannot_both_win(live_server, master, make_admin):
    """Reviewer requests changes while the approver approves: exactly one decision stands, the other gets 409."""
    reviewer, approver = make_admin(), make_admin()
    for _ in range(3):
        doc = _upload(live_server, auth(master), "POL", 2048, f"rev-{uid()}").json()
        base = f"{live_server}/api/admin/documents/{doc['id']}"
        httpx.post(f"{base}/assign", headers=auth(master), json={"reviewer_id": reviewer.id, "approver_id": approver.id})
        assert httpx.post(f"{base}/review", headers=auth(reviewer), json={"decision": "start_review"}).status_code == 200
        codes = _together([
            lambda: httpx.post(f"{base}/review", headers=auth(reviewer), timeout=TIMEOUT,
                               json={"decision": "changes", "feedback": "fix page 3"}).status_code,
            lambda: httpx.post(f"{base}/review", headers=auth(approver), timeout=TIMEOUT,
                               json={"decision": "approve", "confirm_classification": True, "access_level": "public"}).status_code])
        assert sorted(codes) == [200, 409], codes
        status = httpx.get(base, headers=auth(master), timeout=TIMEOUT).json()["status"]
        assert status == ("changes_requested" if codes[0] == 200 else "internally_approved")
