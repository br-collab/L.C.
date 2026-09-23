"""W4 WP-5 acceptance tests for amended gross clearing."""

from decimal import Decimal
from hashlib import sha256

import pytest
from cannae_kernel.canonical import digest
from cannae_kernel.delivery import ClearingMethod
from cannae_kernel.envelopes import ClearingTransformation
from cannae_kernel.ids import LifecycleId, ObligationId

from lc.clearing import (
    AccessModel,
    ClearingPathNotBuiltError,
    GrossObligation,
    GrossTrade,
    assert_gross_conservation,
    clear_gross,
)

LIFECYCLE_ID = LifecycleId("lif_01M2P20SY00000000000000001")
OBLIGATION_A = ObligationId("obl_01M2P20SY00000000000000001")
OBLIGATION_B = ObligationId("obl_01M2P20SY00000000000000002")


def _trade(name: str, obligation_id: ObligationId, quantity: str, cash: str) -> GrossTrade:
    return GrossTrade(
        trade_id=name,
        trade_digest=f"sha256:{sha256(name.encode()).hexdigest()}",
        obligation_id=obligation_id,
        quantity=Decimal(quantity),
        cash=Decimal(cash),
    )


def test_gross_clearing_is_one_trade_to_one_obligation_and_validates_frozen_contract() -> None:
    trades = (
        _trade("trade-a", OBLIGATION_A, "100", "99750.25"),
        _trade("trade-b", OBLIGATION_B, "50", "50125.75"),
    )
    transformation, result = clear_gross(lifecycle_id=LIFECYCLE_ID, trades=trades)

    assert result.method is ClearingMethod.GROSS
    assert result.access_model is AccessModel.BILATERAL
    assert result.input_trade_ids == ("trade-a", "trade-b")
    assert result.output_obligation_ids == (OBLIGATION_A, OBLIGATION_B)
    assert result.extinguished_obligation_ids == ()
    assert result.conservation.input_trade_count == 2
    assert result.conservation.output_obligation_count == 2
    assert result.conservation.input_quantity == result.conservation.output_quantity
    assert result.conservation.input_cash == result.conservation.output_cash
    assert transformation.output_digest == digest(result)
    assert transformation.input_digests == tuple(trade.trade_digest for trade in trades)
    round_trip = ClearingTransformation.model_validate_json(transformation.model_dump_json())
    assert round_trip == transformation
    assert set(transformation.model_fields_set) == {
        "lifecycle_id",
        "input_digests",
        "output_digest",
        "rule_set_version",
        "provenance",
    }


def test_quantity_perturbation_fails_loudly() -> None:
    trade = _trade("trade-a", OBLIGATION_A, "100", "99750.25")
    perturbed = GrossObligation(
        obligation_id=trade.obligation_id,
        source_trade_id=trade.trade_id,
        quantity=Decimal("99"),
        cash=trade.cash,
    )
    with pytest.raises(AssertionError, match="quantity conservation failed"):
        assert_gross_conservation((trade,), (perturbed,))


def test_cash_perturbation_fails_loudly() -> None:
    trade = _trade("trade-a", OBLIGATION_A, "100", "99750.25")
    perturbed = GrossObligation(
        obligation_id=trade.obligation_id,
        source_trade_id=trade.trade_id,
        quantity=trade.quantity,
        cash=Decimal("0.01"),
    )
    with pytest.raises(AssertionError, match="cash conservation failed"):
        assert_gross_conservation((trade,), (perturbed,))


def test_novated_netted_names_the_deferred_slice() -> None:
    with pytest.raises(
        ClearingPathNotBuiltError, match="NOVATED_NETTED is unbuilt until Wave 5 slice 2"
    ):
        clear_gross(
            lifecycle_id=LIFECYCLE_ID,
            trades=(_trade("trade-a", OBLIGATION_A, "100", "99750.25"),),
            method=ClearingMethod.NOVATED_NETTED,
        )


@pytest.mark.parametrize(
    "access_model", [AccessModel.SPONSORED, AccessModel.AGENT, AccessModel.FICC_DIRECT]
)
def test_non_bilateral_access_names_wave_5_and_atreides_t5(
    access_model: AccessModel,
) -> None:
    with pytest.raises(ClearingPathNotBuiltError, match="Wave 5 slice 2 and Atreides T5"):
        clear_gross(
            lifecycle_id=LIFECYCLE_ID,
            trades=(_trade("trade-a", OBLIGATION_A, "100", "99750.25"),),
            access_model=access_model,
        )
