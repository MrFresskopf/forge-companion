import ast
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from forge_companion.brewforge_telemetry import adapt_brewforge_readings
from forge_companion.local_outliers import LocalOutlierValidationError, evaluate_local_outliers
from forge_companion.rapt_sg import normalize_rapt_sg
from forge_companion.telemetry import DeviceKind, parse_rapt_telemetry

BREW = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
START = 1_784_246_400_000_000_000


def _readings(*values: float):
    return adapt_brewforge_readings(
        {
            "data": [
                {
                    "id": f"reading-{index}",
                    "timestamp": (
                        datetime(2026, 7, 17, tzinfo=UTC) + timedelta(seconds=index)
                    ).isoformat(),
                    "gravity": value,
                    "temperature": 20.0,
                }
                for index, value in enumerate(values)
            ]
        },
        brew_id=BREW,
        gravity_unit="sg",
        temperature_unit="c",
    )


def test_interior_sg_spike_has_immutable_local_evidence() -> None:
    readings = _readings(1.000, 1.100, 1.020)
    result = evaluate_local_outliers(
        readings,
        gravity_unit="sg",
        temperature_unit="c",
        gravity_threshold="0.01",
        temperature_threshold="0.5",
        window_start_ns=START,
        window_end_ns=START + 2_000_000_000,
        max_neighbor_gap_ns=1_000_000_000,
    )

    assert result.readings is readings
    assert len(result.assessed_observations) == 2
    finding = result.findings[0]
    assert finding.metric == "gravity-sg"
    assert finding.observation.reading_id == "reading-1"
    assert finding.left_observation.reading_id == "reading-0"
    assert finding.right_observation.reading_id == "reading-2"
    assert finding.expected_value == Decimal("1.010")
    assert finding.residual == Decimal("0.090")
    assert finding.threshold == Decimal("0.01")
    assert replace(finding, residual=Decimal("0")) != finding


def _evaluate(readings, **overrides):
    policy = {
        "gravity_unit": "sg",
        "temperature_unit": "c",
        "gravity_threshold": "0.01",
        "temperature_threshold": "0.5",
        "window_start_ns": START,
        "window_end_ns": START + 2_000_000_000,
        "max_neighbor_gap_ns": 1_000_000_000,
    }
    policy.update(overrides)
    return evaluate_local_outliers(readings, **policy)


def test_linear_trends_are_assessed_without_findings() -> None:
    readings = _readings(1.000, 1.010, 1.020)
    readings = tuple(
        replace(item, temperature_c=20.0 + index) for index, item in enumerate(readings)
    )
    result = _evaluate(readings)
    assert len(result.assessed_observations) == 2
    assert result.findings == ()


def test_interior_c_temperature_spike_is_independent_evidence() -> None:
    readings = tuple(
        replace(item, temperature_c=temperature)
        for item, temperature in zip(_readings(1.0, 1.01, 1.02), (20.0, 24.0, 22.0), strict=True)
    )
    result = _evaluate(readings, gravity_threshold="1", temperature_threshold="0.5")
    assert [item.metric for item in result.findings] == ["temperature-c"]
    assert result.findings[0].residual == Decimal("3.0")


@pytest.mark.parametrize(("value", "findings"), [(1.020, 0), (1.0401, 1)])
def test_threshold_is_strictly_exceeded(value: float, findings: int) -> None:
    assert (
        len(_evaluate(_readings(1.0, value, 1.04), gravity_threshold="0.02").findings) == findings
    )


def test_exact_100ns_interpolation_is_preserved() -> None:
    readings = tuple(
        replace(
            item,
            observed_at=datetime(2026, 7, 17, tzinfo=UTC),
            observed_at_submicrosecond_ns=remainder,
        )
        for item, remainder in zip(_readings(1.0, 1.011, 1.02), (0, 100, 200), strict=True)
    )
    result = _evaluate(readings, gravity_threshold="0.0001", max_neighbor_gap_ns=100)
    finding = result.findings[0]
    assert finding.expected_value == Decimal("1.010")
    assert finding.residual == Decimal("0.001")
    assert finding.observation.observed_at_ns - finding.left_observation.observed_at_ns == 100


def test_endpoints_gaps_and_missing_metric_are_not_assessed() -> None:
    readings = _readings(1.0, 1.1, 1.02)
    assert _evaluate(readings, max_neighbor_gap_ns=999_999_999).assessed_observations == ()
    without_middle_sg = tuple(
        replace(item, gravity_raw=None) if index == 1 else item
        for index, item in enumerate(readings)
    )
    assert _evaluate(without_middle_sg).assessed_observations[0].metric == "temperature-c"


def test_evaluation_preserves_every_input_reading() -> None:
    readings = _readings(1.0, 1.1, 1.02)
    before = tuple(asdict(item) for item in readings)
    _evaluate(readings)
    assert tuple(asdict(item) for item in readings) == before


def test_brewforge_and_normalized_rapt_sg_are_compatible_without_declaring_rapt_temperature() -> (
    None
):
    brewforge = _readings(1.0, 1.1, 1.02)
    raw = parse_rapt_telemetry(
        [
            {
                "id": f"00000000-0000-0000-0000-{index + 10:012d}",
                "createdOn": (
                    datetime(2026, 7, 17, tzinfo=UTC) + timedelta(seconds=index)
                ).isoformat(),
                "temperature": 20.0,
                "gravity": gravity * 1000,
                "battery": 90.0,
                "rssi": -50.0,
            }
            for index, gravity in enumerate((1.0, 1.1, 1.02))
        ],
        kind=DeviceKind.HYDROMETER,
        device_id="00000000-0000-0000-0000-000000000001",
    )
    normalized = normalize_rapt_sg(raw, gravity_interpretation="sg-times-1000")
    brewforge_result = _evaluate(brewforge, temperature_unit=None, temperature_threshold=None)
    rapt_result = _evaluate(normalized, temperature_unit=None, temperature_threshold=None)
    assert [item.residual for item in brewforge_result.findings] == [
        item.residual for item in rapt_result.findings
    ]
    with pytest.raises(LocalOutlierValidationError):
        _evaluate(normalized)


@pytest.mark.parametrize(
    "changed",
    [
        {"source": " rapt"},
        {"device_id": "not-a-uuid"},
        {"reading_id": " bad "},
        {"observed_at": datetime(2026, 7, 17)},
        {"observed_at_submicrosecond_ns": 101},
        {"gravity_raw": float("nan")},
        {"temperature_c": float("inf")},
        {"gravity_unit": None},
        {"temperature_unit": "f"},
    ],
)
def test_invalid_reading_fails_atomically(changed: dict[str, object]) -> None:
    readings = _readings(1.0, 1.01, 1.02)
    with pytest.raises(LocalOutlierValidationError):
        _evaluate((readings[0], replace(readings[1], **changed), readings[2]))


@pytest.mark.parametrize(
    ("readings", "overrides"),
    [
        (_readings(1.0, 1.01, 1.02) + _readings(1.0, 1.01, 1.02)[:1], {}),
        (_readings(1.0, 1.01, 1.02), {"window_end_ns": START + 1}),
        (_readings(1.0, 1.01, 1.02), {"gravity_threshold": "NaN"}),
        (_readings(1.0, 1.01, 1.02), {"gravity_threshold": "-0.1"}),
        (_readings(1.0, 1.01, 1.02), {"max_neighbor_gap_ns": -1}),
    ],
)
def test_invalid_collections_windows_and_policy_fail_atomically(readings, overrides) -> None:
    with pytest.raises(LocalOutlierValidationError):
        _evaluate(readings, **overrides)


@pytest.mark.parametrize(
    "changed",
    [
        {"device_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"},
        {"reading_id": "reading-0"},
        {"reading_id": "other", "observed_at": datetime(2026, 7, 17, tzinfo=UTC)},
    ],
)
def test_mixed_stream_duplicate_id_or_duplicate_exact_instant_is_rejected(
    changed: dict[str, object],
) -> None:
    readings = _readings(1.0, 1.01, 1.02)
    with pytest.raises(LocalOutlierValidationError):
        _evaluate((readings[0], replace(readings[1], **changed), readings[2]))


def test_public_api_has_no_harmful_claims_or_io_imports() -> None:
    source = (Path(__file__).parents[1] / "src/forge_companion/local_outliers.py").read_text(
        encoding="utf-8"
    )
    assert "not validation" in source
    assert "sensor fault" in source
    assert "completion, packaging, safety, or actuation" in source
    imports = [
        node.names[0].name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(ast.parse(source))
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(
        name is None
        or not any(term in name for term in ("client", "rapt", "config", "clock", "persistence"))
        for name in imports
    )
