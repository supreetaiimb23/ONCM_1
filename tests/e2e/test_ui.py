"""Browser tests (Playwright) against a live server. Excluded by default; run with `pytest -m e2e`."""
import re

import pytest
from playwright.sync_api import Page, expect

from tests.conftest import MASTER_PW

pytestmark = pytest.mark.e2e


def sign_in(page: Page, base: str, email: str, password: str):
    page.goto(base + "/")
    page.locator("#e").fill(email)
    page.locator("#p").fill(password)
    page.get_by_role("button", name="Sign in").click()


def test_master_admin_signs_in_and_sees_overview(page: Page, live_server, master):
    sign_in(page, live_server, master.email, MASTER_PW)
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    nav = page.get_by_role("navigation", name="Main")
    for item in ("Onboarding Requests", "Audit log", "Admin accounts"):
        expect(nav.get_by_role("link", name=item)).to_be_visible()


def test_wrong_password_shows_error(page: Page, live_server, master):
    sign_in(page, live_server, master.email, "not-the-password")
    expect(page.locator("#toast")).to_contain_text("Incorrect email or password")
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()


def test_participant_uploads_a_document(page: Page, live_server, make_org, tmp_path):
    _, p = make_org()
    f = tmp_path / "registration.pdf"
    f.write_bytes(b"%PDF-1.4\nregistration form")

    sign_in(page, live_server, p.email, "Partner!Pass123")
    page.get_by_role("navigation", name="Main").get_by_role("link", name="My documents").click()
    page.get_by_role("button", name="Upload document").click()
    form = page.locator("#f-pup")
    form.locator("input[name=title]").fill("Registration form")
    form.locator("input[type=file]").set_input_files(str(f))
    form.get_by_role("button", name="Submit for review").click()

    expect(page.locator("#toast")).to_contain_text("Submitted for review")
    row = page.locator("tr", has_text="Registration form")
    expect(row).to_contain_text(re.compile(r"ONCM-PPD-\d{4}-V1-\d{4}"))


def test_participant_does_not_see_admin_navigation(page: Page, live_server, make_org):
    _, p = make_org()
    sign_in(page, live_server, p.email, "Partner!Pass123")
    expect(page.get_by_role("link", name="My progress")).to_be_visible()
    expect(page.get_by_role("link", name="Audit log")).to_have_count(0)
    page.goto(live_server + "/#/audit")
    expect(page.get_by_role("link", name="Audit log")).to_have_count(0)


def test_public_pages_without_login(page: Page, live_server):
    page.goto(live_server + "/")
    page.get_by_role("link", name="Browse public resources").click()
    expect(page.get_by_role("heading", name="Public resources")).to_be_visible()
    page.goto(live_server + "/#/join")
    page.reload()
    expect(page.get_by_role("heading", name="Request to join ONCM")).to_be_visible()


def test_sign_out(page: Page, live_server, master):
    sign_in(page, live_server, master.email, MASTER_PW)
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.get_by_role("button", name="Sign out").click()
    expect(page.get_by_role("heading", name="Sign in")).to_be_visible()
    assert page.evaluate("localStorage.getItem('oncm_token')") is None


def test_no_content_security_policy_violations(page: Page, live_server, master):
    """The CSP must not block anything the portal itself uses (scripts, styles, fonts, images)."""
    violations = []
    page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)
    sign_in(page, live_server, master.email, MASTER_PW)
    page.wait_for_timeout(1500)
    for route in ("#/join", "#/public"):
        page.goto(live_server + "/" + route)
        page.wait_for_timeout(800)
    assert violations == []


def _code_for(page: Page, ahead=0):
    import time
    from app import mfa
    key = page.locator("code").first.inner_text().replace(" ", "")
    return key, mfa._code(key, int(time.time() // mfa.STEP) + ahead)


def test_admin_two_step_verification_end_to_end(page: Page, live_server, make_admin, monkeypatch):
    """Required for admins: forced setup with QR + recovery codes, then sign-in asks for the code."""
    import time
    from app import mfa
    from app.core import settings
    monkeypatch.setattr(settings, "mfa_required_for_admins", True)
    a = make_admin()
    sign_in(page, live_server, a.email, "Admin!Pass123")
    expect(page.get_by_role("heading", name="Set up two-step verification")).to_be_visible()
    expect(page.locator("#mfa-qr svg")).to_be_visible()
    key, code = _code_for(page)
    page.locator("#mon").fill(code)
    page.get_by_role("button", name="Turn on two-step verification").click()
    expect(page.locator("pre")).to_contain_text("-")  # recovery codes shown once
    assert len(page.locator("pre").inner_text().split()) == 10
    page.get_by_role("button", name="I have saved them").click()
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    # the session is not readable by page scripts
    assert "oncm_session" not in page.evaluate("document.cookie")
    assert page.evaluate("Object.keys(localStorage).filter(k => k.includes('token')).length") == 0

    page.get_by_role("button", name="Sign out").click()
    sign_in(page, live_server, a.email, "Admin!Pass123")
    expect(page.get_by_role("heading", name="Two-step verification")).to_be_visible()
    page.locator("#mc").fill("000000")
    page.get_by_role("button", name="Verify").click()
    expect(page.locator("#toast")).to_contain_text("not valid")
    page.locator("#mc").fill(mfa._code(key, int(time.time() // mfa.STEP) + 1))
    page.get_by_role("button", name="Verify").click()
    expect(page.get_by_role("heading", name="Overview")).to_be_visible()
    page.get_by_role("navigation", name="Main").get_by_role("link", name="My account").click()
    expect(page.get_by_text("recovery code(s) left")).to_be_visible()
