"""Deflection arithmetic for a municipal 311 modernization proposal.

This module exists because a reviewer should be able to re-derive every
number in ``README.md`` rather than take it on faith. It is pure Python
with no I/O, no clock, no randomness, and no network access, so a given set
of inputs always produces the same outputs.

Two kinds of number appear here, and they are not interchangeable.

**City-confirmed figures.** The City of Charlotte disclosed its FY25 311
operating volumes during the request-for-information process. Those figures
are facts about the City, they are public, and they are the inputs to every
scenario below.

**Working assumptions.** Average handle time, minutes saved per call by
consolidating several applications into one workspace, and productive hours
per FTE are *not* disclosed by the City. We chose defensible values and we
label them as assumptions everywhere they are used. A reader who disagrees
with an assumption can change the constant and re-run: that is the point of
keeping the arithmetic in code.

The cost of a phone call is also not disclosed. Rather than invent a figure,
:func:`cost_avoidance` takes the per-call cost as an argument. The module
never asserts what a call costs.

Provenance of the city-confirmed figures
----------------------------------------
``ANNUAL_311_CALLS`` and ``RESIDENT_CHANNEL_SERVICE_REQUESTS`` come from the
City's FY25 disclosures. ``CORE_311_AGENTS`` is the range the City gave.
``STAFF_ASSISTED_SHARE`` is the split the City stated for resident-submitted
intake. See ``README.md`` for the full list of city-confirmed figures and
for the places where our own source notes failed to reconcile.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "ANNUAL_311_CALLS",
    "RESIDENT_CHANNEL_SERVICE_REQUESTS",
    "CORE_311_AGENTS",
    "STAFF_ASSISTED_SHARE",
    "ASSUMED_HANDLE_TIME_MINUTES",
    "ASSUMED_MINUTES_SAVED_PER_CALL",
    "ASSUMED_PRODUCTIVE_HOURS_PER_FTE",
    "DeflectionOutcome",
    "deflect_calls",
    "cost_avoidance",
    "agent_hours_recovered",
    "handle_time_hours_avoided",
    "fte_equivalent",
    "scenario",
    "DEFAULT_DEFLECTION_RATES",
]


# ---------------------------------------------------------------------------
# City-confirmed figures (FY25)
# ---------------------------------------------------------------------------

#: Annual inbound 311 calls handled by the contact center. City-confirmed.
ANNUAL_311_CALLS: int = 925_000

#: Annual resident-submitted service requests across mobile, web, and
#: citizen-directed calls. City-confirmed.
RESIDENT_CHANNEL_SERVICE_REQUESTS: int = 694_000

#: Core 311 contact-center agents, as a (low, high) range. City-confirmed.
CORE_311_AGENTS: tuple[int, int] = (75, 80)

#: Share of resident-submitted intake that arrives as a citizen-directed call
#: rather than a digital self-service submission. City-confirmed, and
#: explicitly scoped to resident-submitted intake (see the note in
#: ``README.md`` about how this interacts with the total call volume).
STAFF_ASSISTED_SHARE: float = 0.75


# ---------------------------------------------------------------------------
# Working assumptions (not disclosed by the City)
# ---------------------------------------------------------------------------

#: Assumed average handle time for a 311 call, in minutes. Not disclosed.
#: The range used in the source notes was 5 to 8 minutes; we carry the
#: midpoint as the default and expose the range through
#: :func:`handle_time_hours_avoided`.
ASSUMED_HANDLE_TIME_MINUTES: float = 6.5

#: Assumed agent-minutes recovered per call by consolidating the several
#: applications a 311 agent switches between. Not disclosed. The source
#: notes assumed 2 minutes per call.
ASSUMED_MINUTES_SAVED_PER_CALL: float = 2.0

#: Assumed productive (on-task) hours per full-time-equivalent agent per
#: year. 250 working days at 8 hours. Not disclosed.
ASSUMED_PRODUCTIVE_HOURS_PER_FTE: float = 2000.0


#: Deflection rates used in the published scenario table. These are *our*
#: estimates of what an AI-handled 311 path could cover, not measurements and
#: not City targets.
DEFAULT_DEFLECTION_RATES: tuple[float, ...] = (0.30, 0.40, 0.50, 0.60)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def _validate_rate(rate: float) -> float:
    if not 0.0 <= rate <= 1.0:
        raise ValueError(f"deflection rate must be within [0, 1], got {rate!r}")
    return float(rate)


def _validate_non_negative(value: float, name: str) -> float:
    if value < 0:
        raise ValueError(f"{name} must not be negative, got {value!r}")
    return float(value)


def deflect_calls(annual_calls: int, deflection_rate: float) -> int:
    """Return the number of calls the AI path would handle.

    ``annual_calls`` is the annual inbound call volume. ``deflection_rate``
    is the share of those calls the AI path completes without a human
    agent.

    The count is truncated to a whole call. Rounding down is deliberate: a
    partial call is not a call, and rounding up would overstate the benefit
    of the scenario being modelled.
    """
    _validate_non_negative(annual_calls, "annual_calls")
    _validate_rate(deflection_rate)
    return int(annual_calls * deflection_rate)


def cost_avoidance(
    annual_calls: int,
    deflection_rate: float,
    cost_per_call: float,
) -> float:
    """Return the cost avoided by deflecting calls.

    ``cost_per_call`` is supplied by the caller on purpose. The City has not
    disclosed its per-call cost, so this module does not assume one. A
    reader who wants to price the scenario plugs in their own figure; a
    reader who wants the volume-only view should call :func:`deflect_calls`
    instead.

    The formula is ``calls_deflected * cost_per_call``. Nothing else.
    """
    _validate_non_negative(cost_per_call, "cost_per_call")
    calls = deflect_calls(annual_calls, deflection_rate)
    return calls * float(cost_per_call)


def agent_hours_recovered(
    annual_calls: int,
    deflection_rate: float,
    minutes_saved_per_call: float = ASSUMED_MINUTES_SAVED_PER_CALL,
) -> float:
    """Return agent-hours recovered from application-switching consolidation.

    This is the benefit that does *not* require a call to be deflected: a
    call that still reaches a human agent takes less agent time because the
    agent is not switching between several applications to answer it.

    It is measured against the calls that remain with staff, since a
    deflected call consumes no agent time at all.
    """
    _validate_non_negative(minutes_saved_per_call, "minutes_saved_per_call")
    calls_to_staff = int(annual_calls) - deflect_calls(annual_calls, deflection_rate)
    return calls_to_staff * float(minutes_saved_per_call) / 60.0


def handle_time_hours_avoided(
    annual_calls: int,
    deflection_rate: float,
    handle_time_minutes: float = ASSUMED_HANDLE_TIME_MINUTES,
) -> float:
    """Return agent-hours avoided because deflected calls never reach staff.

    This is the handle-time equivalent of the deflection, and it is separate
    from :func:`agent_hours_recovered`, which is the *additional* saving on
    the calls that remain. They are additive, not alternatives.
    """
    _validate_non_negative(handle_time_minutes, "handle_time_minutes")
    calls = deflect_calls(annual_calls, deflection_rate)
    return calls * float(handle_time_minutes) / 60.0


def fte_equivalent(
    hours: float,
    hours_per_fte: float = ASSUMED_PRODUCTIVE_HOURS_PER_FTE,
) -> float:
    """Convert agent-hours into full-time-equivalents.

    The denominator is an assumption (250 days times 8 hours). Recovered
    hours are *capacity*, not headcount: the same arithmetic can be
    described as hours, as FTE-equivalents, or as a staffing change, and the
    three are not interchangeable for a city that cannot hire its way out
    of a service-level problem.
    """
    _validate_non_negative(hours, "hours")
    _validate_non_negative(hours_per_fte, "hours_per_fte")
    if hours_per_fte == 0:
        raise ValueError("hours_per_fte must be greater than zero")
    return float(hours) / float(hours_per_fte)


# ---------------------------------------------------------------------------
# Composite scenario
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeflectionOutcome:
    """The full arithmetic for one deflection rate.

    Attributes are named for what they measure rather than for where they
    come from, so that a reader can tell which effects are independent.

    ``hours_per_fte`` is carried on the outcome rather than read from the
    module default, because an outcome computed with a custom denominator
    has to keep using that denominator when it is converted to
    full-time-equivalents. Reading the default here instead would silently
    contradict the inputs that produced the outcome.
    """

    deflection_rate: float
    annual_calls: int
    calls_deflected: int
    calls_to_staff: int
    cost_avoided: float
    consolidation_hours: float
    handle_time_hours: float
    hours_per_fte: float = ASSUMED_PRODUCTIVE_HOURS_PER_FTE

    @property
    def total_hours(self) -> float:
        """Consolidation hours plus handle-time hours."""
        return self.consolidation_hours + self.handle_time_hours

    @property
    def total_fte(self) -> float:
        """Total recovered capacity expressed as FTE-equivalents."""
        return fte_equivalent(self.total_hours, self.hours_per_fte)

    @property
    def consolidation_fte(self) -> float:
        """Consolidation benefit alone, in FTE-equivalents."""
        return fte_equivalent(self.consolidation_hours, self.hours_per_fte)

    @property
    def handle_time_fte(self) -> float:
        """Handle-time benefit alone, in FTE-equivalents."""
        return fte_equivalent(self.handle_time_hours, self.hours_per_fte)


def scenario(
    annual_calls: int = ANNUAL_311_CALLS,
    deflection_rate: float = 0.40,
    cost_per_call: float = 0.0,
    minutes_saved_per_call: float = ASSUMED_MINUTES_SAVED_PER_CALL,
    handle_time_minutes: float = ASSUMED_HANDLE_TIME_MINUTES,
    hours_per_fte: float = ASSUMED_PRODUCTIVE_HOURS_PER_FTE,
) -> DeflectionOutcome:
    """Compute the full outcome for one deflection rate.

    ``cost_per_call`` defaults to zero so that the default call never
    implies a price. Pass a figure to fill in :attr:`DeflectionOutcome.
    cost_avoided`.
    """
    _validate_rate(deflection_rate)
    calls_deflected = deflect_calls(annual_calls, deflection_rate)
    calls_to_staff = int(annual_calls) - calls_deflected
    consolidation_hours = calls_to_staff * float(minutes_saved_per_call) / 60.0
    handle_time_hours = calls_deflected * float(handle_time_minutes) / 60.0
    avoided = (
        calls_deflected * float(cost_per_call) if cost_per_call else 0.0
    )
    return DeflectionOutcome(
        deflection_rate=float(deflection_rate),
        annual_calls=int(annual_calls),
        calls_deflected=calls_deflected,
        calls_to_staff=calls_to_staff,
        cost_avoided=avoided,
        consolidation_hours=consolidation_hours,
        handle_time_hours=handle_time_hours,
        hours_per_fte=float(hours_per_fte),
    )
