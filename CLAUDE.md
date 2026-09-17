# CLAUDE.md — Legiones Cannenses (L.C.)

Guidance for Claude Code in this repository.

## Hard gate
Do not implement middle-layer behaviour until the joint Aureon/Atreides upgrade map exists and the first cross-domain contracts (ApprovedIntentEnvelope, AcceptedSettlementObligationEnvelope) are frozen. Research Charter §18.7. Scaffolding, tests of frozen contracts, and docs are allowed.

## Sibling repositories (same parent folder, ~/Code/cannae)
- `aureon/` — pre-trade, governed intent. Source of ApprovedIntentEnvelope.
- `Project-Atreides/` — post-trade settlement. Consumer of AcceptedSettlementObligationEnvelope. Python 3.11+.
Consume their contracts; never vendor their code into this repository.

## Invariants
- Approval is not execution; an order is not a fill; a fill is not a settlement fact.
- Fills come only from an independent venue adapter and carry provenance (SYNTHETIC or EXTERNAL_VERIFIED).
- Quantity and consideration are conserved across parent/child orders, fills and allocations.
- Duplicate messages never create duplicate orders, fills, allocations or obligations.
- Missing required evidence yields HOLD or INDETERMINATE, never PASS.
- L.C. never submits to a settlement rail.

## Working rules
- One work package per commit, with its acceptance tests.
- Journal and record timestamps use DTG format (YYYYMMDDHHMM).
- Spell out acronyms on first use in docs.
