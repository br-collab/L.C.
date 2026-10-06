# Legiones Cannenses (L.C.)

> **Claim label: research.**
> This repository is research code. It is not audited, not production-qualified, and
> has never been used to move real money. Every surface that could reach a payment
> rail refuses to by construction. The four labels this programme uses are *research*,
> *experimental*, *validated* and *production-qualified*; all five repositories are at
> the first, and this label changes only when evidence changes it.


The synthetic middle layer of Project Cannae Legion — a deliberately bounded OMS/EMS (Order Management System / Execution Management System) and clearing layer that turns Aureon's approved intent into executions, allocations and settlement obligations that Atreides can accept.

```
Aureon (pre-trade, approved intent)  →  L.C. (orders, fills, allocations, clearing, netting)  →  Atreides (settlement, finality, reconciliation)
```

## Status

`lc` is version 0.1.0. `import lc` exposes that version and no aggregate facade. The middle layer is the modules below. Each module's public names are its `__all__`. Inputs are synthetic or caller-supplied. Nothing here files with a regulator or connects to a clearing agency.

- Accept an approved intent, run the order lifecycle, and replay it (`lc/lifecycle.py`).
- Capture a trade, allocate it, record a match, and affirm it (`lc/trade.py`).
- Clear a gross bilateral trade and check that quantity and cash are conserved (`lc/clearing.py`). A clearing path this programme has not built raises `ClearingPathNotBuiltError`.
- Form a settlement obligation and record whether Atreides accepted those bytes (`lc/obligation.py`). The `lc` package does not submit that obligation to a rail.
- Listed options. Series and position accounts (`lc/options.py`). Admit a novation only when the bytes match the attested digest (`lc/option_clearing.py`). Exercise, assign, and expire (`lc/option_exercise.py`). Form premium and exercise obligations (`lc/option_obligation.py`). Ingest an Options Clearing Corporation (OCC) margin or clearing-fund report, and compare it with an estimate labelled an approximation (`lc/occ_margin.py`). Check OCC Rule 301(b)(1) on figures the caller supplies (`lc/occ_membership.py`). Ingest a contract adjustment by reference identifier (`lc/option_adjustment.py`).
- A synthetic OCC (`emulators/occ.py`) novates two matching trade reports. It does not import `lc`.
- The synthetic entitled member (`emulators/settlement.py`) is the component that submits, and it submits only to the synthetic rail.

The read-only Common Operating Picture (COP), the Command and Control (C2) harness, and the Thifur-H experiment are separate surfaces. The joint upgrade map exists, and the first cross-domain contracts are frozen in `cannae-kernel`.

`lc/clearing.py` clears one gross bilateral trade. Equities continuous net settlement is in Atreides.

## Public API

The repository does not have one aggregate Python facade. Its supported
surfaces are explicit and separate:

| Surface | Supported entrypoint | Contract |
|---|---|---|
| L.C. middle layer | The `lc` modules named in Status. `import lc` exposes `__version__` only | Order lifecycle, trade capture, allocation, match, affirmation, gross clearing, obligation formation, and the listed-options modules. No single facade. |
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
