"""Panel 12 — what this picture cannot see. **Mandatory, never empty** (COP-1 WP-2).

COP = Common Operating Picture. C2 = Command and Control.

Why a panel for the gaps
------------------------
Every other panel answers *what is happening*. This one answers *what would not
show up here if it were happening*, and it is the only panel on the page whose
absence would make the others misleading.

An operating picture with four green panels and no statement of its own limits
invites exactly one reading: **that the four panels are the whole picture.** They
are not, and at this stage they are not close — one of three layers is unbuilt,
most sources are unconnected, and several facts the operator needs are not
expressible in the frozen contracts at all.

It is computed, not written down
--------------------------------
The list comes from the snapshot: which sources answered, which did not, which
were never configured, which layers exist. A hand-maintained list is a list that
is accurate on the day somebody writes it and wrong by the time it matters, and
the failure is silent because nobody re-reads a paragraph that has not changed.

The one hand-maintained part is :data:`CONTRACT_BLIND_SPOTS` — facts the five
frozen contracts cannot express. Those cannot be computed, because the absence
of a field is not something the code can notice. They are listed with their
reasons, and each names what would have to change to close it.

Never empty, and the test says so
---------------------------------
:func:`blind_spots` cannot return an empty tuple: the contract limitations alone
guarantee entries, and if every source were connected and every layer built, the
panel would still carry them. **A panel that could render empty would eventually
render empty on a day when it should not have**, and nobody would notice the
difference between "nothing is hidden" and "the check stopped running".
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "CONTRACT_BLIND_SPOTS",
    "BlindSpot",
    "BlindSpotKind",
    "LayerStatus",
    "SourceStatus",
    "blind_spots",
]


class BlindSpotKind(StrEnum):
    """Why the picture cannot see a thing. Three kinds, three responses."""

    SOURCE_NOT_CONNECTED = "SOURCE_NOT_CONNECTED"
    """A source exists and nothing is pointed at it. Somebody configures it."""

    SOURCE_NOT_ANSWERING = "SOURCE_NOT_ANSWERING"
    """A source is configured and is not answering now. Somebody looks at it."""

    LAYER_NOT_BUILT = "LAYER_NOT_BUILT"
    """The thing that would report does not exist yet. Wave 4, or later."""

    CONTRACT_CANNOT_EXPRESS = "CONTRACT_CANNOT_EXPRESS"
    """The frozen contracts carry no field for it. A contract change, deliberately."""


@dataclass(frozen=True)
class BlindSpot:
    """One thing the picture cannot see, and what would change that."""

    kind: BlindSpotKind
    name: str
    detail: str
    remedy: str
    """What would have to happen. Never "investigate" — a remedy a reader cannot
    act on is a sentence that makes the list longer and the page no better."""


#: Facts the operator needs that the five frozen contracts cannot carry.
#:
#: Hand-maintained on purpose, because the absence of a field is not something
#: the code can notice. Each names what would have to change, so that the list is
#: a set of decisions nobody has taken rather than a list of complaints.
CONTRACT_BLIND_SPOTS: tuple[BlindSpot, ...] = (
    BlindSpot(
        kind=BlindSpotKind.CONTRACT_CANNOT_EXPRESS,
        name="Break records across layers",
        detail=(
            "No domain publishes break records yet. Panel 10 is a labelled synthetic "
            "demonstration, not evidence that a live cross-layer break was observed."
        ),
        remedy="change a contract / add a producer",
    ),
    BlindSpot(
        kind=BlindSpotKind.CONTRACT_CANNOT_EXPRESS,
        name="Partial fills against one approved intent",
        detail=(
            "ExecutionEvent references one intent and one revision. A sequence of fills "
            "against a single intent is expressible, but the contract carries no notion of "
            "how much of the intent remains — so this page cannot show a half-filled order "
            "as half-filled."
        ),
        remedy="a field on ExecutionEvent for the remaining quantity, which is a contract change",
    ),
    BlindSpot(
        kind=BlindSpotKind.CONTRACT_CANNOT_EXPRESS,
        name="Why an obligation was held, beyond the DSOR record's own reason",
        detail=(
            "ObligationAcceptanceRecord carries a disposition and either a Decision System "
            "of Record entry or the reason there is none. It does not carry which gate held "
            "it, so a held row shows that it is held and not which rule did it."
        ),
        remedy="a gate reference on the acceptance record, or a separate escalation for each hold",
    ),
    BlindSpot(
        kind=BlindSpotKind.CONTRACT_CANNOT_EXPRESS,
        name="The cost of a break, in money or in time",
        detail=(
            "Nothing in the five contracts carries an impact. An operator reading this page "
            "can see that two layers disagree and cannot see whether it matters, so every "
            "break reads as equally urgent — which is the same as none of them reading as "
            "urgent."
        ),
        remedy="an impact estimate, which is domain work and not a contract field alone",
    ),
    BlindSpot(
        kind=BlindSpotKind.CONTRACT_CANNOT_EXPRESS,
        name="Anything a human did outside this system",
        detail=(
            "A call to a counterparty, an instruction given by telephone, a decision taken "
            "in a meeting. None of it is in any contract, and none of it appears here. "
            "**This is the largest blind spot on the page and it cannot be closed by code.**"
        ),
        remedy="an operator log, which is a product decision rather than a contract one",
    ),
)


@dataclass(frozen=True)
class SourceStatus:
    """One source, as the snapshot found it. Supplied by the caller.

    This module takes a description rather than the snapshot itself, so that the
    rules below can be tested without building a whole page — and so that adding
    a panel does not mean editing this file.
    """

    name: str
    configured: bool
    current: bool
    error: str | None = None


#: The layers, and whether the thing that would report from them exists.
#: ``LC`` is Wave 4; the other two are built.
@dataclass(frozen=True)
class LayerStatus:
    name: str
    built: bool
    covers: str
    """What this page cannot show while the layer is missing."""


def blind_spots(
    sources: tuple[SourceStatus, ...], layers: tuple[LayerStatus, ...]
) -> tuple[BlindSpot, ...]:
    """Everything this picture cannot see, computed from the snapshot.

    **Never empty**: the contract limitations alone guarantee entries, so a panel
    built from this cannot render blank even with every source connected and
    every layer built. That matters more than it looks — a panel that *could*
    render empty would one day render empty when it should not, and nobody would
    tell the difference between "nothing is hidden" and "the check stopped
    running".

    Ordered by what a reader can act on: unconnected sources first (somebody
    configures one this afternoon), then failing sources, then unbuilt layers,
    then the contract limits, which are the slowest to change.
    """
    found: list[BlindSpot] = []

    for source in sources:
        if not source.configured:
            found.append(
                BlindSpot(
                    kind=BlindSpotKind.SOURCE_NOT_CONNECTED,
                    name=source.name,
                    detail=(
                        f"{source.name} is not connected, so nothing it would report "
                        f"appears anywhere on this page."
                    ),
                    remedy=source.error or "point the configuration at it",
                )
            )

    for source in sources:
        if source.configured and not source.current:
            found.append(
                BlindSpot(
                    kind=BlindSpotKind.SOURCE_NOT_ANSWERING,
                    name=source.name,
                    detail=(
                        f"{source.name} is configured and did not answer in this refresh "
                        f"({source.error or 'no reason recorded'}). Panels that read it show "
                        f"INDETERMINATE and are not carrying a previous value forward."
                    ),
                    remedy="look at the source; the panels will recover on their own",
                )
            )

    for layer in layers:
        if not layer.built:
            found.append(
                BlindSpot(
                    kind=BlindSpotKind.LAYER_NOT_BUILT,
                    name=f"{layer.name} layer",
                    detail=(
                        f"The {layer.name} layer does not exist yet, so {layer.covers} "
                        f"cannot be shown by any panel here."
                    ),
                    remedy="Wave 4 builds it; nothing before then will fill these columns",
                )
            )

    found.extend(CONTRACT_BLIND_SPOTS)
    return tuple(found)
