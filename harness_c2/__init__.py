"""Thifur-C2: the Cannae Legion top-level command and control harness.

Phase B of ``_tasking/W3-agent-activation.md``. C2 = Command and Control.
CAOM = Consolidated Authority Operating Mode. COP = Common Operating Picture.

**Held, and deliberately unpushed.** ``W3-agent-activation-AMD1.md`` § 4 holds
Phase B on Bill's Research Charter § 18.5 intellectual-property decision, and no
C2 orchestration design reaches any public repository under ``br-collab`` until
that decision is recorded in the research record. This package exists on a local
branch only. Do not push it until § 18.5 is recorded.

The five immutable stops
------------------------
1. **No self-execution.** C2 never takes a market action, generates an order,
   modifies a position, or issues a settlement instruction under any condition.
2. **No doctrine interpretation.** Doctrine ambiguity escalates to the human
   authority surface.
3. **Handoff before action.** No agent acts on a lifecycle object without a
   recorded C2 handoff authorization.
4. **One lineage record.** Gaps are flagged explicitly, never silently filled.
5. **Escalation completeness.** C2 never escalates a partial picture.

Stop 3 is **already satisfied at the consumer**, by WP-A2 in
``atreides.activation.handoff``: each agent's own type check refuses input that
did not arrive with a recorded handoff. C2 adds the ability to *grant* a
handoff; it does not become the thing that prevents one. That asymmetry is why
adding C2 is safe rather than load-bearing, and nothing in this package may
weaken it.

Build order
-----------
The lineage assembler first, then handoff issuance, then escalation packaging.
The assembler is the only part testable against the frozen contracts with no
authority existing at all, so it goes first — and this package currently
contains only the assembler.

Boundaries
----------
``lc`` never imports ``harness_c2`` and neither does ``cop``; both are enforced
by ``tests/test_import_boundaries.py``. If the harness could be seen by the
middle layer under test, an experiment could no longer show that L.C. behaved
independently of the thing measuring it (Research Charter § 17.9).
"""
