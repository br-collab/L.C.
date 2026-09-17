"""Access control: login, logout, rate limiting, fail-closed configuration and headers."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from cop_fakes import GOOD_ENV, OPERATOR_KEY, SESSION_SECRET, Rig, login

from cop.app import CONTENT_SECURITY_POLICY, PANELS, create_app
from cop.settings import (
    LOGIN_FAILURE_WINDOW,
    LOGIN_MAX_FAILURES_GLOBAL,
    LOGIN_MAX_FAILURES_PER_ADDRESS,
    SESSION_COOKIE_NAME,
    load_settings,
)

PAGE_PATHS = ["/", *(f"/panel/{name}" for name in PANELS)]
TEMPLATES = Path(__file__).resolve().parents[2] / "cop" / "templates"


@pytest.mark.parametrize("path", PAGE_PATHS)
def test_every_page_redirects_to_login_without_a_session(path: str) -> None:
    client = Rig().app_client()
    response = client.get(path)
    assert response.status_code == 302
    assert response.location == "/login"


def test_static_and_unknown_paths_do_not_leak_data_without_a_session() -> None:
    client = Rig().app_client()
    assert client.get("/nope").status_code == 302
    css = client.get("/static/cop.css")
    assert css.status_code == 200 and b"--good" in css.data


def test_login_with_correct_key_sets_a_hardened_cookie() -> None:
    client = Rig().app_client()
    response = login(client)
    assert response.status_code == 302 and response.location == "/"
    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith(f"{SESSION_COOKIE_NAME}=")
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=Strict" in cookie
    assert client.get("/").status_code == 200


def test_wrong_key_is_rejected() -> None:
    client = Rig().app_client()
    response = login(client, "wrong key")
    assert response.status_code == 401
    assert b"not correct" in response.data
    assert "Set-Cookie" not in response.headers
    assert client.get("/").status_code == 302


def test_logout_clears_the_session() -> None:
    client = Rig().app_client()
    login(client)
    assert client.get("/").status_code == 200
    response = client.post("/logout")
    assert response.status_code == 302 and response.location == "/login"
    assert f"{SESSION_COOKIE_NAME}=;" in response.headers["Set-Cookie"]
    assert client.get("/").status_code == 302


def test_forged_or_foreign_session_is_rejected() -> None:
    rig = Rig()
    other = create_app(
        load_settings({**GOOD_ENV, "LEGATE_SESSION_SECRET": "x" * 40}),
        rig.refresher,
        rig.clock,
        start_refresher=False,
    ).test_client()
    login(other)
    cookie = other.get_cookie(SESSION_COOKIE_NAME)
    assert cookie is not None
    client = rig.app_client()
    client.set_cookie(SESSION_COOKIE_NAME, cookie.value)
    assert client.get("/").status_code == 302


def test_rotating_the_operator_key_ends_existing_sessions() -> None:
    rig = Rig()
    client = rig.app_client()
    login(client)
    cookie = client.get_cookie(SESSION_COOKIE_NAME)
    assert cookie is not None
    rotated = rig.app_client({**GOOD_ENV, "LEGATE_OPERATOR_KEY": "a-brand-new-operator-key"})
    rotated.set_cookie(SESSION_COOKIE_NAME, cookie.value)
    assert rotated.get("/").status_code == 302


def test_failed_logins_are_rate_limited_per_address() -> None:
    rig = Rig()
    client = rig.app_client()
    for _ in range(LOGIN_MAX_FAILURES_PER_ADDRESS):
        assert login(client, "wrong").status_code == 401
    # Locked: even the correct key is refused until the window passes.
    limited = login(client)
    assert limited.status_code == 429
    assert b"Too many failed attempts" in limited.data
    assert client.get("/").status_code == 302

    rig.clock.advance(seconds=LOGIN_FAILURE_WINDOW.total_seconds() + 1)
    assert login(client).status_code == 302
    assert client.get("/").status_code == 200


def test_failed_logins_are_rate_limited_globally() -> None:
    rig = Rig()
    client = rig.app_client()
    for i in range(LOGIN_MAX_FAILURES_GLOBAL):
        address = f"10.0.{i // 250}.{i % 250 + 1}"
        response = client.post(
            "/login", data={"operator_key": "wrong"}, environ_base={"REMOTE_ADDR": address}
        )
        assert response.status_code == 401
    fresh = client.post(
        "/login", data={"operator_key": OPERATOR_KEY}, environ_base={"REMOTE_ADDR": "192.0.2.99"}
    )
    assert fresh.status_code == 429


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"LEGATE_OPERATOR_KEY": OPERATOR_KEY},
        {"LEGATE_SESSION_SECRET": SESSION_SECRET},
        {"LEGATE_OPERATOR_KEY": "  ", "LEGATE_SESSION_SECRET": SESSION_SECRET},
    ],
)
def test_missing_environment_variables_refuse_to_serve(env: dict[str, str]) -> None:
    rig = Rig()
    client = rig.app_client(env)
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json() == {"status": "misconfigured"}
    for path in [*PAGE_PATHS, "/login", "/static/cop.css"]:
        assert client.get(path).status_code == 503
    assert client.post("/login", data={"operator_key": OPERATOR_KEY}).status_code == 503


@pytest.mark.parametrize(
    "extra",
    [
        {"PORT": "8080", "LEGATE_INSECURE_LOCAL": "1"},
        {"RAILWAY_ENVIRONMENT": "production", "LEGATE_DEMO": "1"},
        {"PORT": "8080", "LEGATE_SESSION_SECRET": "short"},
        {"PORT": "8080", "LEGATE_OPERATOR_KEY": "short"},
    ],
)
def test_local_only_flags_and_weak_secrets_are_refused_in_production(extra: dict[str, str]) -> None:
    client = Rig().app_client({**GOOD_ENV, **extra})
    assert client.get("/healthz").get_json() == {"status": "misconfigured"}
    assert client.get("/login").status_code == 503


def test_insecure_local_relaxes_only_the_secure_flag() -> None:
    client = Rig().app_client({**GOOD_ENV, "LEGATE_INSECURE_LOCAL": "1"})
    cookie = login(client).headers["Set-Cookie"]
    assert "Secure" not in cookie
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie


def test_healthz_is_public_and_ok_when_configured() -> None:
    client = Rig().app_client()
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok", "refresher": "idle"}


def test_security_headers_on_every_response() -> None:
    client = Rig().app_client()
    login(client)
    for path in ["/", "/login", "/healthz", "/static/cop.css"]:
        headers = client.get(path).headers
        assert headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
        assert headers["X-Frame-Options"] == "DENY"
        assert headers["X-Content-Type-Options"] == "nosniff"
    assert "unsafe-inline" not in CONTENT_SECURITY_POLICY
    assert "default-src 'self'" in CONTENT_SECURITY_POLICY
    assert "http" not in CONTENT_SECURITY_POLICY  # no third-party sources


def test_templates_have_no_inline_scripts_styles_or_handlers() -> None:
    for template in TEMPLATES.glob("*.html"):
        text = template.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)", text), template.name
        assert "<style" not in text, template.name
        assert not re.search(r"\sstyle=", text), template.name
        assert not re.search(r"\son[a-z]+=", text), template.name
