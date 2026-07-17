from datetime import UTC, datetime, timedelta

from forge_companion.spunding_advisor import (
    AdvisorEvidence,
    AdvisorResult,
    AdvisorStatus,
)
from forge_companion.spunding_report import render_spunding_advice


def test_render_spunding_advice_shows_evidence_and_safety_boundaries() -> None:
    result = AdvisorResult(
        status=AdvisorStatus.CONDITION_MET,
        reason="all confirmation readings are at or below trigger SG",
        trigger_sg=1.012,
        evidence=(
            AdvisorEvidence(
                reading_id="r1",
                timestamp=datetime(2026, 7, 17, 8, tzinfo=UTC),
                gravity=1.0119,
            ),
            AdvisorEvidence(
                reading_id="r2",
                timestamp=datetime(2026, 7, 17, 9, tzinfo=UTC),
                gravity=1.0117,
            ),
        ),
        latest_age=timedelta(minutes=42),
        largest_confirmation_gap=timedelta(hours=1),
        gravity_slope_per_day=-0.006,
        trend_note="least-squares slope over the latest 24 hours",
    )

    report = render_spunding_advice(result)

    assert report.startswith("Spunding advisor: CONDITION_MET\n")
    assert "Reason: all confirmation readings are at or below trigger SG" in report
    assert "Simulation only: no device command was sent." in report
    assert "does not verify pressure, valve position, regulator, or PRV safety." in report
    assert "Trigger SG: 1.0120" in report
    assert "Latest reading age: 42m" in report
    assert "Largest confirmation gap: 1h 0m" in report
    assert "Trend: -0.0060 SG/day (descriptive only)" in report
    assert "r1 | 2026-07-17T08:00:00+00:00 | 1.0119 SG" in report


def test_render_spunding_advice_sanitizes_untrusted_reading_ids() -> None:
    result = AdvisorResult(
        status=AdvisorStatus.WAIT,
        reason="not all confirmation readings are at or below trigger SG",
        trigger_sg=1.012,
        evidence=(
            AdvisorEvidence(
                reading_id="r1\n\x1b[31mFORGED",
                timestamp=datetime(2026, 7, 17, 9, tzinfo=UTC),
                gravity=1.013,
            ),
        ),
        latest_age=timedelta(minutes=5),
        largest_confirmation_gap=timedelta(0),
        gravity_slope_per_day=None,
        trend_note="insufficient recent data for a 24-hour slope",
    )

    report = render_spunding_advice(result)

    assert "\x1b" not in report
    assert "r1 FORGED |" in report
    assert "\nFORGED" not in report
