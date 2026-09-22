"""Flask application: login, the read-only panels, and ``/healthz``.

Run locally:   ``LEGATE_OPERATOR_KEY=… LEGATE_SESSION_SECRET=… flask --app cop.app run``
Run in production: ``gunicorn --workers=1 --threads=4 --bind 0.0.0.0:$PORT cop.app:app``

Routes never call GitHub or Aureon; they render the refresher's in-memory snapshot. The
only non-GET routes are ``POST /login`` and ``POST /logout``.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

from flask import (
    Flask,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.wrappers.response import Response as BaseResponse

from cop.agents import HttpxAgentsClient
from cop.aureon import HttpxAureonClient
from cop.auth import LoginLimiter, key_fingerprint, keys_match
from cop.demo import DemoAgents, DemoAureon, DemoEscalations, DemoGitHub, DemoLifecycles
from cop.escalations import HttpxEscalationClient
from cop.github import HttpxGitHubClient
from cop.refresher import Clock, Refresher, RefresherOptions, Sources, utc_now
from cop.settings import (
    LOGIN_FAILURE_WINDOW,
    LOGIN_MAX_FAILURES_GLOBAL,
    LOGIN_MAX_FAILURES_PER_ADDRESS,
    PAGE_RELOAD_SECONDS,
    PRODUCT_NAME,
    PROGRAM_FILE,
    SESSION_COOKIE_NAME,
    SESSION_LIFETIME,
    STALE_AFTER,
    Settings,
    load_settings,
)
from cop.view import WORK_STATUS_BADGES, build_page, fmt_age, run_badge, short_sha

log = logging.getLogger(__name__)

PANELS: dict[str, str] = {
    "waves": "Wave board",
    "repositories": "Repositories",
    "aureon": "Live Aureon",
    "agents": "Atreides agents",
    "lifecycles": "Lifecycle board",
    "escalations": "Escalation queue",
    "blindspots": "What this picture cannot see",
    "scheduled": "Nightly and scheduled checks",
    "decisions": "Open decisions",
}

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'none'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)

_SESSION_OPERATOR = "operator"
_SESSION_LOGIN_AT = "login_at"
_PUBLIC_ENDPOINTS = frozenset({"healthz", "login_form", "login_submit", "static"})


@dataclass
class CopState:
    settings: Settings
    refresher: Refresher | None
    limiter: LoginLimiter
    clock: Clock


def build_refresher(settings: Settings, clock: Clock = utc_now) -> Refresher:
    if settings.demo:
        return Refresher(
            sources=Sources(
                github=DemoGitHub(clock),
                aureon=DemoAureon(),
                agents=DemoAgents(clock),
                lifecycles=DemoLifecycles(clock),
                escalations=DemoEscalations(clock),
            ),
            clock=clock,
            options=RefresherOptions(
                program_path=PROGRAM_FILE,
                refresh_seconds=settings.refresh_seconds,
                github_authenticated=True,
                demo=True,
            ),
        )
    return Refresher(
        sources=Sources(
            github=HttpxGitHubClient(settings.github_token),
            aureon=HttpxAureonClient(),
            # None when ATREIDES_AGENTS_URL is unset. The Agents panel then reports
            # "not configured" rather than inventing an address to fail against.
            agents=(
                HttpxAgentsClient(settings.agents_url) if settings.agents_url is not None else None
            ),
            # No live lifecycle source exists: the middle layer is Wave 4. Outside
            # demo mode the board says so rather than rendering an empty table.
            lifecycles=None,
            # None when C2_ESCALATIONS_URL is unset. Panel 12 names it as an
            # unconnected source rather than the queue rendering empty, which
            # would read as "nothing is waiting".
            escalations=(
                HttpxEscalationClient(settings.escalations_url)
                if settings.escalations_url is not None
                else None
            ),
        ),
        clock=clock,
        options=RefresherOptions(
            program_path=PROGRAM_FILE,
            refresh_seconds=settings.refresh_seconds,
            github_authenticated=settings.github_authenticated,
        ),
    )


def _state() -> CopState:
    state: CopState = current_app.extensions["cop"]
    return state


def _logged_in(state: CopState) -> bool:
    settings = state.settings
    if settings.operator_key is None or settings.session_secret is None:
        return False
    stored = session.get(_SESSION_OPERATOR)
    if not isinstance(stored, str):
        return False
    expected = key_fingerprint(settings.operator_key, settings.session_secret)
    return hmac.compare_digest(stored, expected)


def create_app(  # noqa: PLR0915 - route definitions read best in one place
    settings: Settings | None = None,
    refresher: Refresher | None = None,
    clock: Clock = utc_now,
    *,
    start_refresher: bool = True,
) -> Flask:
    """Build the app. Starts no thread: the refresher starts on the first request.

    Tests pass ``start_refresher=False`` and call ``Refresher.refresh_once`` themselves.
    """
    settings = settings if settings is not None else load_settings(os.environ)
    app = Flask(__name__)
    app.config.update(
        # When misconfigured no page is served, but Flask still needs a key to exist.
        SECRET_KEY=settings.session_secret or secrets.token_hex(32),
        SESSION_COOKIE_NAME=SESSION_COOKIE_NAME,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SECURE=not settings.insecure_local,
        SESSION_COOKIE_SAMESITE="Strict",
        PERMANENT_SESSION_LIFETIME=SESSION_LIFETIME,
        MAX_CONTENT_LENGTH=16 * 1024,
    )

    if not settings.configured:
        for problem in settings.problems:
            log.error("Misconfigured, refusing to serve pages: %s", problem)
        refresher = None
    else:
        if refresher is None:
            refresher = build_refresher(settings, clock)
        refresher.load_program_now()
        if settings.insecure_local:
            log.warning("LEGATE_INSECURE_LOCAL: session cookie is not marked Secure")
        if settings.demo:
            log.warning("Demo mode: every value shown is invented")

    app.extensions["cop"] = CopState(
        settings=settings,
        refresher=refresher,
        limiter=LoginLimiter(
            max_per_address=LOGIN_MAX_FAILURES_PER_ADDRESS,
            max_global=LOGIN_MAX_FAILURES_GLOBAL,
            window=LOGIN_FAILURE_WINDOW,
            clock=clock,
        ),
        clock=clock,
    )
    app.jinja_env.globals.update(
        product_name=PRODUCT_NAME,
        panels=PANELS,
        short_sha=short_sha,
        run_badge=run_badge,
        work_badges=WORK_STATUS_BADGES,
        stale_minutes=int(STALE_AFTER.total_seconds() // 60),
        reload_seconds=PAGE_RELOAD_SECONDS,
    )

    @app.before_request
    def guard() -> Response | BaseResponse | tuple[str, int, dict[str, str]] | None:
        state = _state()
        if state.refresher is not None and start_refresher:
            state.refresher.start()
        if request.endpoint == "healthz":
            return None
        if state.refresher is None:
            body = f"{PRODUCT_NAME} is misconfigured and refuses to serve pages."
            return body, 503, {"Content-Type": "text/plain; charset=utf-8"}
        if request.endpoint in _PUBLIC_ENDPOINTS:
            return None
        if not _logged_in(state):
            return redirect(url_for("login_form"))
        return None

    @app.after_request
    def security_headers(response: Response) -> Response:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not _state().settings.insecure_local:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/healthz")
    def healthz() -> tuple[Response, int]:
        state = _state()
        if state.refresher is None:
            return jsonify(status="misconfigured"), 503
        return jsonify(status="ok", refresher="running" if state.refresher.running else "idle"), 200

    @app.get("/login")
    def login_form() -> str | BaseResponse:
        if _logged_in(_state()):
            return redirect(url_for("dashboard"))
        return render_template("login.html", error=None)

    @app.post("/login")
    def login_submit() -> tuple[str, int] | BaseResponse:
        state = _state()
        address = request.remote_addr or "unknown"
        wait = state.limiter.retry_after(address)
        if wait is not None:
            log.warning("Login refused by rate limit for %s", address)
            message = f"Too many failed attempts. Try again in {fmt_age(wait)}."
            return render_template("login.html", error=message), 429
        supplied = request.form.get("operator_key", "")
        settings = state.settings
        if (
            settings.operator_key is not None
            and settings.session_secret is not None
            and keys_match(supplied, settings.operator_key)
        ):
            session.clear()
            session.permanent = True
            session[_SESSION_OPERATOR] = key_fingerprint(
                settings.operator_key, settings.session_secret
            )
            session[_SESSION_LOGIN_AT] = datetime.now(UTC).isoformat()
            return redirect(url_for("dashboard"))
        state.limiter.record_failure(address)
        log.warning("Failed login from %s", address)
        return render_template("login.html", error="That operator key is not correct."), 401

    @app.post("/logout")
    def logout() -> BaseResponse:
        session.clear()
        return redirect(url_for("login_form"))

    def render_panels(selected: tuple[str, ...]) -> str:
        state = _state()
        if state.refresher is None:  # guard() already refuses; kept so this fails closed
            raise RuntimeError("refresher missing")
        page = build_page(state.refresher.snapshot, state.clock())
        return render_template("dashboard.html", page=page, selected=selected)

    @app.get("/")
    def dashboard() -> str:
        return render_panels(tuple(PANELS))

    @app.get("/panel/<name>")
    def panel(name: str) -> str | tuple[str, int]:
        if name not in PANELS:
            return "No such panel", 404
        return render_panels((name,))

    return app


if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

app = create_app()
