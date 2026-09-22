"""Settings for the COP-0 (Common Operating Picture, layer 0) program picture.

``PRODUCT_NAME`` is the only place the product name is written. The name is a working
name (JUM-D-27) and may change, so every template and message reads it from here.

Configuration comes from environment variables and is read once, by ``load_settings``.
Secrets are never included in ``repr`` output or log lines.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

PRODUCT_NAME = "Legate"

# Sources ---------------------------------------------------------------------------------

GITHUB_OWNER = "br-collab"
REPOSITORIES: tuple[str, ...] = ("aureon", "Project-Atreides", "cannae-kernel", "L.C.")
AUREON_REPOSITORY = "aureon"
MAIN_BRANCH = "main"
GITHUB_API_URL = "https://api.github.com"
GITHUB_WEB_URL = "https://github.com"
AUREON_SNAPSHOT_URL = "https://aureon-production.up.railway.app/api/snapshot"

# The Atreides Phase A activation snapshot (W3 § WP-A3). There is deliberately no
# default URL: the document is not published anywhere yet, and a guessed address
# would fail as a ConnectionError, which reads like an outage rather than like
# "not configured". Unset, the Agents panel shows INDETERMINATE and says which
# variable to set — the same rule the panel exists to enforce, applied to the
# panel's own source.
AGENTS_SNAPSHOT_SOURCE = "Atreides activation snapshot (ATREIDES_AGENTS_URL)"

# COP-1 panel 8. In demo mode the rows are invented (cop/demo.py). There is no live
# source yet: the middle layer is Wave 4, and two of the three layers a row needs
# publish nothing a board could read. Unconfigured, the panel reports that rather
# than rendering an empty table, which would read as "no lifecycles" instead of
# "no source".
LIFECYCLE_SOURCE = "Lifecycle board (synthetic; no live source until Wave 4)"
LIFECYCLE_SOURCE_UNSET = (
    "no lifecycle source is connected; the middle layer that would supply one is Wave 4"
)
AGENTS_SOURCE_UNSET = "ATREIDES_AGENTS_URL is not set, so no activation snapshot is being read"
PROGRAM_FILE = Path(__file__).with_name("program.yaml")

HTTP_TIMEOUT_SECONDS = 10.0

# Refresh and staleness -------------------------------------------------------------------

REFRESH_SECONDS_WITH_TOKEN = 60
REFRESH_SECONDS_WITHOUT_TOKEN = 600
STALE_AFTER = timedelta(minutes=5)
PAGE_RELOAD_SECONDS = 60

# Access ----------------------------------------------------------------------------------

LOGIN_MAX_FAILURES_PER_ADDRESS = 5
LOGIN_MAX_FAILURES_GLOBAL = 20
LOGIN_FAILURE_WINDOW = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=12)
SESSION_COOKIE_NAME = "legate_session"

# In production the secrets must be long enough that a session cookie cannot be forged by
# guessing the signing secret, or the operator key guessed within the login rate limit.
PRODUCTION_MIN_SESSION_SECRET_LENGTH = 32
PRODUCTION_MIN_OPERATOR_KEY_LENGTH = 16

# Any of these variables marks a production (or production-like) process. Railway sets
# them. Local development flags are refused when one is present.
PRODUCTION_MARKERS: tuple[str, ...] = (
    "PORT",
    "RAILWAY_ENVIRONMENT",
    "RAILWAY_ENVIRONMENT_NAME",
    "RAILWAY_PROJECT_ID",
)

ENV_OPERATOR_KEY = "LEGATE_OPERATOR_KEY"
ENV_SESSION_SECRET = "LEGATE_SESSION_SECRET"
ENV_GITHUB_TOKEN = "GITHUB_TOKEN"
ENV_INSECURE_LOCAL = "LEGATE_INSECURE_LOCAL"
ENV_DEMO = "LEGATE_DEMO"
ENV_AGENTS_URL = "ATREIDES_AGENTS_URL"


@dataclass(frozen=True)
class Settings:
    """Validated configuration. ``problems`` lists every reason to refuse to serve."""

    operator_key: str | None = field(repr=False)
    session_secret: str | None = field(repr=False)
    github_token: str | None = field(repr=False)
    agents_url: str | None
    """Where the Atreides activation snapshot is published. ``None`` when unset,
    which the Agents panel reports as INDETERMINATE rather than as a failure."""
    insecure_local: bool
    demo: bool
    production: bool
    problems: tuple[str, ...]

    @property
    def configured(self) -> bool:
        return not self.problems

    @property
    def github_authenticated(self) -> bool:
        return self.github_token is not None

    @property
    def refresh_seconds(self) -> int:
        if self.demo or self.github_authenticated:
            return REFRESH_SECONDS_WITH_TOKEN
        return REFRESH_SECONDS_WITHOUT_TOKEN


def _non_empty(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name, "")
    return value if value.strip() else None


def _flag(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "") == "1"


def load_settings(env: Mapping[str, str]) -> Settings:
    """Read settings from ``env``. Never raises: problems are collected instead."""
    operator_key = _non_empty(env, ENV_OPERATOR_KEY)
    session_secret = _non_empty(env, ENV_SESSION_SECRET)
    github_token = _non_empty(env, ENV_GITHUB_TOKEN)
    agents_url = _non_empty(env, ENV_AGENTS_URL)
    insecure_local = _flag(env, ENV_INSECURE_LOCAL)
    demo = _flag(env, ENV_DEMO)
    production = any(name in env for name in PRODUCTION_MARKERS)

    problems: list[str] = []
    if operator_key is None:
        problems.append(f"{ENV_OPERATOR_KEY} is not set")
    if session_secret is None:
        problems.append(f"{ENV_SESSION_SECRET} is not set")
    if production:
        if insecure_local:
            problems.append(f"{ENV_INSECURE_LOCAL} is set in a production environment")
        if demo:
            problems.append(f"{ENV_DEMO} is set in a production environment")
        if session_secret is not None and (
            len(session_secret) < PRODUCTION_MIN_SESSION_SECRET_LENGTH
        ):
            problems.append(
                f"{ENV_SESSION_SECRET} is shorter than "
                f"{PRODUCTION_MIN_SESSION_SECRET_LENGTH} characters"
            )
        if operator_key is not None and len(operator_key) < PRODUCTION_MIN_OPERATOR_KEY_LENGTH:
            problems.append(
                f"{ENV_OPERATOR_KEY} is shorter than "
                f"{PRODUCTION_MIN_OPERATOR_KEY_LENGTH} characters"
            )

    return Settings(
        operator_key=operator_key,
        session_secret=session_secret,
        github_token=github_token,
        agents_url=agents_url,
        insecure_local=insecure_local,
        demo=demo,
        production=production,
        problems=tuple(problems),
    )
