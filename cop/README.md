# Legate: COP-0 program picture

**Live deployment:** [lc-production-373f.up.railway.app](https://lc-production-373f.up.railway.app) · health: [`/healthz`](https://lc-production-373f.up.railway.app/healthz)

Legate is a private, read-only web page: a COP (Common Operating Picture) for Project Cannae Legion. It shows the state of the build program, of the live Aureon service and of the Atreides agent family on one URL. "Legate" is a working name (decision JUM-D-27); in code it lives only in `cop/settings.py` as `PRODUCT_NAME`.

It has no write routes and no controls. The halt control arrives in COP-1. The COP shows; the domains decide.

This package is a sibling of `lc/`. `lc` never imports `cop`, and `cop` never imports `lc` (tested in `tests/test_import_boundaries.py`). `cop` imports only the standard library, `cannae_kernel` and its own web dependencies. That includes the Agents panel: `cop` never imports `atreides`, so the shapes in `cop/agents.py` are this repository's own reading of the published document, exactly as `cop/aureon.py` is its own reading of Aureon's snapshot.

## What the page shows

| # | Panel | Content | Source |
|---|---|---|---|
| 1 | Banner | Product name, overall program state, last refresh times, a staleness warning (any source older than 5 minutes), and a rate-limit warning when no GitHub token is set | Computed |
| 2 | Wave board | Waves 0 to 8: status, work packages, evidence, next action and owner | `cop/program.yaml` |
| 3 | Repositories | aureon, Project-Atreides, cannae-kernel and L.C.: `main` short SHA (Secure Hash Algorithm commit identifier), latest CI (continuous integration) result on `main`, latest tag, open pull requests with checks, "needs update branch" and age | GitHub REST API (representational state transfer application programming interface) |
| 4 | Live Aureon | `deploy_sha`, stack, positions, pending decisions, market open; deploy drift; the AUR-I-17 pending-drop marker | `https://aureon-production.up.railway.app/api/snapshot` |
| 5 | Atreides agents | Each activated agent: whether it is up, what it last recommended, what it refused, the C2 (Command and Control) handoff its work arrived under, and the server-side halt state | Atreides activation snapshot (`ATREIDES_AGENTS_URL`) |
| 6 | Nightly and scheduled checks | Newest run of each scheduled GitHub Actions workflow, with its date | GitHub REST API |
| 7 | Open decisions | ID, title, owner, age | `cop/program.yaml` |

Each panel also has its own page, `/panel/<name>` (`waves`, `repositories`, `aureon`, `agents`, `scheduled`, `decisions`), so one panel fits one laptop screen where its content allows.

### Rules the page follows

- **Routes never call GitHub or Aureon.** A background refresher calls every source and swaps a complete in-memory snapshot in under a lock; routes only read that snapshot. The refresher starts on the first request, never at import time.
- **Fail closed.** A source that times out, returns an HTTP (Hypertext Transfer Protocol) error, returns malformed JSON (JavaScript Object Notation), or is rate limited shows `INDETERMINATE` (the kernel `Disposition`) with the error class. Its last good value is shown separately, labelled stale, with the time it was observed. A value older than 5 minutes is treated the same way, even if its source never reported an error. Nothing is green by default: no workflow runs is not a pass, and the first refresh cannot pass the AUR-I-17 check.
- **Provenance on every tile.** Each tile shows its source URL, `observed_at` in UTC (Coordinated Universal Time), and its provenance tag: `FACT_EXTERNAL` for fetched values, `POLICY_RESULT` for computed ones (overall state, drift, pending drop), and `HUMAN_JUDGMENT` for the hand-maintained program file.
- **Plain labels.** Every status colour has a written label beside it.

### Definitions

- **CI on `main`** is the newest GitHub Actions run of each workflow for the `main` head commit, excluding scheduled runs. Any failure → `BLOCK`; any still running → `HOLD`; at least one success and nothing failing → `PASS`; no runs, or only skipped runs → `INDETERMINATE`. External status checks from other services are not read.
- **Deploy drift** compares Aureon's `deploy_sha` with the aureon `main` commit read in the same refresh. Different → `HOLD` (expected for a few minutes after a merge). If either input is unavailable, or Aureon reports `deploy_sha` as `unset`, drift is `INDETERMINATE`.
- **Pending-drop marker (AUR-I-17, pending decisions lost on redeploy)** fires when, between two consecutive good snapshots, `pending` fell from more than 0 to 0 **and** `deploy_sha` changed. Once seen it stays on the page until the Legate process restarts; it is not stored anywhere.
- **Atreides agents** is Phase A of agent activation (tasking order `W3-agent-activation.md` and its amendment `AMD1`). The agents run continuously against a synthetic flow and **recommend only**: no agent makes an approval decision, and every approval gate still requires explicit operator action (CAOM-001, Consolidated Authority Operating Mode). The panel shows one card per agent. Three things on it are worth reading closely:
  - **An agent that produced nothing is absent with a reason**, never blank and never a pass. A *stopped* agent reads "nothing recorded · agent stopped: …" and its last recommendation is **not** carried forward — a recommendation from before it stopped is not its current state.
  - **The C2 handoff basis** is what the agent's work arrived under. While no C2 runtime exists this is the recorded absence "operator-direct under CAOM-001". It is a value in the record, not an empty field.
  - **The lateral-handoff probe reads inverted.** It offers an agent-to-agent input on every tick with no recorded handoff, and it is healthy when that input is **refused** — "Refusal held". If it ever shows "REFUSAL BROKEN", an inadmissible input was admitted and the whole picture goes `BLOCK`.
- **Overall program state** is the worst of: CI on each `main`, scheduled runs, the Aureon stack, drift, the AUR-I-17 marker, blocked waves, Atreides agent activation, and every source tile being current. Open pull requests are not included. Order of severity: `BLOCK`, `INDETERMINATE`, `HOLD`, `PASS`.

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

Add `ATREIDES_AGENTS_URL=…` to point the Agents panel at a published activation snapshot. **There is deliberately no default.** Unset, the panel reports `NotConfigured` and names the variable, which is different from a source that failed: it does not spoil the "last refresh with every source answering" time and it is not listed in the staleness warning. It does still hold the overall state at `INDETERMINATE`, because a picture that cannot see the agents may not report that they are fine.

**Why `LEGATE_INSECURE_LOCAL=1`:** the session cookie is marked `Secure`, and browsers do not send `Secure` cookies over plain `http://`. Without the flag you can sign in but the next page will send you back to the login form. The flag removes only the `Secure` attribute (the cookie stays `HttpOnly` and `SameSite=Strict`) and drops the `Strict-Transport-Security` header. The app **refuses to serve** if the flag is set while any production marker is present (`PORT`, `RAILWAY_ENVIRONMENT`, `RAILWAY_ENVIRONMENT_NAME` or `RAILWAY_PROJECT_ID`).

### With fake data (demo mode)

```
LEGATE_DEMO=1 LEGATE_INSECURE_LOCAL=1 LEGATE_OPERATOR_KEY=demo LEGATE_SESSION_SECRET=demo flask --app cop.app run
```

Demo mode replaces GitHub, Aureon and Atreides with invented data (`cop/demo.py`) and shows a "DEMO DATA" warning in the banner. The data includes a failing nightly run, a pull request that needs an update, a timed-out source, deploy drift, and (from the second refresh, after 60 seconds) an AUR-I-17 pending drop. For the Agents panel it includes one agent reporting cleanly, one holding work for the operator, one stopped and therefore absent-with-reason, and the lateral-handoff probe being refused — every state the panel can show, because a demonstration that only showed the happy row would not tell anyone whether the unhappy ones render at all. Demo mode makes no network calls. Like the insecure flag, it is refused when a production marker is present.

### Tests and checks

```
ruff check . && ruff format --check .
mypy lc cop tests
COP_EXTRA_REQUIRED=1 pytest -q
```

No test reaches the network: GitHub, Aureon and the Atreides activation snapshot are faked behind `httpx.MockTransport`.

## Deploy to Railway

**Deployed**, at the address above. The steps below are what was done and what to repeat if the service is ever rebuilt.

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
   - `ATREIDES_AGENTS_URL` (optional): where the Atreides Phase A activation snapshot is published. Leave it unset until that document is actually served; the Agents panel then reports "not configured" rather than failing against a guessed address.
   - Do **not** set `LEGATE_DEMO` or `LEGATE_INSECURE_LOCAL`. If either is set, the service refuses to serve.
4. Under **Networking**, generate a Railway domain. Railway serves it over HTTPS (encrypted HTTP), which the `Secure` session cookie requires. **The generated domain is `lc-production-373f.up.railway.app`** — recorded here because a service whose address lives only in somebody's memory cannot be checked by anybody else.
5. Check `https://<domain>/healthz`, and **check what it returns rather than that it answers.** A healthy Legate replies `200` with `Content-Type: application/json` and a body of `{"refresher": "running", "status": "ok"}`. `{"status": "misconfigured"}` with HTTP 503 means a variable is missing or too short; the deploy logs name which one, and Railway's health check fails in that state, which stops a misconfigured deploy from going live.

   **A 200 on its own proves nothing** (workspace working rule 13). The sibling service is the illustration: `aureon-production.up.railway.app/healthz` also returns 200, because aureon serves its dashboard as a catch-all for any unmatched path — `/healthz`, `/nonsense` and `/` all return the same 287,180 bytes. A health check pointed there would report healthy for ever. Legate does not behave that way: `/healthz` is JSON and an unknown path redirects, so assert on the **content type and the body**, not the status code.

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
- **The Atreides activation snapshot is not published anywhere yet.** The reader, the panel and their tests are complete and exercised against a fake server, but until something serves that document at `ATREIDES_AGENTS_URL` the panel shows "not configured" in any real deployment. Publishing it is a deployment step, not a code change.
- The reader accepts schema version 1 only. A document declaring any other version is refused outright rather than read field by field, because a renamed field read as absent would look like a fact about the agents instead of a fact about the reader.
