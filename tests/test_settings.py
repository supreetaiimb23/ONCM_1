"""Startup refuses insecure default secrets outside APP_ENV=development."""
import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import main
from app.core import Settings, check_secrets

GOOD = {"secret_key": "x" * 32, "master_admin_password": "Some!Other123", "app_env": "production"}


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, **{**GOOD, **kw})


def test_good_settings_pass():
    assert check_secrets(_settings()) == []


@pytest.mark.parametrize("key", ["change-me", "change-me-to-a-long-random-string", "", "x" * 31])
def test_default_or_short_secret_key_refused(key):
    with pytest.raises(RuntimeError, match="SECRET_KEY"):
        check_secrets(_settings(secret_key=key))


def test_default_master_password_refused():
    with pytest.raises(RuntimeError, match="MASTER_ADMIN_PASSWORD"):
        check_secrets(_settings(master_admin_password="ChangeMe!12345"))


def test_app_env_defaults_to_production():
    with pytest.raises(RuntimeError):
        check_secrets(Settings(_env_file=None, secret_key="change-me"))


@pytest.mark.parametrize("env", ["development", " Development "])
def test_development_allows_defaults_with_warnings(env):
    problems = check_secrets(_settings(app_env=env, secret_key="change-me", master_admin_password="ChangeMe!12345"))
    assert len(problems) == 2


def test_lifespan_fails_fast(monkeypatch):
    monkeypatch.setattr(main.settings, "secret_key", "change-me")
    monkeypatch.setattr(main.settings, "app_env", "production")
    monkeypatch.setattr(main, "migrate", lambda: pytest.fail("ran past the secret check"))

    async def start():
        async with main.lifespan(main.app):
            pass

    with pytest.raises(RuntimeError, match="Refusing to start"), ThreadPoolExecutor(1) as ex:
        ex.submit(asyncio.run, start()).result()  # own thread: Playwright's e2e tests leave an event loop running
