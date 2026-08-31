from decimal import Decimal

import pytest

from level_tester.domain.execution import (
    BracketRule,
    ExecutionConfig,
    ExecutionVariant,
    compute_brackets,
    round_to_tick,
)
from level_tester.domain.models import LevelSide
from level_tester.settings import load_replay_config


def _variant(
    stop_value: str = "0.2",
    take_type: str = "risk_reward",
    take_value: str = "2",
    stop_type: str = "percent_from_entry",
    vid: str = "v1",
) -> ExecutionVariant:
    return ExecutionVariant(
        id=vid,
        name=vid,
        stop_rule=BracketRule(stop_type),
        stop_value=Decimal(stop_value),
        take_rule=BracketRule(take_type),
        take_value=Decimal(take_value),
    )


def test_long_percent_stop_and_rr_take() -> None:
    entry = Decimal("100.00")
    variant = _variant(stop_value="0.2", take_type="risk_reward", take_value="2")
    stop, take = compute_brackets(entry, LevelSide.SUPPORT, variant)
    assert stop == Decimal("99.80")  # 0.2% below entry
    risk = entry - stop
    assert take == entry + risk * 2  # RR 2


def test_long_both_percent() -> None:
    entry = Decimal("100.00")
    variant = _variant(stop_value="0.5", take_type="percent_from_entry", take_value="1.0")
    stop, take = compute_brackets(entry, LevelSide.SUPPORT, variant)
    assert stop == Decimal("99.50")
    assert take == Decimal("101.00")


def test_short_direction() -> None:
    entry = Decimal("100.00")
    variant = _variant(stop_value="0.2", take_type="risk_reward", take_value="2")
    stop, take = compute_brackets(entry, LevelSide.RESISTANCE, variant)
    assert stop == Decimal("100.20")  # above entry
    assert take == Decimal("99.60")  # below entry (RR 2)


def test_tick_rounding() -> None:
    entry = Decimal("100.00")
    variant = _variant(stop_value="0.23", take_type="percent_from_entry", take_value="1.0")
    stop, take = compute_brackets(entry, LevelSide.SUPPORT, variant, tick_size=Decimal("0.05"))
    assert stop == Decimal("99.75")  # 100 - 0.23 = 99.77 -> 0.05 grid
    assert take == Decimal("101.00")


def test_invalid_brackets_after_rounding_rejected() -> None:
    entry = Decimal("100.00")
    variant = _variant(stop_value="0.001", take_type="percent_from_entry", take_value="0.001")
    with pytest.raises(ValueError):
        compute_brackets(entry, LevelSide.SUPPORT, variant, tick_size=Decimal("1.0"))


def test_risk_reward_stop_rejected() -> None:
    with pytest.raises(ValueError):
        ExecutionVariant(
            id="bad",
            name="bad",
            stop_rule=BracketRule.RISK_REWARD,
            stop_value=Decimal("2"),
            take_rule=BracketRule.RISK_REWARD,
            take_value=Decimal("2"),
        )


def test_variants_are_independent() -> None:
    entry = Decimal("100.00")
    a = _variant(stop_value="0.2", take_type="risk_reward", take_value="2", vid="a")
    b = _variant(stop_value="0.5", take_type="percent_from_entry", take_value="1.0", vid="b")
    stop_a, take_a = compute_brackets(entry, LevelSide.SUPPORT, a)
    stop_b, take_b = compute_brackets(entry, LevelSide.SUPPORT, b)
    assert stop_a != stop_b
    assert take_a != take_b


def test_config_rejects_duplicate_variant_ids() -> None:
    variant = _variant()
    with pytest.raises(ValueError):
        ExecutionConfig(variants=(variant, variant))


def test_round_to_tick() -> None:
    assert round_to_tick(Decimal("100.23"), Decimal("0.05")) == Decimal("100.25")
    assert round_to_tick(Decimal("100.22"), Decimal("0.05")) == Decimal("100.20")


def test_load_replay_config_parses_variants() -> None:
    config = load_replay_config("config/default.yaml")
    ids = [variant.id for variant in config.execution.variants]
    assert ids == ["sl_02_rr_2", "sl_03_rr_2", "sl_05_tp_1"]
    assert config.execution.tick_size == Decimal("0.01")
