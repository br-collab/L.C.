"""The optional public deployment is unauthenticated, read-only and fail closed."""

from __future__ import annotations

import pytest
from cop_fakes import Rig

from cop.app import CONTENT_SECURITY_POLICY, PANELS, SECTIONS
from cop.settings import load_settings

PUBLIC_ENV = {"LEGATE_PUBLIC": "1"}
PUBLIC_BANNER = "PUBLIC VIEW"
FEED_VARIABLES = (
    "ATREIDES_AGENTS_URL",
    "ATREIDES_ADVISORIES_URL",
    "ATREIDES_BREAKS_URL",
    "ATREIDES_TRACEABILITY_URL",
    "C2_ESCALATIONS_URL",
    "LC_LAYER_CLOCK_URL",
    "LC_LIFECYCLE_BASE_URL",
)


def test_private_mode_still_redirects_to_login() -> None:
    response = Rig().app_client().get("/")
    assert response.status_code == 302
    assert response.location == "/login"


def test_public_dashboard_needs_no_session_and_exposes_no_login_controls() -> None:
    response = Rig().app_client(PUBLIC_ENV).get("/")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert PUBLIC_BANNER in body
    assert "Sign out" not in body
    assert "operator_key" not in body


def test_public_responses_keep_security_headers_and_block_indexing() -> None:
    response = Rig().app_client(PUBLIC_ENV).get("/")
    assert "noindex" in response.headers["X-Robots-Tag"]
    assert response.headers["Content-Security-Policy"] == CONTENT_SECURITY_POLICY
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"


def test_public_login_logout_and_other_posts_are_unavailable() -> None:
    client = Rig().app_client(PUBLIC_ENV)
    for path in ("/login", "/logout"):
        response = client.post(path)
        assert response.status_code == 404
        assert response.content_type == "text/plain; charset=utf-8"
    response = client.post("/")
    assert response.status_code == 405
    assert response.content_type == "text/plain; charset=utf-8"
    login = client.get("/login")
    assert login.status_code == 302 and login.location == "/"


@pytest.mark.parametrize(
    "path",
    [
        *(f"/section/{name}" for name in SECTIONS),
        *(f"/panel/{name}" for name in PANELS),
    ],
)
def test_every_public_section_and_panel_is_visible(path: str) -> None:
    response = Rig().app_client(PUBLIC_ENV).get(path)
    assert response.status_code == 200
    assert PUBLIC_BANNER in response.get_data(as_text=True)


def test_public_settings_need_no_operator_credentials() -> None:
    settings = load_settings(PUBLIC_ENV)
    assert settings.public is True
    assert settings.operator_key is None
    assert settings.session_secret is None
    assert settings.configured


@pytest.mark.parametrize("credential", ("LEGATE_OPERATOR_KEY", "LEGATE_SESSION_SECRET"))
def test_public_settings_refuse_operator_credentials(credential: str) -> None:
    settings = load_settings({**PUBLIC_ENV, credential: "secret"})
    assert not settings.configured
    assert settings.problems == (
        "LEGATE_OPERATOR_KEY or LEGATE_SESSION_SECRET is set while LEGATE_PUBLIC=1",
    )


def test_public_settings_refuse_demo_mode() -> None:
    settings = load_settings({**PUBLIC_ENV, "LEGATE_DEMO": "1"})
    assert not settings.configured
    assert "LEGATE_DEMO is set while LEGATE_PUBLIC=1" in settings.problems


@pytest.mark.parametrize("variable", FEED_VARIABLES)
def test_public_settings_refuse_each_feed_without_explicit_permission(variable: str) -> None:
    settings = load_settings({**PUBLIC_ENV, variable: "https://feed.example.invalid/data"})
    assert not settings.configured
    assert any(variable in problem for problem in settings.problems)


@pytest.mark.parametrize("variable", FEED_VARIABLES)
def test_public_settings_allow_each_feed_with_explicit_permission(variable: str) -> None:
    settings = load_settings(
        {
            **PUBLIC_ENV,
            "LEGATE_PUBLIC_ALLOW_FEEDS": "1",
            variable: "https://feed.example.invalid/data",
        }
    )
    assert settings.configured


def test_misconfigured_public_service_refuses_pages_and_health() -> None:
    client = Rig().app_client({**PUBLIC_ENV, "LEGATE_OPERATOR_KEY": "ambiguous"})
    page = client.get("/")
    assert page.status_code == 503
    assert "misconfigured and refuses to serve pages" in page.get_data(as_text=True)
    health = client.get("/healthz")
    assert health.status_code == 503
    assert health.get_json() == {"status": "misconfigured"}


def test_public_health_is_json_with_an_ok_body() -> None:
    response = Rig().app_client(PUBLIC_ENV).get("/healthz")
    assert response.status_code == 200
    assert response.content_type == "application/json"
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["refresher"] == "idle"
