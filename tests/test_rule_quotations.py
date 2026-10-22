"""Rule 31: enforcement code and tests cite the same pinned paragraphs."""

from lc.market_access import (
    REG_SHO_200_PARAGRAPH,
    REG_SHO_203_PARAGRAPH,
    RULE_15C3_5_PARAGRAPH,
)


def test_rule_15c3_5_market_access_cites_the_pinned_paragraph() -> None:
    assert "credit thresholds" in RULE_15C3_5_PARAGRAPH
    assert "erroneous orders" in RULE_15C3_5_PARAGRAPH


def test_reg_sho_rule_200_sell_marking_cites_the_pinned_paragraph() -> None:
    assert REG_SHO_200_PARAGRAPH == "Sell orders must be marked long, short, or short exempt."


def test_reg_sho_rule_203_locate_cites_the_pinned_paragraph() -> None:
    assert "borrow, arrange to borrow" in REG_SHO_203_PARAGRAPH
    assert "borrowed for delivery when due" in REG_SHO_203_PARAGRAPH
