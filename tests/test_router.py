"""Tier router policy tests (fyp.txt S6/S7 synergy)."""

from hive.llm.router import RouteInputs, Tier, route


def test_default_is_cheap():
    assert route(RouteInputs()) is Tier.CHEAP


def test_injection_escalates():
    assert route(RouteInputs(injection_flagged=True)) is Tier.STRONG


def test_elicitation_escalates():
    assert route(RouteInputs(eliciting_hvi=True)) is Tier.STRONG


def test_consistency_risk_escalates():
    assert route(RouteInputs(consistency_risk=True)) is Tier.STRONG


def test_injection_takes_priority():
    # Highest-priority reason wins even if several signals are set.
    assert route(RouteInputs(injection_flagged=True, eliciting_hvi=True)) is Tier.STRONG
