"""Firm market-access control under SEC Rule 15c3-5.

This gate is separate from :mod:`lc.policy`: strategy bounds cannot replace
the broker-dealer's controls. Limits remain under the firm's direct and
exclusive control. Annual review and chief executive certification are
declared, not built. Decisions are records only; this module submits nothing.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self, TypeVar

from cannae_kernel.absence import require_recorded
from cannae_kernel.actor import ActorRef
from cannae_kernel.canonical import digest, digest_bytes
from cannae_kernel.disposition import Disposition
from cannae_kernel.ids import OrderId
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from lc.events import LifecycleState, OrderEvent
from lc.lifecycle import EventInput, LifecycleRegister, replay, transition

# 17 CFR 240.15c3-5 and 17 CFR 242.200/203, quoted from the pinned sources
# beside the market-access and short-sale controls that implement them.
RULE_15C3_5_PARAGRAPH = (
    "Controls must prevent orders exceeding credit thresholds, prevent erroneous orders, "
    "restrict access, and assure appropriate surveillance personnel receive post-trade "
    "execution reports."
)
REG_SHO_200_PARAGRAPH = "Sell orders must be marked long, short, or short exempt."
REG_SHO_203_PARAGRAPH = (
    "Before effecting a short sale, a broker-dealer must borrow, arrange to borrow, or have "
    "reasonable grounds to believe the security can be borrowed for delivery when due."
)


class _Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class SellMark(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"
    SHORT_EXEMPT = "SHORT_EXEMPT"


class HaltScope(StrEnum):
    FIRM = "FIRM"
    ACCOUNT = "ACCOUNT"
    STRATEGY = "STRATEGY"


class FirmHalt(_Record):
    halt_id: str = Field(min_length=1)
    scope: HaltScope
    scope_id: str | None
    authority: ActorRef
    recorded_at: datetime
    released_at: datetime | None = None

    @model_validator(mode="after")
    def _authority_and_scope(self) -> Self:
        if not self.authority.authenticated:
            raise ValueError("firm halt authority must be authenticated")
        if (self.scope is HaltScope.FIRM) != (self.scope_id is None):
            raise ValueError("firm scope has no id; account and strategy scopes require one")
        return self

    def applies(self, *, account_id: str, strategy_id: str) -> bool:
        if self.released_at is not None:
            return False
        return (
            self.scope is HaltScope.FIRM
            or (self.scope is HaltScope.ACCOUNT and self.scope_id == account_id)
            or (self.scope is HaltScope.STRATEGY and self.scope_id == strategy_id)
        )


class CheckResult(_Record):
    code: str
    passed: bool
    reason: str


class LimitsManifest(_Record):
    version: str = Field(min_length=1)
    authority: str = Field(min_length=1)
    account_credit_limits: dict[str, Decimal]
    firm_aggregate_threshold: Decimal = Field(gt=0)
    price_collar_percent: Decimal = Field(gt=0)
    maximum_quantity: Decimal = Field(gt=0)
    maximum_notional: Decimal = Field(gt=0)
    duplicate_window_seconds: int = Field(gt=0)
    restricted_issues: tuple[str, ...] = ()
    halted_issues: tuple[str, ...] = ()
    restricted_accounts: tuple[str, ...] = ()


class LimitChange(_Record):
    previous_version: str
    new_version: str
    authority: str
    changed_at: datetime
    manifest_digest: str


def record_limit_change(
    previous: LimitsManifest, current: LimitsManifest, at: datetime
) -> LimitChange:
    return LimitChange(
        previous_version=previous.version,
        new_version=current.version,
        authority=current.authority,
        changed_at=at,
        manifest_digest=digest(current),
    )


class CapacityPosition(_Record):
    issue: str
    quantity: Decimal


class CapacityAccount(_Record):
    account_id: str
    buying_power: Decimal
    margin_excess: Decimal
    margin_result_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    positions: tuple[CapacityPosition, ...]


class CapacityDocument(_Record):
    schema_version: Literal["atreides.equities_account_capacity/2"]
    as_of: datetime
    maximum_age_seconds: int
    synthetic: Literal[True]
    journal_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    accounts: tuple[CapacityAccount, ...]


class Locate(_Record):
    locate_id: str
    source: Literal["SYNTHETIC_EASY_TO_BORROW", "SYNTHETIC_LENDER"]
    located_at: datetime
    expires_at: datetime
    issue: str
    quantity: Decimal


class LocateDocument(_Record):
    schema_version: Literal["atreides.equities_locates/1"]
    as_of: datetime
    synthetic: Literal[True]
    locates: tuple[Locate, ...]


class AttestedInput(_Record):
    payload: bytes
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class ProducerAttestation(_Record):
    schema_version: Literal["atreides.attested_document/1"]
    document_type: Literal["ACCOUNT_CAPACITY", "LOCATES"]
    payload_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    payload: bytes


def consume_producer_attestation(raw: bytes, expected_type: str) -> AttestedInput:
    document = ProducerAttestation.model_validate_json(raw, strict=True)
    if document.document_type != expected_type:
        raise ValueError(f"expected {expected_type}, received {document.document_type}")
    if digest_bytes(document.payload) != document.payload_digest:
        raise ValueError("producer attestation payload digest mismatch")
    return AttestedInput(payload=document.payload, payload_digest=document.payload_digest)


class ChildRelease(_Record):
    order_event: OrderEvent
    lifecycle_register: LifecycleRegister
    reference_price: Decimal = Field(gt=0)
    sell_mark: SellMark | None = None
    locate_id: str | None = None
    submitted_at: datetime


class MarketAccessDecision(_Record):
    order_id: OrderId
    order_event_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    disposition: Disposition
    terminal: bool
    checks: tuple[CheckResult, ...]
    limits_version: str
    limits_digest: str
    capacity_digest: str | None
    capacity_journal_digest: str | None
    capacity_margin_result_digest: str | None
    locates_digest: str | None


T = TypeVar("T", bound=BaseModel)


def _parse_attested(item: AttestedInput | None, model: type[T]) -> tuple[T | None, str]:
    if item is None:
        return None, "DOCUMENT_MISSING"
    if digest_bytes(item.payload) != item.payload_digest:
        return None, "DOCUMENT_TAMPERED"
    try:
        return model.model_validate_json(item.payload, strict=True), "OK"
    except ValidationError:
        return None, "DOCUMENT_UNREADABLE"


class MarketAccessGate:
    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str, Decimal, Decimal], datetime] = {}
        self._locate_used: dict[str, Decimal] = {}
        self.decisions: list[MarketAccessDecision] = []

    def evaluate(  # noqa: PLR0912, PLR0915 - controls remain explicit evidence
        self,
        order: ChildRelease,
        *,
        limits: LimitsManifest,
        capacity_input: AttestedInput | None,
        locates_input: AttestedInput | None,
        halts: tuple[FirmHalt, ...] = (),
    ) -> MarketAccessDecision:
        terms = order.order_event.payload.terms
        if terms is None:
            raise ValueError("market access requires terms sealed in the order event")
        order_id = order.order_event.payload.order_id
        quantity = require_recorded(order.order_event.payload.quantity)
        snapshot = replay(order.lifecycle_register.events)
        if order_id not in snapshot.orders:
            raise ValueError("sealed order event is not in the lifecycle register")
        live = {
            LifecycleState.PARENT_ACTIVE,
            LifecycleState.MARKET_ACCESS_PASSED,
            LifecycleState.PARTIALLY_EXECUTED,
        }
        firm_open_order_notional = Decimal(0)
        account_open_order_notional = Decimal(0)
        termless_live_orders: list[str] = []
        for item_id, item in snapshot.orders.items():
            if item_id == order_id or item.state not in live:
                continue
            if item.terms is None:
                termless_live_orders.append(str(item_id))
                continue
            open_notional = (item.quantity - item.executed_quantity) * item.terms.control_price
            firm_open_order_notional += open_notional
            if item.terms.account_id == terms.account_id:
                account_open_order_notional += open_notional
        capacity, capacity_status = _parse_attested(capacity_input, CapacityDocument)
        locates, locates_status = _parse_attested(locates_input, LocateDocument)
        checks: list[CheckResult] = []

        def check(code: str, passed: bool, reason: str) -> None:
            checks.append(CheckResult(code=code, passed=passed, reason=reason))

        check(
            "OPEN_ORDER_TERMS",
            not termless_live_orders,
            (
                "OK"
                if not termless_live_orders
                else f"LIVE_ORDER_TERMS_MISSING:{','.join(termless_live_orders)}"
            ),
        )

        now = order.submitted_at
        capacity_fresh = capacity is not None and now - capacity.as_of <= timedelta(
            seconds=capacity.maximum_age_seconds
        )
        check(
            "CAPACITY_DOCUMENT",
            capacity_fresh,
            capacity_status if capacity is None else ("OK" if capacity_fresh else "DOCUMENT_STALE"),
        )
        account = (
            next((item for item in capacity.accounts if item.account_id == terms.account_id), None)
            if capacity
            else None
        )
        notional = quantity * terms.control_price
        credit_limit = limits.account_credit_limits.get(terms.account_id)
        check(
            "ACCOUNT_CREDIT",
            account is not None
            and credit_limit is not None
            and account_open_order_notional + notional <= min(account.buying_power, credit_limit),
            "ACCOUNT_CREDIT_LIMIT",
        )
        check(
            "FIRM_AGGREGATE",
            firm_open_order_notional + notional <= limits.firm_aggregate_threshold,
            "FIRM_AGGREGATE_LIMIT",
        )
        collar = abs(terms.control_price - order.reference_price) / order.reference_price
        check("PRICE_COLLAR", collar <= limits.price_collar_percent, "ERRONEOUS_PRICE")
        check("MAXIMUM_QUANTITY", quantity <= limits.maximum_quantity, "MAXIMUM_QUANTITY")
        check("MAXIMUM_NOTIONAL", notional <= limits.maximum_notional, "MAXIMUM_NOTIONAL")
        key = (terms.account_id, terms.issue, terms.side, quantity, terms.control_price)
        duplicate = (
            key in self._seen
            and (now - self._seen[key]).total_seconds() <= limits.duplicate_window_seconds
        )
        check("DUPLICATE", not duplicate, "DUPLICATE_ORDER")
        check("RESTRICTED_LIST", terms.issue not in limits.restricted_issues, "RESTRICTED_ISSUE")
        check("TRADING_HALT", terms.issue not in limits.halted_issues, "TRADING_HALT")
        check(
            "ACCOUNT_RESTRICTION",
            terms.account_id not in limits.restricted_accounts,
            "ACCOUNT_RESTRICTED",
        )
        active_halt = next(
            (
                halt
                for halt in halts
                if halt.applies(account_id=terms.account_id, strategy_id=terms.strategy_id)
            ),
            None,
        )
        check(
            "FIRM_HALT",
            active_halt is None,
            f"FIRM_HALT:{active_halt.halt_id}" if active_halt else "NO_ACTIVE_HALT",
        )
        marking_ok = terms.side != "SELL" or order.sell_mark is not None
        check("SELL_MARKING", marking_ok, "SELL_MARK_MISSING")
        position = (
            next((p.quantity for p in account.positions if p.issue == terms.issue), Decimal(0))
            if account
            else Decimal(0)
        )
        long_ok = (
            terms.side != "SELL" or order.sell_mark is not SellMark.LONG or position >= quantity
        )
        check("LONG_OWNERSHIP", long_ok, "LONG_POSITION_INSUFFICIENT")
        locate_ok = True
        locate_reason = "NOT_REQUIRED"
        if terms.side == "SELL" and order.sell_mark in {SellMark.SHORT, SellMark.SHORT_EXEMPT}:
            locate = (
                next((item for item in locates.locates if item.locate_id == order.locate_id), None)
                if locates
                else None
            )
            if locates is None:
                locate_ok, locate_reason = False, locates_status
            elif locate is None:
                locate_ok, locate_reason = False, "LOCATE_MISSING"
            elif locate.issue != terms.issue:
                locate_ok, locate_reason = False, "LOCATE_ISSUE_MISMATCH"
            elif now > locate.expires_at:
                locate_ok, locate_reason = False, "LOCATE_EXPIRED"
            elif self._locate_used.get(locate.locate_id, Decimal(0)) + quantity > locate.quantity:
                locate_ok, locate_reason = False, "LOCATE_INSUFFICIENT"
            else:
                locate_reason = "LOCATE_AVAILABLE"
        check("LOCATE", locate_ok, locate_reason)
        passed = all(item.passed for item in checks)
        if passed:
            self._seen[key] = now
            if order.locate_id is not None and order.sell_mark in {
                SellMark.SHORT,
                SellMark.SHORT_EXEMPT,
            }:
                self._locate_used[order.locate_id] = (
                    self._locate_used.get(order.locate_id, Decimal(0)) + quantity
                )
        decision = MarketAccessDecision(
            order_id=order_id,
            order_event_digest=order.order_event.envelope_digest,
            disposition=Disposition.PASS if passed else Disposition.HOLD,
            terminal=not passed,
            checks=tuple(checks),
            limits_version=limits.version,
            limits_digest=digest(limits),
            capacity_digest=capacity_input.payload_digest if capacity_input else None,
            capacity_journal_digest=capacity.journal_digest if capacity else None,
            capacity_margin_result_digest=account.margin_result_digest if account else None,
            locates_digest=locates_input.payload_digest if locates_input else None,
        )
        self.decisions.append(decision)
        return decision


def record_decision(
    register: LifecycleRegister,
    decision: MarketAccessDecision,
    *,
    actor: ActorRef,
    event: EventInput,
) -> LifecycleRegister:
    return transition(
        register,
        order_id=decision.order_id,
        target=(
            LifecycleState.MARKET_ACCESS_PASSED
            if decision.disposition is Disposition.PASS
            else LifecycleState.MARKET_ACCESS_REFUSED
        ),
        reason="; ".join(item.reason for item in decision.checks if not item.passed)
        or "all firm market access controls passed",
        actor=actor,
        event=event,
    )
