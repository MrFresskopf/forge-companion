import json
from pathlib import Path

import pytest

from forge_companion.preferences import (
    HOPPER_QUALIFICATION_STATEMENT_VERSION,
    Preferences,
    PreferencesError,
    hopper_qualification_is_current,
    load_preferences,
    save_preferences,
)


def test_preferences_round_trip_in_explicit_config_directory(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    save_preferences(Preferences(temperature_unit="C"))

    assert load_preferences() == Preferences(temperature_unit="C")
    content = (tmp_path / "preferences.json").read_text(encoding="utf-8")
    assert "token" not in content.lower()


def test_preferences_load_legacy_temperature_only_document(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    (tmp_path / "preferences.json").write_text('{"temperature_unit":"F"}\n', encoding="utf-8")

    assert load_preferences() == Preferences(temperature_unit="F")


def test_preferences_round_trip_current_hopper_attestation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    expected = Preferences(
        temperature_unit="C",
        hopper_qualification_statement_version=HOPPER_QUALIFICATION_STATEMENT_VERSION,
        hopper_qualification_attested_at="2026-08-02T18:00:00+00:00",
    )

    save_preferences(expected)

    loaded = load_preferences()
    assert loaded == expected
    assert hopper_qualification_is_current(loaded)


@pytest.mark.parametrize(
    "qualification",
    [
        {"hopper_qualification_statement_version": 1},
        {"hopper_qualification_attested_at": "2026-08-02T18:00:00+00:00"},
        {
            "hopper_qualification_statement_version": 1,
            "hopper_qualification_attested_at": "2026-08-02T18:00:00",
        },
    ],
)
def test_preferences_reject_inconsistent_hopper_attestation(
    qualification: dict[str, object],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    payload: dict[str, object] = {"temperature_unit": None}
    payload.update(qualification)
    (tmp_path / "preferences.json").write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(PreferencesError, match="Stored hopper qualification is invalid"):
        load_preferences()


def test_future_hopper_statement_version_is_not_current(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    future = Preferences(
        hopper_qualification_statement_version=HOPPER_QUALIFICATION_STATEMENT_VERSION + 1,
        hopper_qualification_attested_at="2026-08-02T18:00:00+00:00",
    )

    save_preferences(future)

    assert not hopper_qualification_is_current(load_preferences())


def test_one_second_hopper_statement_version_is_not_current() -> None:
    previous = Preferences(
        hopper_qualification_statement_version=1,
        hopper_qualification_attested_at="2026-08-02T18:00:00+00:00",
    )

    assert HOPPER_QUALIFICATION_STATEMENT_VERSION == 2
    assert not hopper_qualification_is_current(previous)
