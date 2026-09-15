"""Tests for the BrewForge-backed fermentation-context start primitives."""

import json

import pytest

from forge_companion.fermentation_contexts import (
    MAX_CONTEXTS,
    FermentationContextValidationError,
    load_fermentation_contexts,
    preflight_context_start,
    start_brewforge_fermentation_context,
    start_fermentation_context,
    validate_context_input_fields,
)
from forge_companion.vessels import bind_vessel

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
BREW_ID = "54d34560-f1af-49f0-9a26-6caca3397f75"


def _binding(vessel_id="tank"):
    return {
        "vessel_id": vessel_id,
        "hydrometer": PILL,
        "temperature_controller": CONTROLLER,
        "gravity_interpretation": "unknown",
    }


def _brewforge_start(**overrides):
    values = {
        "vessel_id": "tank",
        "brewforge_brew_id": BREW_ID,
        "batch_display_name": "From BrewForge",
        "original_gravity_sg": "1.077",
        "expected_final_gravity_sg": "1.015",
        "yeast": "US-05",
        "fermentation_start_date": "2026-08-29",
        "authoritative_temperature_role": "hydrometer",
    }
    values.update(overrides)
    return start_brewforge_fermentation_context(**values)


def test_validate_context_input_fields_validates_only_provided_fields():
    assert validate_context_input_fields(yeast="US-05") == {"yeast": "US-05"}
    assert validate_context_input_fields() == {}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_display_name", "  spaced  "),
        ("original_gravity_sg", ".077"),
        ("expected_final_gravity_sg", "1.0150"),
        ("fermentation_start_date", "2026-8-9"),
    ],
    ids=["batch", "og", "fg", "date"],
)
def test_validate_context_input_fields_rejects_invalid_overrides(field, value):
    with pytest.raises(FermentationContextValidationError):
        validate_context_input_fields(**{field: value})


def test_validate_context_input_fields_checks_og_against_fg_when_both_given():
    with pytest.raises(FermentationContextValidationError):
        validate_context_input_fields(
            original_gravity_sg="1.010", expected_final_gravity_sg="1.020"
        )


def test_preflight_requires_bound_vessel(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    with pytest.raises(FermentationContextValidationError):
        preflight_context_start("tank", switch=False, contexts=[], bindings=[])


def test_preflight_refuses_active_context_without_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    start_fermentation_context(
        vessel_id="tank",
        batch_display_name="First",
        original_gravity_sg="1.077",
        expected_final_gravity_sg="1.015",
        yeast="US-05",
        fermentation_start_date="2026-08-29",
        authoritative_temperature_role="hydrometer",
    )
    contexts = load_fermentation_contexts()
    bindings = [_binding()]

    with pytest.raises(FermentationContextValidationError):
        preflight_context_start("tank", switch=False, contexts=contexts, bindings=bindings)

    preflight_context_start("tank", switch=True, contexts=contexts, bindings=bindings)


def test_preflight_enforces_capacity(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    contexts = [
        {"vessel_id": "other", "status": "closed"} for _ in range(MAX_CONTEXTS)
    ]

    with pytest.raises(FermentationContextValidationError):
        preflight_context_start("tank", switch=False, contexts=contexts, bindings=[_binding()])


def test_brewforge_start_persists_canonical_brew_id_and_snapshots_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())

    item = _brewforge_start()

    assert item["brewforge_brew_id"] == BREW_ID
    assert item["status"] == "active"
    assert item["source_devices"] == {"hydrometer": PILL, "temperature_controller": CONTROLLER}
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    assert stored["contexts"][0]["brewforge_brew_id"] == BREW_ID


def test_brewforge_start_switch_closes_previous_active(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _brewforge_start(batch_display_name="First")

    second = _brewforge_start(batch_display_name="Second", switch=True)

    contexts = load_fermentation_contexts()
    statuses = {item["batch_display_name"]: item["status"] for item in contexts}
    assert statuses == {"First": "closed", "Second": "active"}
    assert second["status"] == "active"


def test_brewforge_start_rejects_non_uuid_brew_id(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())

    with pytest.raises(FermentationContextValidationError):
        _brewforge_start(brewforge_brew_id="not-a-uuid")


def test_brewforge_start_requires_bound_vessel(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    with pytest.raises(FermentationContextValidationError):
        _brewforge_start()


def test_manual_start_records_no_brewforge_id_and_stays_valid(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())

    item = start_fermentation_context(
        vessel_id="tank",
        batch_display_name="Manual",
        original_gravity_sg="1.077",
        expected_final_gravity_sg="1.015",
        yeast="US-05",
        fermentation_start_date="2026-08-29",
        authoritative_temperature_role="hydrometer",
    )

    assert "brewforge_brew_id" not in item
    stored = json.loads((tmp_path / "fermentation-contexts.json").read_text())
    assert "brewforge_brew_id" not in stored["contexts"][0]
    # The legacy-shaped store still loads.
    assert load_fermentation_contexts()[0]["batch_display_name"] == "Manual"
