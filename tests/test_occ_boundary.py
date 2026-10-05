"""SC-3 WP-2 acceptance: the boundary between the OCC emulator and L.C.

The order: "The boundary test constructs the real kernel type and proves L.C. consumes the
exact attested payload without translation." Workspace rule 20: a cross-domain claim is
proven by the producer's real emitter and the consumer's real parser, in one execution.

This test is the independent caller. It imports both packages; neither imports the other
(checked by ``tests/test_import_boundaries.py``, and again below). The emulator's
``ExecutionEvent`` is the real kernel type, and the bytes it attests reach
``lc.option_clearing.admit_novation`` exactly as the emulator produced them.

Everything here is SYNTHETIC.
"""

from __future__ import annotations

import ast
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from cannae_kernel.canonical import digest_bytes
from cannae_kernel.envelopes import ExecutionEvent
from test_occ_emulator import trade

import emulators.occ as occ_module
import lc.option_clearing as clearing_module
from emulators.occ import OccEmulator, OccOutcome, OpenClose
from lc.option_clearing import (
    AdmittedNovation,
    NovationNotAdmittedError,
    NovationRefusal,
    admit_novation,
)
from lc.options import PositionAccountType


def novated() -> tuple[ExecutionEvent, ExecutionEvent, bytes]:
    ccp = OccEmulator()
    buy_report, sell_report = trade(1, "CM-A", "CM-B", sell=OpenClose.OPEN)
    ccp.submit(buy_report)
    result = ccp.submit(sell_report)
    assert result.outcome is OccOutcome.NOVATED and result.payload is not None
    buyer_event, seller_event = result.events
    return buyer_event, seller_event, result.payload


def test_lc_admits_the_exact_bytes_the_emulator_attests() -> None:
    buyer_event, seller_event, payload = novated()
    assert type(buyer_event) is ExecutionEvent  # the real kernel type, not a look-alike
    for event in (buyer_event, seller_event):
        admitted = admit_novation(event, payload)
        assert isinstance(admitted, AdmittedNovation)
        assert admitted.event == event
        trade_fact = admitted.trade
        assert trade_fact.series_osi == "SYN   261218C00150500"
        assert trade_fact.contracts == 10
        assert trade_fact.buyer.clearing_member_id == "CM-A"
        assert trade_fact.seller.clearing_member_id == "CM-B"
        assert trade_fact.buyer.account_type is PositionAccountType.CUSTOMER
        assert digest_bytes(payload) == event.payload_digest


def test_what_lc_holds_is_byte_for_byte_what_was_attested() -> None:
    """No translation: L.C.'s record re-serialises to the received bytes, and the
    emulator's record and L.C.'s record are the same JSON document."""
    ccp = OccEmulator()
    buy_report, sell_report = trade(1, "CM-A", "CM-B")
    ccp.submit(buy_report)
    result = ccp.submit(sell_report)
    assert result.novation is not None and result.payload is not None
    admitted = admit_novation(result.events[0], result.payload)
    assert admitted.trade.model_dump(mode="json") == result.novation.model_dump(mode="json")


@pytest.mark.parametrize(
    ("alter", "refusal"),
    [
        (
            lambda b: b.replace(b'"contracts":10', b'"contracts":11'),
            NovationRefusal.DIGEST_MISMATCH,
        ),
        (lambda b: b + b" ", NovationRefusal.DIGEST_MISMATCH),
    ],
    ids=["changed-economics", "trailing-byte"],
)
def test_bytes_other_than_the_attested_ones_are_not_admitted(
    alter: Callable[[bytes], bytes], refusal: NovationRefusal
) -> None:
    event, _, payload = novated()
    with pytest.raises(NovationNotAdmittedError) as refused:
        admit_novation(event, alter(payload))
    assert refused.value.refusal is refusal


def test_an_attested_but_unparseable_payload_is_malformed() -> None:
    event, _, _ = novated()
    garbage = b'{"not":"a novation"}'
    forged = event.model_copy(update={"payload_digest": digest_bytes(garbage)})
    with pytest.raises(NovationNotAdmittedError) as refused:
        admit_novation(forged, garbage)
    assert refused.value.refusal is NovationRefusal.MALFORMED


def test_an_attested_but_reformatted_payload_is_not_canonical() -> None:
    """The same facts, pretty-printed and attested: they parse, but they are not the bytes
    the record re-serialises to, so something reshaped them after the producer wrote them."""
    event, _, payload = novated()
    reformatted = json.dumps(json.loads(payload), indent=2).encode()
    forged = event.model_copy(update={"payload_digest": digest_bytes(reformatted)})
    with pytest.raises(NovationNotAdmittedError) as refused:
        admit_novation(forged, reformatted)
    assert refused.value.refusal is NovationRefusal.NOT_CANONICAL


@pytest.mark.parametrize(
    ("module", "forbidden"),
    [(occ_module, "lc"), (clearing_module, "emulators")],
)
def test_neither_side_imports_the_other(module: object, forbidden: str) -> None:
    source = Path(str(getattr(module, "__file__", ""))).read_text(encoding="utf-8")
    imported = {
        (node.module or "").split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.ImportFrom)
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    assert forbidden not in imported


def test_the_admission_states_what_it_checks() -> None:
    text = " ".join((clearing_module.__doc__ or "").split())
    assert "EXPERIMENTAL" in text and "no translation" in text
