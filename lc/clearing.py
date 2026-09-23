"""Gross bilateral clearing without settlement-domain coupling (W4 AMD1)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Self

from cannae_kernel.canonical import digest
from cannae_kernel.delivery import ClearingMethod
from cannae_kernel.envelopes import ClearingTransformation
from cannae_kernel.ids import LifecycleId, ObligationId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "AccessModel",
    "ClearingPathNotBuiltError",
    "ConservationResult",
    "GrossClearingResult",
    "GrossTrade",
    "assert_gross_conservation",
    "clear_gross",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AccessModel(StrEnum):
    BILATERAL = "BILATERAL"
    SPONSORED = "SPONSORED"
    AGENT = "AGENT"
    FICC_DIRECT = "FICC_DIRECT"


class ClearingPathNotBuiltError(ValueError):
    """The requested clearing path belongs to a later, named programme slice."""


class GrossTrade(_Record):
    """An affirmed allocated trade and its deterministic 1:1 output identity."""

    trade_id: str = Field(min_length=1)
    trade_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    obligation_id: ObligationId
    quantity: Decimal = Field(gt=0)
    cash: Decimal = Field(gt=0)


class GrossObligation(_Record):
    obligation_id: ObligationId
    source_trade_id: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    cash: Decimal = Field(gt=0)


class ConservationResult(_Record):
    input_trade_count: int = Field(gt=0)
    output_obligation_count: int = Field(gt=0)
    input_quantity: Decimal = Field(gt=0)
    output_quantity: Decimal = Field(gt=0)
    input_cash: Decimal = Field(gt=0)
    output_cash: Decimal = Field(gt=0)
    conserved: bool

    @model_validator(mode="after")
    def _must_be_conserved(self) -> Self:
        if not self.conserved:
            raise ValueError("gross clearing conservation cannot be recorded as false")
        return self


class GrossClearingResult(_Record):
    method: ClearingMethod
    access_model: AccessModel
    rule_set_version: str = Field(min_length=1)
    input_trade_ids: tuple[str, ...]
    output_obligation_ids: tuple[ObligationId, ...]
    extinguished_obligation_ids: tuple[ObligationId, ...]
    obligations: tuple[GrossObligation, ...]
    conservation: ConservationResult

    @model_validator(mode="after")
    def _gross_is_identity_preserving(self) -> Self:
        if self.method is not ClearingMethod.GROSS:
            raise ValueError("GrossClearingResult only represents GROSS clearing")
        if self.access_model is not AccessModel.BILATERAL:
            raise ValueError("Wave 4 gross clearing is bilateral only")
        if self.extinguished_obligation_ids:
            raise ValueError("gross clearing extinguishes no obligations")
        if len(self.input_trade_ids) != len(self.output_obligation_ids):
            raise ValueError("gross clearing requires one obligation per trade")
        if len(set(self.input_trade_ids)) != len(self.input_trade_ids):
            raise ValueError("gross clearing cannot consume a trade twice")
        if len(set(self.output_obligation_ids)) != len(self.output_obligation_ids):
            raise ValueError("gross clearing cannot emit an obligation twice")
        return self


def assert_gross_conservation(
    trades: tuple[GrossTrade, ...], obligations: tuple[GrossObligation, ...]
) -> ConservationResult:
    """Raise before a transformation exists unless gross identity is conserved."""
    if not trades:
        raise ValueError("gross clearing requires at least one trade")
    if len(trades) != len(obligations):
        raise AssertionError("gross clearing count conservation failed")
    for trade, obligation in zip(trades, obligations, strict=True):
        if obligation.source_trade_id != trade.trade_id:
            raise AssertionError("gross clearing trade-to-obligation identity failed")
        if obligation.obligation_id != trade.obligation_id:
            raise AssertionError("gross clearing obligation identity failed")
        if obligation.quantity != trade.quantity:
            raise AssertionError("gross clearing quantity conservation failed")
        if obligation.cash != trade.cash:
            raise AssertionError("gross clearing cash conservation failed")

    input_quantity = sum((trade.quantity for trade in trades), Decimal(0))
    output_quantity = sum((item.quantity for item in obligations), Decimal(0))
    input_cash = sum((trade.cash for trade in trades), Decimal(0))
    output_cash = sum((item.cash for item in obligations), Decimal(0))
    if input_quantity != output_quantity:
        raise AssertionError("gross clearing summed quantity conservation failed")
    if input_cash != output_cash:
        raise AssertionError("gross clearing summed cash conservation failed")
    return ConservationResult(
        input_trade_count=len(trades),
        output_obligation_count=len(obligations),
        input_quantity=input_quantity,
        output_quantity=output_quantity,
        input_cash=input_cash,
        output_cash=output_cash,
        conserved=True,
    )


def clear_gross(
    *,
    lifecycle_id: LifecycleId,
    trades: tuple[GrossTrade, ...],
    method: ClearingMethod = ClearingMethod.GROSS,
    access_model: AccessModel = AccessModel.BILATERAL,
    rule_set_version: str = "lc-m6-gross/1.0",
) -> tuple[ClearingTransformation, GrossClearingResult]:
    """Form exactly one bilateral obligation per trade and seal the frozen contract."""
    if method is ClearingMethod.NOVATED_NETTED:
        raise ClearingPathNotBuiltError("NOVATED_NETTED is unbuilt until Wave 5 slice 2")
    if method is not ClearingMethod.GROSS:
        raise ClearingPathNotBuiltError(f"{method.value} is not built in Wave 4")
    if access_model is not AccessModel.BILATERAL:
        raise ClearingPathNotBuiltError(
            f"{access_model.value} access is unbuilt until Wave 5 slice 2 and Atreides T5"
        )

    obligations = tuple(
        GrossObligation(
            obligation_id=trade.obligation_id,
            source_trade_id=trade.trade_id,
            quantity=trade.quantity,
            cash=trade.cash,
        )
        for trade in trades
    )
    conservation = assert_gross_conservation(trades, obligations)
    result = GrossClearingResult(
        method=method,
        access_model=access_model,
        rule_set_version=rule_set_version,
        input_trade_ids=tuple(trade.trade_id for trade in trades),
        output_obligation_ids=tuple(item.obligation_id for item in obligations),
        extinguished_obligation_ids=(),
        obligations=obligations,
        conservation=conservation,
    )
    transformation = ClearingTransformation(
        lifecycle_id=lifecycle_id,
        input_digests=tuple(trade.trade_digest for trade in trades),
        output_digest=digest(result),
        rule_set_version=rule_set_version,
        provenance=Provenance.POLICY_RESULT,
    )
    return transformation, result
