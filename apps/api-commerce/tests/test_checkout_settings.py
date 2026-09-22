"""Checkout settings and flag (stage E, S1)."""

from __future__ import annotations

import pytest

from app.core.exceptions import ValidationError
from app.tenancy.models import DEFAULT_FEATURE_FLAGS
from app.tenancy.settings_schemas import checkout_settings, validate_setting


def test_checkout_is_off_until_a_store_switches_it_on() -> None:
    assert DEFAULT_FEATURE_FLAGS["checkout"] is False


def test_a_row_written_before_stage_e_still_reads() -> None:
    old = {"reservation_mode": "reserve_on_place", "pix_ttl_minutes": 45}
    settings = checkout_settings({"checkout": old})
    assert settings.pix_ttl_minutes == 45
    assert (settings.auto_accept, settings.customer_cancel_until) == (False, "accepted")
    assert (settings.max_open_orders, settings.refund_four_eyes_threshold_cents) == (3, 20_000)
    assert checkout_settings({}).pix_ttl_minutes == 30


@pytest.mark.parametrize(
    "value",
    [
        {"customer_cancel_until": "delivered"},
        {"max_open_orders": 0},
        {"refund_four_eyes_threshold_cents": -1},
        {"surprise": True},
    ],
)
def test_invalid_checkout_settings_are_refused(value: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        validate_setting("checkout", value)
