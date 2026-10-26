"""Rule 29 property evidence for listed-option premium and exercise money paths."""

from __future__ import annotations

from decimal import Decimal
from random import Random

from cannae_kernel.canonical import canonical_bytes, digest_bytes
from test_occ_boundary import novated
from test_option_exercise import series
from test_option_obligation import (
    CAPABILITIES,
    CCP,
    FORMED_AT,
    ROUTE,
    SETTLES,
    exercise_obligations,
    legs,
    party,
    settling,
)

from lc.option_clearing import admit_novation
from lc.option_obligation import NovationSide, form_premium_obligation
from lc.options import OptionRight


def test_option_premium_conserves_money_for_200_generated_examples() -> None:
    """Seed 29007: buyer pays and seller receives the same cent-denominated premium."""
    rng = Random(29007)
    buyer_event, seller_event, payload = novated()
    base = admit_novation(buyer_event, payload, capabilities=CAPABILITIES).trade
    for _ in range(200):
        premium = Decimal(rng.randint(1, 100_000_000)) / 100
        trade = base.model_copy(update={"premium": premium})
        trade_bytes = canonical_bytes(trade)
        formed = []
        for side, source_event in (
            (NovationSide.BUYER, buyer_event),
            (NovationSide.SELLER, seller_event),
        ):
            event = source_event.model_copy(update={"payload_digest": digest_bytes(trade_bytes)})
            admitted = admit_novation(event, trade_bytes, capabilities=CAPABILITIES)
            member = admitted.trade.buyer if side is NovationSide.BUYER else admitted.trade.seller
            formed.append(
                form_premium_obligation(
                    admitted,
                    trade_bytes,
                    side=side,
                    member=party(member.clearing_member_id),
                    central_counterparty=CCP,
                    settlement_date=SETTLES,
                    route=ROUTE,
                    session=settling(event.session),
                    formed_at=FORMED_AT,
                )
            )
        buyer_cash = formed[0].payload.cash_leg
        seller_cash = formed[1].payload.cash_leg
        assert buyer_cash is not None and seller_cash is not None
        assert buyer_cash.total == seller_cash.total == premium
        assert buyer_cash.total == buyer_cash.total.quantize(Decimal("0.01"))


def test_option_exercise_conserves_shares_and_strike_for_200_generated_examples() -> None:
    """Seed 29008: holder and writer obligations conserve whole shares and cents."""
    rng = Random(29008)
    for _ in range(200):
        right = rng.choice(list(OptionRight))
        strike = Decimal(rng.randint(1, 10_000)) / 100
        chosen = series(right=right, strike=strike)
        formed, expired, _ = exercise_obligations(chosen)
        holder_side, writer_side = legs(formed, True), legs(formed, False)
        holder_shares = sum(
            (
                obligation.payload.securities_leg.quantity
                for obligation in holder_side
                if obligation.payload.securities_leg is not None
            ),
            Decimal(0),
        )
        writer_shares = sum(
            (
                obligation.payload.securities_leg.quantity
                for obligation in writer_side
                if obligation.payload.securities_leg is not None
            ),
            Decimal(0),
        )
        holder_strike = sum(
            (
                obligation.payload.cash_leg.total
                for obligation in holder_side
                if obligation.payload.cash_leg is not None
            ),
            Decimal(0),
        )
        writer_strike = sum(
            (
                obligation.payload.cash_leg.total
                for obligation in writer_side
                if obligation.payload.cash_leg is not None
            ),
            Decimal(0),
        )
        expected_shares = Decimal(100 * expired.exercised_contracts)
        expected_strike = strike * expected_shares
        assert holder_shares == writer_shares == expected_shares
        assert holder_strike == writer_strike == expected_strike
        assert expected_strike == expected_strike.quantize(Decimal("0.01"))
