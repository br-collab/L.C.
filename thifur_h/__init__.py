"""Thifur-H: the adaptive intelligence class. Phase C of the agent activation order.

H = Thifur-H. LNN = liquid neural network. C2 = Command and Control.

**H recommends. It never authorizes and never submits, under any condition**
(JUM-D-07, and unchanged by `W3-agent-activation-AMD2.md` § 4). That is enforced
in three places, on purpose: in the contract, which has no field that could
carry an instruction; at the consumer, by each agent's own handoff check; and
here, by this package having no write path and no client of any kind.

What is in this package, and what is deliberately not
-----------------------------------------------------
- :mod:`thifur_h.projection` — the input a condition scores against. A plain
  value type, not an import of Atreides.
- :mod:`thifur_h.baseline` — **condition A**, the deterministic baseline.
- :mod:`thifur_h.evaluation` — the harness that scores **every** condition
  identically, A through D.

Conditions B, C and D are Phase C.2 and are gated on Wave 4 — the synthetic
event stream. Model work cannot be honest without that data: training on
hand-made streams tests a model against its author's assumptions.

Why the baseline comes first
----------------------------
Every later claim in this experiment is *"better than A"*. So A has to exist and
be measured before anything is compared to it, and it has to be scored by the
same harness on the same metrics — otherwise the comparison is between a model
and a memory of how the old thing behaved.

Why this package does not import Atreides
------------------------------------------
`lc` never imports a domain package, and neither does `cop`; this follows the
same rule for the same reason (Research Charter § 17.9). If the thing being
measured could see the domain it is measuring, an experiment could no longer
show that the result was independent of the measurement.

So a condition takes a :class:`~thifur_h.projection.FundingProjection` — a value
handed to it — and returns a
:class:`~cannae_kernel.recommendation.Recommendation`. It reads nothing, fetches
nothing and writes nothing. That also makes every score replayable, which is the
property the experiment actually needs.
"""
