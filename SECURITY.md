# Security policy

## Reporting a vulnerability

Please report security problems **privately**. Do not open a public issue, pull request or discussion for a suspected vulnerability — a public report discloses the problem before it can be fixed.

To report privately:

1. Go to this repository's **Security** tab on GitHub.
2. Choose **Report a vulnerability**. This opens a private advisory that only you and the maintainer, Bill Ravelo, can see.

Include what you found, how to reproduce it, and what you believe the impact is. You will get an acknowledgement, and the fix and any disclosure will be coordinated with you in that private advisory.

## Scope

L.C. (Legiones Cannenses) is a research instrument. It uses synthetic data only, holds no credentials and never submits to a settlement rail. Reports are still welcome — in particular anything that would let the package import code it must not import, or let synthetic data be presented as real.

The `cop/` package (Legate, the COP-0 program picture) is a web application. It reads its operator key, session secret and a read-only GitHub token from environment variables at deploy time; none is stored in this repository. For it, reports of authentication bypass, session forgery, token exposure, or a failed or stale source being shown as current are especially welcome.

## Supported versions

Only the latest commit on `main` is supported. There are no releases yet.
