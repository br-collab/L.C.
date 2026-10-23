"""Gross bilateral clearing without settlement-domain coupling (W4 AMD1)."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self

from cannae_kernel.canonical import digest, digest_bytes
from cannae_kernel.delivery import ClearingMethod
from cannae_kernel.envelopes import ClearingTransformation
from cannae_kernel.ids import LifecycleId, ObligationId
from cannae_kernel.provenance import Provenance
from pydantic import BaseModel, ConfigDict, Field, model_validator

from lc.ficc import FiccGsdDocument, FiccNetObligation, consume_ficc_gsd_result

__all__ = [
    "AccessModel",
    "ClearingPathNotBuiltError",
    "ConservationResult",
    "FiccClearingResult",
    "GrossClearingResult",
    "GrossTrade",
    "assert_gross_conservation",
    "clear_gross",
    "consume_and_record_ficc",
]


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class AccessModel(StrEnum):
    BILATERAL = "BILATERAL"
    SPONSORED = "SPONSORED"
    AGENT = "AGENT"
    FICC_DIRECT = "FICC_DIRECT"


class ClearingPathNotBuiltError(ValueError):
    """The requested clearing path is unsupported by this specific operation."""


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


class FiccClearingResult(_Record):
    """L.C.'s recorded reading of an attested Atreides FICC result."""

    method: Literal[ClearingMethod.NOVATED_NETTED] = ClearingMethod.NOVATED_NETTED
    method_label: Literal["FICC_GSD_NOVATED_NETTED"] = "FICC_GSD_NOVATED_NETTED"
    access_model: AccessModel
    clearing_agency: Literal["FICC_GSD"] = "FICC_GSD"
    observation_seat: Literal["FICC_MEMBER"] = "FICC_MEMBER"
    observed_member_id: str
    rule_set_version: str
    processing_calendar_id: str
    input_trade_ids: tuple[str, ...]
    input_obligation_ids: tuple[str, ...]
    output_position_count: int = Field(gt=0)
    net_settlement_obligations: tuple[FiccNetObligation, ...]


def _record_ficc_netted(
    *, lifecycle_id: LifecycleId, document: FiccGsdDocument, source_payload_digest: str
) -> tuple[ClearingTransformation, FiccClearingResult]:
    """Record a verified FICC result; parsing and digest verification happen upstream."""

    access_models = {
        "FICC_DIRECT": AccessModel.FICC_DIRECT,
        "FICC_SPONSORED": AccessModel.SPONSORED,
        "FICC_AGENT": AccessModel.AGENT,
    }
    novated_totals: dict[tuple[str, str, object], tuple[Decimal, Decimal]] = {}
    for obligation in document.novated_obligations:
        key = (obligation.member_id, obligation.cusip, obligation.settlement_date)
        quantity, money = novated_totals.get(key, (Decimal(0), Decimal(0)))
        novated_totals[key] = (
            quantity + obligation.signed_quantity,
            money + obligation.signed_settlement_amount,
        )
    net_totals = {
        (position.member_id, position.cusip, position.settlement_date): (
            position.net_quantity,
            position.net_settlement_amount,
        )
        for position in document.net_settlement_obligations
    }
    if novated_totals != net_totals:
        raise ValueError(
            "FICC net positions do not conserve novated par and money "
            "per member, CUSIP, and settlement date"
        )

    result = FiccClearingResult(
        access_model=access_models[document.access_path],
        observed_member_id=document.observed_member_id,
        rule_set_version=document.rule_set_version,
        processing_calendar_id=document.processing_calendar_id,
        input_trade_ids=tuple(
            sorted(
                trade_id
                for obligation in document.novated_obligations
                for trade_id in obligation.trade_ids
            )
        ),
        input_obligation_ids=tuple(
            sorted(obligation.obligation_id for obligation in document.novated_obligations)
        ),
        output_position_count=len(document.net_settlement_obligations),
        net_settlement_obligations=document.net_settlement_obligations,
    )
    transformation = ClearingTransformation(
        lifecycle_id=lifecycle_id,
        input_digests=(source_payload_digest,),
        output_digest=digest(result),
        rule_set_version=document.rule_set_version,
        provenance=Provenance.POLICY_RESULT,
    )
    return transformation, result


def consume_and_record_ficc(
    *, lifecycle_id: LifecycleId, header_bytes: bytes, payload: bytes
) -> tuple[ClearingTransformation, FiccClearingResult]:
    """Verify exact Atreides bytes and record their FICC netting result atomically."""

    document = consume_ficc_gsd_result(header_bytes, payload)
    return _record_ficc_netted(
        lifecycle_id=lifecycle_id,
        document=document,
        source_payload_digest=digest_bytes(payload),
    )


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
        raise ClearingPathNotBuiltError(
            "NOVATED_NETTED is not gross clearing; consume the attested FICC result "
            "with record_ficc_netted"
        )
    if method is not ClearingMethod.GROSS:
        raise ClearingPathNotBuiltError(f"{method.value} is not built in Wave 4")
    if access_model is not AccessModel.BILATERAL:
        raise ClearingPathNotBuiltError(
            f"{access_model.value} access is not valid for bilateral gross clearing"
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
