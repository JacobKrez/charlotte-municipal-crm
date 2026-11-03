"""Tests for the deflection arithmetic in ``src/roi``.

The point of these tests is not coverage for its own sake. It is that a
reviewer should be able to change an assumption, re-run the suite, and see
exactly which published numbers move.
"""

from __future__ import annotations

import pytest

from src.roi import (
    ANNUAL_311_CALLS,
    ASSUMED_HANDLE_TIME_MINUTES,
    ASSUMED_MINUTES_SAVED_PER_CALL,
    ASSUMED_PRODUCTIVE_HOURS_PER_FTE,
    CORE_311_AGENTS,
    DEFAULT_DEFLECTION_RATES,
    RESIDENT_CHANNEL_SERVICE_REQUESTS,
    STAFF_ASSISTED_SHARE,
    agent_hours_recovered,
    cost_avoidance,
    deflect_calls,
    fte_equivalent,
    handle_time_hours_avoided,
    scenario,
)


class TestCityConfirmedConstants:
    """The City-confirmed figures must not drift without a decision."""

    def test_annual_calls(self) -> None:
        assert ANNUAL_311_CALLS == 925_000

    def test_resident_channel_service_requests(self) -> None:
        assert RESIDENT_CHANNEL_SERVICE_REQUESTS == 694_000

    def test_core_agents_is_a_range(self) -> None:
        assert CORE_311_AGENTS == (75, 80)

    def test_staff_assisted_share(self) -> None:
        assert STAFF_ASSISTED_SHARE == 0.75

    def test_assumptions_are_positive(self) -> None:
        assert ASSUMED_HANDLE_TIME_MINUTES > 0
        assert ASSUMED_MINUTES_SAVED_PER_CALL > 0
        assert ASSUMED_PRODUCTIVE_HOURS_PER_FTE > 0


class TestDeflectCalls:
    def test_truncates_to_whole_calls(self) -> None:
        # 333 * 0.5 = 166.5 and a partial call is not a call.
        assert deflect_calls(333, 0.5) == 166

    def test_zero_rate_deflects_nothing(self) -> None:
        assert deflect_calls(925_000, 0.0) == 0

    def test_full_rate_deflects_everything(self) -> None:
        assert deflect_calls(925_000, 1.0) == 925_000

    def test_forty_percent_of_city_volume(self) -> None:
        assert deflect_calls(ANNUAL_311_CALLS, 0.40) == 370_000

    def test_thirty_percent_of_city_volume(self) -> None:
        assert deflect_calls(ANNUAL_311_CALLS, 0.30) == 277_500

    def test_fifty_percent_of_city_volume(self) -> None:
        assert deflect_calls(ANNUAL_311_CALLS, 0.50) == 462_500

    def test_sixty_percent_of_city_volume(self) -> None:
        assert deflect_calls(ANNUAL_311_CALLS, 0.60) == 555_000

    def test_rejects_rate_above_one(self) -> None:
        with pytest.raises(ValueError, match="within"):
            deflect_calls(1000, 1.5)

    def test_rejects_negative_rate(self) -> None:
        with pytest.raises(ValueError, match="within"):
            deflect_calls(1000, -0.1)

    def test_rejects_negative_calls(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            deflect_calls(-1, 0.5)


class TestCostAvoidance:
    def test_is_calls_times_cost(self) -> None:
        # 1000 calls, half deflected, is 500 calls at 4.0 each.
        assert cost_avoidance(1_000, 0.5, 4.0) == 2000.0

    def test_zero_rate_costs_nothing(self) -> None:
        assert cost_avoidance(925_000, 0.0, 12.0) == 0.0

    def test_rejects_negative_cost(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            cost_avoidance(1000, 0.5, -1.0)

    def test_module_does_not_assert_a_price(self) -> None:
        """The default scenario must not imply any per-call cost."""
        default = scenario()
        assert default.cost_avoided == 0.0


class TestAgentHoursRecovered:
    def test_applies_to_calls_that_reach_staff(self) -> None:
        # 1000 calls, 40% deflected leaves 600 for staff, 2 minutes each.
        assert agent_hours_recovered(1000, 0.4, 2.0) == pytest.approx(20.0)

    def test_no_calls_to_staff_means_no_hours(self) -> None:
        assert agent_hours_recovered(1000, 1.0, 2.0) == 0.0

    def test_full_deflection_still_yields_zero(self) -> None:
        assert agent_hours_recovered(925_000, 1.0) == 0.0

    def test_rejects_negative_minutes(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            agent_hours_recovered(1000, 0.4, -1.0)


class TestHandleTimeHoursAvoided:
    def test_uses_deflected_calls(self) -> None:
        assert handle_time_hours_avoided(1000, 0.5, 6.0) == pytest.approx(50.0)

    def test_zero_rate_yields_zero(self) -> None:
        assert handle_time_hours_avoided(1000, 0.0) == 0.0

    def test_rejects_negative_handle_time(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            handle_time_hours_avoided(1000, 0.5, -2.0)


class TestFteEquivalent:
    def test_divides_by_assumed_hours(self) -> None:
        assert fte_equivalent(2000.0) == pytest.approx(1.0)

    def test_zero_hours_is_zero_fte(self) -> None:
        assert fte_equivalent(0.0) == 0.0

    def test_rejects_zero_denominator(self) -> None:
        with pytest.raises(ValueError, match="greater than zero"):
            fte_equivalent(100.0, hours_per_fte=0.0)

    def test_rejects_negative_hours(self) -> None:
        with pytest.raises(ValueError, match="negative"):
            fte_equivalent(-5.0)


class TestScenario:
    def test_forty_percent_case_matches_hand_calculation(self) -> None:
        result = scenario(deflection_rate=0.40)
        assert result.calls_deflected == 370_000
        assert result.calls_to_staff == 555_000
        assert result.consolidation_hours == pytest.approx(18_500.0)
        assert result.handle_time_hours == pytest.approx(40_083.33, abs=0.01)

    def test_consolidation_and_handle_time_are_additive(self) -> None:
        result = scenario(deflection_rate=0.40)
        assert result.total_hours == pytest.approx(
            result.consolidation_hours + result.handle_time_hours
        )

    def test_total_fte_uses_assumed_hours(self) -> None:
        result = scenario(deflection_rate=0.40)
        assert result.total_fte == pytest.approx(
            result.total_hours / ASSUMED_PRODUCTIVE_HOURS_PER_FTE
        )

    def test_higher_deflection_yields_more_total_hours(self) -> None:
        """Deflection trade-off: more deflection, less consolidation saving."""
        low = scenario(deflection_rate=0.30)
        high = scenario(deflection_rate=0.60)
        assert high.handle_time_hours > low.handle_time_hours
        assert high.consolidation_hours < low.consolidation_hours
        assert high.total_hours > low.total_hours

    def test_parts_sum_to_total(self) -> None:
        for rate in DEFAULT_DEFLECTION_RATES:
            result = scenario(deflection_rate=rate)
            assert result.calls_deflected + result.calls_to_staff == ANNUAL_311_CALLS

    def test_cost_avoided_is_zero_without_a_price(self) -> None:
        assert scenario(deflection_rate=0.40).cost_avoided == 0.0

    def test_cost_avoided_reflects_supplied_price(self) -> None:
        result = scenario(deflection_rate=0.40, cost_per_call=10.0)
        assert result.cost_avoided == pytest.approx(3_700_000.0)

    def test_rejects_out_of_range_rate(self) -> None:
        with pytest.raises(ValueError, match="within"):
            scenario(deflection_rate=2.0)


class TestDeterminism:
    def test_repeated_calls_agree(self) -> None:
        first = scenario(deflection_rate=0.40)
        second = scenario(deflection_rate=0.40)
        assert first == second

    def test_custom_inputs_flow_through(self) -> None:
        result = scenario(
            annual_calls=1000,
            deflection_rate=0.5,
            minutes_saved_per_call=3.0,
            handle_time_minutes=5.0,
            hours_per_fte=1000.0,
        )
        assert result.calls_deflected == 500
        assert result.consolidation_hours == pytest.approx(25.0)
        assert result.handle_time_hours == pytest.approx(41.666666, abs=0.001)
        assert result.total_fte == pytest.approx(0.0666667, abs=0.0001)
