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
