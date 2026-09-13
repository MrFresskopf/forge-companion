"""Pure SG diagnostics: candidates are not quality approval."""

from dataclasses import FrozenInstanceError, replace
from datetime import timedelta, timezone
from decimal import Decimal, Inexact, Rounded, localcontext
from inspect import signature
from itertools import permutations

import pytest
from test_telemetry_sg_advisor import BASE, SECOND

from forge_companion import spunding_advisor as advisor
from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.sg_trend import exact_ns
from forge_companion.telemetry import DeviceKind, parse_rapt_telemetry


def explain(readings=(BASE, SECOND), **overrides):
    policy = dict(
        gravity_unit="sg",
        now_ns=exact_ns(SECOND),
        trigger_sg=Decimal("1.020"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    policy.update(overrides)
    result = advisor.explain_telemetry_sg(readings, **policy)
    assert advisor.advise_telemetry_sg(readings, **policy) is result.status
    return result


@pytest.mark.parametrize(
    "readings,policy,reason",
    [
        ((BASE, SECOND), {"gravity_unit": None}, "UNDECLARED_UNIT"),
        ([BASE, SECOND], {}, "INVALID_COLLECTION"),
        ((), {}, "NO_READINGS"),
        ((BASE, SECOND, None), {}, "INVALID_READING"),
        ((BASE, SECOND, replace(BASE, source="")), {}, "INVALID_READING"),
        ((BASE, SECOND, replace(BASE, observed_at=None)), {}, "INVALID_TIMESTAMP"),
        ((BASE, SECOND, replace(BASE, gravity_raw=None)), {}, "MISSING_SG"),
        ((BASE, SECOND, replace(BASE, gravity_raw=True)), {}, "INVALID_SG"),
        ((BASE, SECOND, replace(BASE, source="other")), {}, "MIXED_STREAM"),
        ((BASE, SECOND, replace(BASE, gravity_raw=1.01)), {}, "CONFLICTING_SG"),
        ((BASE, replace(SECOND, reading_id=BASE.reading_id)), {}, "REUSED_ID"),
    ],
)
def test_integrity_failure_discards_all_candidates(readings, policy, reason):
    result = explain(readings, **policy)
    assert result == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.NO_DECISION,
        advisor.TelemetrySgReason(reason),
        (),
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "policy,reason,status,count,age,gap",
    [
        ({"confirmations": 3}, "INSUFFICIENT_CONFIRMATIONS", "NO_DECISION", 2, 0, None),
        ({"now_ns": exact_ns(BASE)}, "FUTURE", "NO_DECISION", 2, -100, 100),
        ({"now_ns": exact_ns(SECOND) + 200}, "STALE", "NO_DECISION", 2, 200, 100),
        ({"max_gap_ns": 99}, "GAP_EXCEEDED", "NO_DECISION", 2, 0, 100),
        ({"trigger_sg": Decimal("1.019")}, "ABOVE_TRIGGER", "WAIT", 2, 0, 100),
        ({"now_ns": exact_ns(SECOND) + 100}, "AT_OR_BELOW_TRIGGER", "CONDITION_MET", 2, 100, 100),
    ],
)
def test_quality_results_retain_candidates(policy, reason, status, count, age, gap):
    assert explain(**policy) == advisor.TelemetrySgResult(
        advisor.AdvisorStatus(status),
        advisor.TelemetrySgReason(reason),
        (
            advisor.TelemetrySgEvidence(exact_ns(BASE), Decimal("1.02")),
            advisor.TelemetrySgEvidence(exact_ns(SECOND), Decimal("1.02")),
        ),
        count,
        age,
        gap,
    )


@pytest.mark.parametrize("index", [0, 1, 2])
def test_conflicts_at_first_interior_and_latest_discard_everything(index):
    later = replace(SECOND, reading_id="later", observed_at_submicrosecond_ns=200)
    readings = (BASE, SECOND, later)
    conflict = replace(readings[index], reading_id="conflict", gravity_raw=1.01)
    for order in permutations((*readings, conflict)):
        assert explain(order) == advisor.TelemetrySgResult(
            advisor.AdvisorStatus.NO_DECISION,
            advisor.TelemetrySgReason.CONFLICTING_SG,
            (),
            None,
            None,
            None,
        )


@pytest.mark.parametrize(
    "value",
    [
        True,
        "1.02",
        float("nan"),
        float("inf"),
        -float("inf"),
        Decimal("sNaN"),
        0.89,
        1.21,
        10**5000,
    ],
    ids=["bool", "string", "nan", "inf", "negative-inf", "decimal", "low", "high", "huge"],
)
def test_invalid_sg_full_result(value):
    assert explain((BASE, SECOND, replace(BASE, gravity_raw=value))) == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.NO_DECISION,
        advisor.TelemetrySgReason.INVALID_SG,
        (),
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"observed_at": BASE.observed_at.replace(tzinfo=None)},
        {"observed_at": None},
        *[{"observed_at_submicrosecond_ns": value} for value in [True, -100, 1000, 1, 100.0]],
    ],
)
def test_invalid_timestamp_full_result(changes):
    assert explain((BASE, SECOND, replace(BASE, **changes))) == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.NO_DECISION,
        advisor.TelemetrySgReason.INVALID_TIMESTAMP,
        (),
        None,
        None,
        None,
    )


def test_offset_duplicates_and_latest_selection_are_exact():
    offset = replace(
        BASE,
        reading_id="offset",
        observed_at=BASE.observed_at.astimezone(timezone(timedelta(hours=2))),
    )
    older = replace(
        BASE, reading_id="older", observed_at=BASE.observed_at - timedelta(days=1), gravity_raw=1.1
    )
    for order in permutations((SECOND, BASE, offset, older)):
        assert explain(order) == replace(explain(), distinct_observations=3)
    assert explain((BASE, offset)) == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.NO_DECISION,
        advisor.TelemetrySgReason.INSUFFICIENT_CONFIRMATIONS,
        (advisor.TelemetrySgEvidence(exact_ns(BASE), Decimal("1.02")),),
        1,
        100,
        None,
    )


def test_insufficient_candidates_include_signed_age_but_no_gap():
    assert explain((SECOND, BASE, BASE), confirmations=3, now_ns=exact_ns(BASE)) == (
        advisor.TelemetrySgResult(
            advisor.AdvisorStatus.NO_DECISION,
            advisor.TelemetrySgReason.INSUFFICIENT_CONFIRMATIONS,
            explain().evidence,
            2,
            -100,
            None,
        )
    )


def test_gap_equality_and_one_tick_beyond():
    later = replace(SECOND, observed_at_submicrosecond_ns=200)
    result = explain((BASE, later), now_ns=exact_ns(later))
    assert result == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.NO_DECISION,
        advisor.TelemetrySgReason.GAP_EXCEEDED,
        (
            advisor.TelemetrySgEvidence(exact_ns(BASE), Decimal("1.02")),
            advisor.TelemetrySgEvidence(exact_ns(later), Decimal("1.02")),
        ),
        2,
        0,
        200,
    )
    assert explain((BASE, later), now_ns=exact_ns(later), max_gap_ns=200) == replace(
        result,
        status=advisor.AdvisorStatus.CONDITION_MET,
        reason=advisor.TelemetrySgReason.AT_OR_BELOW_TRIGGER,
    )


@pytest.mark.parametrize("precision", [1, 2, 28, 60])
def test_decimal_context_cannot_round_evidence_or_comparison(precision):
    expected = explain()
    with localcontext() as context:
        context.prec = precision
        context.traps[Inexact] = True
        context.traps[Rounded] = True
        assert explain() == expected
        assert explain(trigger_sg=Decimal("1.019999999999999999999999999999")) == replace(
            expected,
            status=advisor.AdvisorStatus.WAIT,
            reason=advisor.TelemetrySgReason.ABOVE_TRIGGER,
        )


def test_result_and_evidence_are_frozen_and_inputs_unchanged():
    readings = (BASE, SECOND)
    before = tuple(replace(item) for item in readings)
    result = explain(readings)
    with pytest.raises(FrozenInstanceError):
        result.status = advisor.AdvisorStatus.WAIT
    with pytest.raises(FrozenInstanceError):
        result.evidence[0].sg = Decimal("1.1")
    assert isinstance(result.evidence, tuple)
    assert readings == before
    assert hash(result) == hash(explain(readings))


def test_signatures_match_and_every_parameter_is_required():
    old = signature(advisor.advise_telemetry_sg)
    new = signature(advisor.explain_telemetry_sg)
    assert old.parameters == new.parameters
    assert all(p.default is p.empty for p in new.parameters.values())
    policy = dict(
        gravity_unit="sg",
        now_ns=0,
        trigger_sg=Decimal("1.02"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    for name in policy:
        with pytest.raises(TypeError):
            advisor.explain_telemetry_sg((), **{k: v for k, v in policy.items() if k != name})


@pytest.mark.parametrize(
    "policy,message",
    [
        ({"trigger_sg": True, "now_ns": True}, "trigger SG"),
        ({"trigger_sg": Decimal("sNaN")}, "trigger SG"),
        ({"now_ns": True, "max_age_ns": 0}, "must be integers"),
        ({"max_age_ns": 0}, "age and gap must be positive"),
    ],
)
def test_policy_errors_precede_unusable_unit_and_collection(policy, message):
    with pytest.raises(ValueError, match=message):
        explain(None, gravity_unit=None, **policy)


def test_first_blocker_traversal_is_not_order_independent():
    missing = replace(BASE, gravity_raw=None)
    malformed = replace(SECOND, source="")
    assert explain((missing, malformed)).reason is advisor.TelemetrySgReason.MISSING_SG
    assert explain((malformed, missing)).reason is advisor.TelemetrySgReason.INVALID_READING
    assert explain(None, gravity_unit=None).reason is advisor.TelemetrySgReason.UNDECLARED_UNIT
    assert explain((BASE, replace(SECOND, gravity_raw=None)), confirmations=5).reason is (
        advisor.TelemetrySgReason.MISSING_SG
    )
    assert explain(now_ns=exact_ns(BASE), max_gap_ns=1).reason is advisor.TelemetrySgReason.FUTURE
    assert explain(now_ns=exact_ns(SECOND) + 200, max_gap_ns=1).reason is (
        advisor.TelemetrySgReason.STALE
    )


def test_wrapper_delegates_once(monkeypatch):
    expected = explain()
    calls = []

    def evaluate(*args, **kwargs):
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(advisor, "explain_telemetry_sg", evaluate)
    assert (
        advisor.advise_telemetry_sg(
            (),
            gravity_unit=None,
            now_ns=0,
            trigger_sg=Decimal("1.02"),
            max_age_ns=100,
            max_gap_ns=100,
            confirmations=2,
        )
        is expected.status
    )
    assert calls == [
        (
            ((),),
            dict(
                gravity_unit=None,
                now_ns=0,
                trigger_sg=Decimal("1.02"),
                max_age_ns=100,
                max_gap_ns=100,
                confirmations=2,
            ),
        )
    ]


@pytest.mark.parametrize("confirmations", [2, 3, 4, 5])
def test_latest_n_candidates_exclude_old_gaps_and_above_threshold_values(confirmations):
    readings = tuple(
        replace(BASE, reading_id=str(i), observed_at_submicrosecond_ns=i * 100) for i in range(6)
    )
    older = replace(
        BASE, reading_id="older", observed_at=BASE.observed_at - timedelta(days=1), gravity_raw=1.1
    )
    result = explain(
        (*reversed(readings), older, BASE),
        confirmations=confirmations,
        now_ns=exact_ns(readings[-1]),
    )
    assert result == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.CONDITION_MET,
        advisor.TelemetrySgReason.AT_OR_BELOW_TRIGGER,
        tuple(
            advisor.TelemetrySgEvidence(exact_ns(item), Decimal("1.02"))
            for item in readings[-confirmations:]
        ),
        7,
        0,
        100,
    )


@pytest.mark.parametrize("sg", [0.9, 1.2])
def test_inclusive_sg_bounds(sg):
    readings = tuple(replace(item, gravity_raw=sg) for item in (BASE, SECOND))
    result = explain(readings, trigger_sg=Decimal(str(sg)))
    assert result.status is advisor.AdvisorStatus.CONDITION_MET
    assert tuple(e.sg for e in result.evidence) == (Decimal(str(sg)),) * 2


@pytest.mark.parametrize(
    "name,value",
    [
        ("trigger_sg", None),
        ("trigger_sg", Decimal("NaN")),
        ("trigger_sg", Decimal("Infinity")),
        ("trigger_sg", Decimal("0.8999")),
        ("trigger_sg", Decimal("1.2001")),
        *[
            (name, value)
            for name in ["now_ns", "max_age_ns", "max_gap_ns", "confirmations"]
            for value in [True, None, 1.0]
        ],
        ("max_age_ns", 0),
        ("max_gap_ns", -1),
        ("confirmations", 1),
        ("confirmations", 6),
    ],
)
def test_both_entrypoints_preserve_policy_exception_messages(name, value):
    policy = dict(
        gravity_unit=None,
        now_ns=0,
        trigger_sg=Decimal("1.02"),
        max_age_ns=100,
        max_gap_ns=100,
        confirmations=2,
    )
    policy[name] = value
    with pytest.raises(ValueError) as old:
        advisor.advise_telemetry_sg(None, **policy)
    with pytest.raises(ValueError) as new:
        advisor.explain_telemetry_sg(None, **policy)
    assert str(old.value) == str(new.value)


@pytest.mark.parametrize(
    "gravities,reason,status",
    [
        ((1020, 1019), "AT_OR_BELOW_TRIGGER", "CONDITION_MET"),
        ((1021, 1019), "ABOVE_TRIGGER", "WAIT"),
        ((1020, None), "MISSING_SG", "NO_DECISION"),
        ((None, None), "MISSING_SG", "NO_DECISION"),
        ((1020,), "INSUFFICIENT_CONFIRMATIONS", "NO_DECISION"),
        ((), "NO_READINGS", "NO_DECISION"),
    ],
)
def test_real_parser_bridge_diagnostic_full_result(gravities, reason, status):
    raw = parse_rapt_telemetry(
        [
            {
                "id": f"00000000-0000-0000-0000-{i + 10:012d}",
                "createdOn": f"2026-01-01T02:00:00.000000{i}+02:00",
                "gravity": gravity,
                "temperature": 20.0,
                "rssi": -60,
                "battery": 80,
            }
            for i, gravity in enumerate(gravities)
        ],
        kind=DeviceKind.HYDROMETER,
        device_id="00000000-0000-0000-0000-000000000001",
    )
    normalized = normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")
    result = explain(normalized)
    assert result.status is advisor.AdvisorStatus(status)
    assert result.reason is advisor.TelemetrySgReason(reason)
    if not gravities or None in gravities:
        assert result.evidence == ()
        assert (
            result.distinct_observations,
            result.latest_age_ns,
            result.largest_confirmation_gap_ns,
        ) == (None, None, None)
    else:
        assert result.evidence == tuple(
            advisor.TelemetrySgEvidence(exact_ns(BASE) + i * 100, Decimal(str(sg)))
            for i, sg in enumerate([item.gravity_raw for item in normalized])
        )
        assert result.distinct_observations == len(gravities)
        assert result.latest_age_ns == (100 if len(gravities) == 1 else 0)
        assert result.largest_confirmation_gap_ns == (None if len(gravities) == 1 else 100)
    assert explain(raw).status is advisor.AdvisorStatus.NO_DECISION


def test_complete_result_at_threshold():
    result = explain()
    assert result == advisor.TelemetrySgResult(
        advisor.AdvisorStatus.CONDITION_MET,
        advisor.TelemetrySgReason.AT_OR_BELOW_TRIGGER,
        (
            advisor.TelemetrySgEvidence(exact_ns(BASE), Decimal("1.02")),
            advisor.TelemetrySgEvidence(exact_ns(SECOND), Decimal("1.02")),
        ),
        2,
        0,
        100,
    )
