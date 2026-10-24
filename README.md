# Legiones Cannenses (L.C.)

> **Claim label: research.**
> This repository is research code. It is not audited, not production-qualified, and
> has never been used to move real money. Every surface that could reach a payment
> rail refuses to by construction. The four labels this programme uses are *research*,
> *experimental*, *validated* and *production-qualified*; all five repositories are at
> the first, and this label changes only when evidence changes it.

**Last updated:** 242250Z OCT 26. Reflects `main` at `df32507`, package version 0.1.0 (no release tag). Latest merged change: #120, dated SIM-1 simulation links in the Common Operating Picture.


The synthetic middle layer of Project Cannae Legion — a deliberately bounded OMS/EMS (Order Management System / Execution Management System) and clearing layer that turns Aureon's approved intent into executions, allocations and settlement obligations that Atreides can accept.

```
Aureon (pre-trade, approved intent)  →  L.C. (orders, fills, allocations, clearing, netting)  →  Atreides (settlement, finality, reconciliation)
```

## Maturity and criticality

The programme uses two separate descriptions. **Automation level** describes what
the software does: 1 is Observe, 2 is Compare, 3 is Recommend, 4 is Execute with
control, 5 is Exception led, and 6 is Straight through. **Criticality tier**
describes the consequence-bearing function a component would touch: Tier 0 covers
money, positions, regulatory calculations and clearing submission, Tier 1 covers
production workflow or regulated records, Tier 2 covers shared decision support,
and Tier 3 covers analysis and operator productivity. A tier is not an assurance
rating.

| Component | Automation level | Criticality tier |
|---|---:|---:|
| L.C. lifecycle and emulators | 2, Compare | 2 |
| Common Operating Picture | 1, Observe | 2 |

Both components are synthetic and advisory. Neither meets the assurance,
operational control or authorization that its tier would require in production,
and the table makes no compliance claim.

## Status

`lc` is version 0.1.0. `import lc` exposes that version and no aggregate facade. The middle layer is the modules below. Each module's public names are its `__all__`. Inputs are synthetic or caller-supplied. Nothing here files with a regulator or connects to a clearing agency.

- Accept an approved intent, run the order lifecycle, and replay it (`lc/lifecycle.py`).
- Apply a separate firm market-access gate before every child-order release
  (`lc/market_access.py`). It verifies attested Atreides account-capacity and locate bytes,
  records every pass or refusal and its limits version, and refuses missing, stale or tampered
  evidence. Sell orders require a long, short or short-exempt mark; short sales consume a
  quantity-limited synthetic locate. The gate is advisory infrastructure and has no venue
  submission capability. Annual review, chief executive certification, Rule 201, bona fide
  market making, and threshold-security Rule 203(b)(3) close-out are declared, not built.
- Capture a trade, allocate it, record a match, and affirm it (`lc/trade.py`).
- Clear a gross bilateral trade and check that quantity and cash are conserved (`lc/clearing.py`). Consume Atreides' attested FICC GSD bytes without translation, then record the distinct `FICC_GSD_NOVATED_NETTED` result (`lc/ficc.py`, `lc/clearing.py`). The result is explicitly observed from the FICC member seat and carries conserved net par and signed settlement money with currency. Direct, sponsored and agent access are built through that FICC path; CME Securities Clearing is declared in Atreides but its rules are not built. FICC fails, close out, buy in and intraday funding are not built.
- Form a settlement obligation and record whether Atreides accepted those bytes (`lc/obligation.py`). The `lc` package does not submit that obligation to a rail.
- Listed options. Series and position accounts (`lc/options.py`). Admit a novation only when the bytes match the attested digest (`lc/option_clearing.py`). Exercise, assign, and expire (`lc/option_exercise.py`). Form premium and exercise obligations (`lc/option_obligation.py`). Ingest an Options Clearing Corporation (OCC) margin or clearing-fund report, and compare it with an estimate labelled an approximation (`lc/occ_margin.py`). Check OCC Rule 301(b)(1) on figures the caller supplies (`lc/occ_membership.py`). Ingest a contract adjustment by reference identifier (`lc/option_adjustment.py`).
- A synthetic OCC (`emulators/occ.py`) novates two matching trade reports. It does not import `lc`.
- The synthetic entitled member (`emulators/settlement.py`) is the component that submits, and it submits only to the synthetic rail.
- The `lifecycle-document` publication branch exposes `ficc-netted.json`, whose five independent
  facts, `FICC_GSD_NOVATED_NETTED` method, `FICC_MEMBER` seat and both net settlement legs come
  from exact attested Atreides bytes. The Common Operating Picture reads that same document and
  labels the seat, observed member, net par and signed settlement money.
  The same branch exposes `ficc-repo-dvp.json`, `ficc-repo-gcf.json`, and
  `ficc-cash-repo-netted.json`. Repo views show the member seat, both dated
  securities/money legs, rate, day-count term, access, and the distinct DVP
  netting or GCF tri-party method. Haircut is an input and member margin is a
  labelled synthetic approximation; fails, closeout, real FICC VaR,
  cross-margining, and participant submission are not built.
  EQ-1 adds `equities-books.json`, `equities-margin.json`,
  `equities-market-access.json`, `equities-protection-from-books.json`,
  `equities-account-capacity.json`, and `equities-locates.json`; all are synthetic and replayable.

The read-only Common Operating Picture (COP), the Command and Control (C2) harness, and the Thifur-H experiment are separate surfaces. The joint upgrade map exists, and the first cross-domain contracts are frozen in `cannae-kernel`.

`lc/clearing.py` records both bilateral gross clearing and verified FICC GSD novated/netted
results. Equities continuous net settlement remains a separate model in Atreides and is never
used under a Treasury label.

## Public API

The repository does not have one aggregate Python facade. Its supported
surfaces are explicit and separate:

| Surface | Supported entrypoint | Contract |
|---|---|---|
| L.C. middle layer | The `lc` modules named in Status. `import lc` exposes `__version__` only | Order lifecycle, trade capture, allocation, match, affirmation, bilateral gross clearing, attested FICC GSD novated/netted result consumption, obligation formation, and the listed-options modules. No single facade. |
| Common Operating Picture (COP) | `cop.app:create_app` for application construction; `cop.app:app` as the Web Server Gateway Interface (WSGI) deployment target | Read-only HTTP surface documented in [`cop/README.md`](cop/README.md). Other `cop.*` modules are implementation details unless they declare `__all__`. |
| Command and Control (C2) harness | `harness_c2.lineage`, `harness_c2.handoff`, `harness_c2.escalation` | Only names declared in each module's `__all__` are public. The package root is not a facade. |
| Thifur-H experiment | `thifur_h.projection`, `thifur_h.baseline`, `thifur_h.evaluation` | Only names declared in each module's `__all__` are public. It recommends and scores; it never authorizes or submits. |

Consumers must not import underscored names, tests, fixtures, or internal COP
readers and view models. This table records the current surface before the later
work to consolidate each repository behind one public API; it does not pretend that
consolidation has already happened.

## Scope rules

- L.C. is a research instrument, not a commercial OMS/EMS. It builds only what a registered experiment needs.
- L.C. forms the obligation; Atreides accepts or rejects it and never rewrites the economics.
- L.C. never copies a fill from approved intent. Execution events come from an independent (synthetic) venue adapter and are labelled synthetic.
- Functions that Aureon or Atreides already perform correctly stay there and are consumed through contracts.
- First vertical slice (Charter §19.10): bilateral U.S. Treasury DvP (delivery versus payment).

## Layout

- `lc/` — Python package (import name `lc`). Version marker plus the middle-layer modules named in Status.
- `emulators/` — synthetic venue, matching, settlement member, and OCC. Independent of `lc`.
- `cop/` — Legate, the COP-0 (Common Operating Picture) program picture: a private, read-only status page. Separate from `lc`, with its own optional dependencies. See `cop/README.md`.
- `harness_c2/` — deterministic lineage and handoff harness.
- `thifur_h/` — condition-A recommendation and evaluation. It recommends and scores. It does not authorize or submit.
- `tests/` — tests
- `docs/` — specifications and phase records that belong with the code

The research record (charter, inventories, strategy, evidence) lives outside this repository in the Project Cannae Legion › Research-record folder.

## License

MIT — see `LICENSE`.
