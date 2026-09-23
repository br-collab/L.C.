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

This research repository has **353 passing tests** and four defined public surfaces: the `lc`
package version marker, the deployed read-only Common Operating Picture (COP), the deterministic
Command and Control (C2) lineage/handoff harness, and the Thifur-H condition-A recommendation and
evaluation harness. The joint upgrade map exists and all five first cross-domain contracts are
frozen in `cannae-kernel`; they are not pending. The order, fill, allocation, clearing, and netting
middle layer itself is still not built, `lc` still exposes only `__version__ = "0.0.0"`, and no
surface submits an order or settlement instruction. Whole-repository line coverage measures
**98%** across `lc`, `cop`, `harness_c2`, and `thifur_h`.

## Public API

The repository does not yet have one aggregate Python facade. Its supported
surfaces are explicit and separate today:

| Surface | Supported entrypoint | Contract |
|---|---|---|
| L.C. middle layer | `import lc` | Version marker only. No order, fill, allocation, clearing or netting API exists yet. |
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

- `lc/` — Python package (import name `lc`)
- `cop/` — Legate, the COP-0 (Common Operating Picture) program picture: a private, read-only status page. Separate from `lc`, with its own optional dependencies. See `cop/README.md`.
- `tests/` — tests
- `docs/` — specifications and phase records that belong with the code

The research record (charter, inventories, strategy, evidence) lives outside this repository in the Project Cannae Legion › Research-record folder.

## License

MIT — see `LICENSE`.
