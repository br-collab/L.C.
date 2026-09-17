# Legiones Cannenses (L.C.)

The synthetic middle layer of Project Cannae Legion — a deliberately bounded OMS/EMS (Order Management System / Execution Management System) and clearing layer that turns Aureon's approved intent into executions, allocations and settlement obligations that Atreides can accept.

```
Aureon (pre-trade, approved intent)  →  L.C. (orders, fills, allocations, clearing, netting)  →  Atreides (settlement, finality, reconciliation)
```

## Status: pre-implementation (gated)

No middle-layer code is written until Research Charter §18.7 steps 1–4 are complete:

1. Aureon inventory — done (15 Sep 2026)
2. Atreides inventory — done (15 Sep 2026)
3. Joint upgrade map — pending
4. First cross-domain contracts frozen — pending

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
