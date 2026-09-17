# Legate: COP-0 program picture

Legate is a private, read-only web page: a COP (Common Operating Picture) for Project Cannae Legion. It shows the state of the build program and of the live Aureon service on one URL. "Legate" is a working name (decision JUM-D-27); in code it lives only in `cop/settings.py` as `PRODUCT_NAME`.

It has no write routes and no controls. The halt control arrives in COP-1. The COP shows; the domains decide.

This package is a sibling of `lc/`. `lc` never imports `cop`, and `cop` never imports `lc` (tested in `tests/test_import_boundaries.py`). `cop` imports only the standard library, `cannae_kernel` and its own web dependencies.

## What the page shows

| # | Panel | Content | Source |
|---|---|---|---|
| 1 | Banner | Product name, overall program state, last refresh times, a staleness warning (any source older than 5 minutes), and a rate-limit warning when no GitHub token is set | Computed |
| 2 | Wave board | Waves 0 to 8: status, work packages, evidence, next action and owner | `cop/program.yaml` |
| 3 | Repositories | aureon, Project-Atreides, cannae-kernel and L.C.: `main` short SHA (Secure Hash Algorithm commit identifier), latest CI (continuous integration) result on `main`, latest tag, open pull requests with checks, "needs update branch" and age | GitHub REST API (representational state transfer application programming interface) |
| 4 | Live Aureon | `deploy_sha`, stack, positions, pending decisions, market open; deploy drift; the AUR-I-17 pending-drop marker | `https://aureon-production.up.railway.app/api/snapshot` |
| 5 | Nightly and scheduled checks | Newest run of each scheduled GitHub Actions workflow, with its date | GitHub REST API |
| 6 | Open decisions | ID, title, owner, age | `cop/program.yaml` |

Each panel also has its own page, `/panel/<name>` (`waves`, `repositories`, `aureon`, `scheduled`, `decisions`), so one panel fits one laptop screen where its content allows.

### Rules the page follows

- **Routes never call GitHub or Aureon.** A background refresher calls every source and swaps a complete in-memory snapshot in under a lock; routes only read that snapshot. The refresher starts on the first request, never at import time.
- **Fail closed.** A source that times out, returns an HTTP (Hypertext Transfer Protocol) error, returns malformed JSON (JavaScript Object Notation), or is rate limited shows `INDETERMINATE` (the kernel `Disposition`) with the error class. Its last good value is shown separately, labelled stale, with the time it was observed. A value older than 5 minutes is treated the same way, even if its source never reported an error. Nothing is green by default: no workflow runs is not a pass, and the first refresh cannot pass the AUR-I-17 check.
- **Provenance on every tile.** Each tile shows its source URL, `observed_at` in UTC (Coordinated Universal Time), and its provenance tag: `FACT_EXTERNAL` for fetched values, `POLICY_RESULT` for computed ones (overall state, drift, pending drop), and `HUMAN_JUDGMENT` for the hand-maintained program file.
- **Plain labels.** Every status colour has a written label beside it.

### Definitions

- **CI on `main`** is the newest GitHub Actions run of each workflow for the `main` head commit, excluding scheduled runs. Any failure → `BLOCK`; any still running → `HOLD`; at least one success and nothing failing → `PASS`; no runs, or only skipped runs → `INDETERMINATE`. External status checks from other services are not read.
- **Deploy drift** compares Aureon's `deploy_sha` with the aureon `main` commit read in the same refresh. Different → `HOLD` (expected for a few minutes after a merge). If either input is unavailable, or Aureon reports `deploy_sha` as `unset`, drift is `INDETERMINATE`.
- **Pending-drop marker (AUR-I-17, pending decisions lost on redeploy)** fires when, between two consecutive good snapshots, `pending` fell from more than 0 to 0 **and** `deploy_sha` changed. Once seen it stays on the page until the Legate process restarts; it is not stored anywhere.
- **Overall program state** is the worst of: CI on each `main`, scheduled runs, the Aureon stack, drift, the AUR-I-17 marker, blocked waves, and every source tile being current. Open pull requests are not included. Order of severity: `BLOCK`, `INDETERMINATE`, `HOLD`, `PASS`.

## `cop/program.yaml`

The repository is public, so this file holds **status only**: identifiers, statuses (`NOT_STARTED`, `IN_PROGRESS`, `BLOCKED`, `DONE`), evidence identifiers, next actions with an owner (`Bill`, `Claude Code`, `Cowork`), and open decisions with one-line titles. No design content. The schema (`cop/program.py`) rejects unknown fields, multi-line or long text, duplicate identifiers, and a wave list that is not exactly 0 to 8.

The file is validated when the app starts and again on every refresh. If it is invalid, panels 2 and 6 show `INDETERMINATE` and the other panels still work. Update `as_of` whenever you change a status.

## Run it locally

Install into a virtual environment outside the repository, with Python 3.11:

```
pip install -e ".[cop,dev]"
```

### With live data

```
LEGATE_OPERATOR_KEY=… LEGATE_SESSION_SECRET=… LEGATE_INSECURE_LOCAL=1 flask --app cop.app run
```

Open http://127.0.0.1:5000 and sign in with the operator key. Add `GITHUB_TOKEN=…` to avoid GitHub's unauthenticated limit (60 requests an hour; one refresh uses about 25 to 40).

**Why `LEGATE_INSECURE_LOCAL=1`:** the session cookie is marked `Secure`, and browsers do not send `Secure` cookies over plain `http://`. Without the flag you can sign in but the next page will send you back to the login form. The flag removes only the `Secure` attribute (the cookie stays `HttpOnly` and `SameSite=Strict`) and drops the `Strict-Transport-Security` header. The app **refuses to serve** if the flag is set while any production marker is present (`PORT`, `RAILWAY_ENVIRONMENT`, `RAILWAY_ENVIRONMENT_NAME` or `RAILWAY_PROJECT_ID`).

### With fake data (demo mode)

```
LEGATE_DEMO=1 LEGATE_INSECURE_LOCAL=1 LEGATE_OPERATOR_KEY=demo LEGATE_SESSION_SECRET=demo flask --app cop.app run
```

Demo mode replaces GitHub and Aureon with invented data (`cop/demo.py`) and shows a "DEMO DATA" warning in the banner. The data includes a failing nightly run, a pull request that needs an update, a timed-out source, deploy drift, and (from the second refresh, after 60 seconds) an AUR-I-17 pending drop. Demo mode makes no network calls. Like the insecure flag, it is refused when a production marker is present.

### Tests and checks

```
ruff check . && ruff format --check .
mypy lc cop tests
COP_EXTRA_REQUIRED=1 pytest -q
```

No test reaches the network: GitHub and Aureon are faked behind `httpx.MockTransport`.

## Deploy to Railway (prepared, not performed)

Legate runs as its **own Railway service**, separate from Aureon's. Deploying or restarting Legate never restarts Aureon.

1. In the Railway project, choose **New** → **GitHub Repo** and select `br-collab/L.C.`. Set the deploy branch to `main`.
2. In the new service's **Settings**:
   - **Build command:** `python -m pip install -r requirements.txt`
   - **Start command:** `python -m gunicorn --workers=1 --threads=4 --bind 0.0.0.0:$PORT cop.app:app`
   - **Healthcheck path:** `/healthz`
   - Service name suggestion: `cannae-cop`.

   Confirm in the first build log that Flask, gunicorn, httpx, PyYAML and cannae-kernel were installed.

   **Do not put the extras marker in the build command.** Railpack strips it: a plan printed as `pip install ".[cop]"` or `python -m pip install .[cop]` executes as `pip install "."`, quoted or not, so only the bare package installs and none of the web dependencies do. Two deploys on 17 September 2026 failed exactly there — `gunicorn: command not found`, then `No module named gunicorn`, with nothing answering `/healthz`. The root `requirements.txt` exists for this reason: pip reads it line by line, so `.[cop]` survives. The `cop` extra in `pyproject.toml` is still the single source of truth for the dependency list.

   **`python -m gunicorn`, not `gunicorn`,** so the start command does not depend on the console script being on `PATH` in the runtime image.
3. In **Variables**, add:
   - `LEGATE_OPERATOR_KEY`: the key you will type at the login form. At least 16 characters; use a password manager to generate it.
   - `LEGATE_SESSION_SECRET`: signs the session cookie. At least 32 random characters, for example the output of `python -c "import secrets; print(secrets.token_urlsafe(48))"`. Changing it signs everyone out.
   - `GITHUB_TOKEN`: the fine-grained read-only token below.
   - Do **not** set `LEGATE_DEMO` or `LEGATE_INSECURE_LOCAL`. If either is set, the service refuses to serve.
4. Under **Networking**, generate a Railway domain. Railway serves it over HTTPS (encrypted HTTP), which the `Secure` session cookie requires.
5. Check: `https://<domain>/healthz` returns `{"status": "ok", ...}`. If it returns `{"status": "misconfigured"}` with HTTP 503, a variable is missing or too short; the deploy logs name which one. Railway's health check will fail in that state, which stops a misconfigured deploy from going live.

**One worker only.** The refresher runs inside the web process and the snapshot lives in that process's memory. With more than one worker, each worker would run its own refresher (multiplying GitHub calls) and could serve a different snapshot on each request, and failed-login counting would be split across workers. `--threads=4` gives concurrency within the single worker. Do not raise `--workers`.

Rotating `LEGATE_OPERATOR_KEY` ends every existing session.

## Create the fine-grained, read-only GitHub token

All four repositories are public, so the token mainly raises the rate limit from 60 to 5,000 requests an hour. It still gets read access only.

1. Sign in to GitHub as `br-collab`. Click your profile picture (top right) → **Settings**.
2. In the left sidebar, click **Developer settings** → **Personal access tokens** → **Fine-grained tokens** → **Generate new token**.
3. **Token name:** `cannae-cop-read-only`. **Expiration:** 90 days (put a reminder in your calendar to rotate it). **Description:** "Legate COP-0: read-only status".
4. **Resource owner:** `br-collab`.
5. **Repository access:** choose **Only select repositories**, then select `br-collab/aureon`, `br-collab/Project-Atreides`, `br-collab/cannae-kernel` and `br-collab/L.C.`.
6. **Permissions** → **Repository permissions**. Set exactly these, and leave everything else at "No access":
   - **Actions:** Read-only (workflow runs: CI on `main`, pull request checks, scheduled runs)
   - **Contents:** Read-only (commits and tags)
   - **Pull requests:** Read-only (open pull requests and their merge state)
   - **Metadata:** Read-only (GitHub selects this automatically and requires it)

   Account permissions: none.
7. Click **Generate token**, copy it once, and paste it into the Railway variable `GITHUB_TOKEN`. Do not store it anywhere else.

Legate sends the token only in the `Authorization` header to `api.github.com`. It never logs it or shows it on the page.

## Limits

- The `/api/snapshot` endpoint gives no time of its own, so `observed_at` for Aureon is the time Legate received the response.
- A scheduled workflow appears only if one of its runs is among the repository's newest 100 scheduled runs.
- More than 99 open pull requests in one repository shows `INDETERMINATE` rather than a partial list.
- Without `GITHUB_TOKEN` the refresh slows to every 10 minutes, so values pass the 5-minute staleness limit between refreshes and show as stale for part of each cycle.
- The AUR-I-17 marker and failed-login counts are held in memory and reset when the process restarts.
