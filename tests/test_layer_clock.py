"""W4 WP-7: publish only clocks backed by recorded L.C. events."""

from __future__ import annotations

import pytest
from test_lifecycle import _accepted

from lc.layer_clock import publish_layer_clock
from lc.lifecycle import LifecycleRegister


def test_newest_register_event_supplies_the_published_clock() -> None:
    register = _accepted().journal
    document = publish_layer_clock(register)

    assert document.lifecycle_id == register.lifecycle_id
    assert document.event_id == register.events[-1].event_id
    assert document.times == register.events[-1].times
    assert document.event_digest == register.events[-1].envelope_digest


def test_empty_register_cannot_masquerade_as_an_operating_layer() -> None:
    empty = LifecycleRegister(lifecycle_id=_accepted().journal.lifecycle_id)
    with pytest.raises(ValueError, match="empty register"):
        publish_layer_clock(empty)
