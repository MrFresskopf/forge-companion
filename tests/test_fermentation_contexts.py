import json
from io import BytesIO

import pytest

import forge_companion.fermentation_contexts as context_module
from forge_companion.fermentation_contexts import (
    MAX_CONTEXT_FILE_BYTES,
    MAX_PHASE_EVENTS,
    FermentationContextBusyError,
    append_fermentation_phase,
    close_fermentation_context,
    context_phase_history,
    load_fermentation_contexts,
    start_fermentation_context,
)
from forge_companion.vessels import bind_vessel

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
MULTILINE_CHARACTERS = (
    "\n",
    "\r",
    "\v",
    "\f",
    "\x1c",
    "\x1d",
    "\x1e",
    "\x85",
    "\u2028",
    "\u2029",
)


def _binding(hydrometer=PILL, controller=CONTROLLER):
    return {
        "vessel_id": "tank",
        "hydrometer": hydrometer,
        "temperature_controller": controller,
        "gravity_interpretation": "unknown",
    }


def _start(**overrides):
    values = {
        "vessel_id": "tank",
        "batch_display_name": "Test Batch",
        "original_gravity_sg": "1.077",
        "expected_final_gravity_sg": "1.015",
        "yeast": "US-05",
        "fermentation_start_date": "2026-08-29",
        "authoritative_temperature_role": "hydrometer",
    }
    values.update(overrides)
    return start_fermentation_context(**values)


def test_start_snapshots_binding_and_marks_date_boundary_inexact(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())

    created = _start()

    assert created == load_fermentation_contexts()[0]
    assert created["context_id"]
    assert created["status"] == "active"
    assert created["source_devices"] == {
        "hydrometer": PILL,
        "temperature_controller": CONTROLLER,
    }
    assert created["fermentation_start_date"] == "2026-08-29"
    assert created["start_instant"] is None
    assert created["same_day_telemetry_attribution"] == "unavailable"
    assert created["original_gravity_sg"] == "1.077"
    assert not (tmp_path / ".fermentation-contexts.json.lock").exists()


def test_start_refuses_silent_overwrite_but_switch_closes_previous(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    first = _start()
    original = (tmp_path / "fermentation-contexts.json").read_bytes()

    with pytest.raises(ValueError, match="already has an active"):
        _start(batch_display_name="Second")
    assert (tmp_path / "fermentation-contexts.json").read_bytes() == original

    second = _start(batch_display_name="Second", switch=True)
    contexts = load_fermentation_contexts()
    assert [item["context_id"] for item in contexts] == [first["context_id"], second["context_id"]]
    assert [item["status"] for item in contexts] == ["closed", "active"]


def test_close_preserves_context_and_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    created = _start()

    closed = close_fermentation_context("tank")

    assert closed["context_id"] == created["context_id"]
    assert closed["status"] == "closed"
    assert load_fermentation_contexts() == [closed]
    with pytest.raises(ValueError, match="no active"):
        close_fermentation_context("tank")


@pytest.mark.parametrize(
    "field,value",
    [
        ("original_gravity_sg", ".1016"),
        ("original_gravity_sg", "1.2"),
        ("original_gravity_sg", "NaN"),
        ("original_gravity_sg", True),
        ("original_gravity_sg", "0.999"),
        ("original_gravity_sg", "1.201"),
        ("expected_final_gravity_sg", "1.077"),
        ("expected_final_gravity_sg", "1.078"),
        ("fermentation_start_date", "2026-8-29"),
        ("fermentation_start_date", "2026-02-30"),
        ("fermentation_start_date", "2026-08-29T00:00:00Z"),
        ("batch_display_name", ""),
        ("batch_display_name", " x"),
        ("batch_display_name", "x\nprivate"),
        ("batch_display_name", "x" * 121),
        ("yeast", "x" * 121),
        ("authoritative_temperature_role", "either"),
        ("vessel_id", "../escape"),
    ],
)
def test_invalid_start_never_writes(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    with pytest.raises((TypeError, ValueError)):
        _start(**{field: value})
    assert not (tmp_path / "fermentation-contexts.json").exists()


@pytest.mark.parametrize("field", ["batch_display_name", "yeast"])
@pytest.mark.parametrize("separator", MULTILINE_CHARACTERS)
def test_start_rejects_multiline_characters(tmp_path, monkeypatch, field, separator):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())

    with pytest.raises(ValueError):
        _start(**{field: f"visible{separator}injected"})

    assert not (tmp_path / "fermentation-contexts.json").exists()


@pytest.mark.parametrize("field", ["batch_display_name", "yeast"])
@pytest.mark.parametrize("separator", MULTILINE_CHARACTERS)
def test_persisted_display_fields_reject_multiline_characters(
    tmp_path, monkeypatch, field, separator
):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["contexts"][0][field] = f"visible{separator}injected"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(ValueError):
        load_fermentation_contexts()


def test_start_rejects_identical_source_device_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(context_module, "load_vessels", lambda: [_binding(controller=PILL)])

    with pytest.raises(ValueError, match="distinct"):
        _start()

    assert not (tmp_path / "fermentation-contexts.json").exists()


def test_persisted_context_rejects_identical_source_device_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["contexts"][0]["source_devices"]["temperature_controller"] = PILL
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="distinct"):
        load_fermentation_contexts()


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        '{"schema_version":"future","contexts":[]}',
        '{"schema_version":"forge-companion-fermentation-contexts-v1","contexts":[],"x":1}',
        '{"schema_version":"forge-companion-fermentation-contexts-v1","contexts":[],"contexts":[]}',
        '{"schema_version":"forge-companion-fermentation-contexts-v1","contexts":NaN}',
        "[" * 2000,
        " " * (256 * 1024 + 1),
    ],
    ids=[f"malformed-{i}" for i in range(8)],
)
def test_malformed_context_file_fails_closed_without_replacement(tmp_path, monkeypatch, text):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    path = tmp_path / "fermentation-contexts.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_fermentation_contexts()
    with pytest.raises(ValueError):
        _start()
    assert path.read_text(encoding="utf-8") == text


def test_context_read_is_bounded(monkeypatch):
    class TrackedFile(BytesIO):
        def read(self, size=-1):
            assert size == MAX_CONTEXT_FILE_BYTES + 1
            return super().read(size)

    class Source:
        def open(self, mode):
            assert mode == "rb"
            return TrackedFile(b" " * (MAX_CONTEXT_FILE_BYTES + 1))

    monkeypatch.setattr(context_module, "fermentation_contexts_path", lambda: Source())
    with pytest.raises(ValueError, match="too large"):
        load_fermentation_contexts()


def test_lock_cleanup_and_atomic_failure_preserve_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    original = path.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr(context_module, "atomic_write_text", fail_write)
    with pytest.raises(OSError):
        close_fermentation_context("tank")
    assert path.read_bytes() == original
    assert not (tmp_path / ".fermentation-contexts.json.lock").exists()

    lock = tmp_path / ".fermentation-contexts.json.lock"
    lock.write_text("other", encoding="utf-8")
    with pytest.raises(FermentationContextBusyError):
        close_fermentation_context("tank")
    assert lock.read_text(encoding="utf-8") == "other"


def test_v1_context_without_phases_has_derived_date_only_fermentation_event(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    created = _start()
    path = tmp_path / "fermentation-contexts.json"
    original = path.read_bytes()

    loaded = load_fermentation_contexts()[0]

    assert "phase_history" not in loaded
    assert context_phase_history(loaded) == [{"phase": "fermentation", "start_date": "2026-08-29"}]
    assert created["context_id"] == loaded["context_id"]
    assert path.read_bytes() == original


def test_append_phase_preserves_order_and_earlier_history(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    created = _start()

    updated = append_fermentation_phase(
        vessel_id="tank", phase="cold-crash", start_date="2026-09-06"
    )

    assert updated["context_id"] == created["context_id"]
    assert context_phase_history(updated) == [
        {"phase": "fermentation", "start_date": "2026-08-29"},
        {"phase": "cold-crash", "start_date": "2026-09-06"},
    ]
    assert load_fermentation_contexts() == [updated]


@pytest.mark.parametrize(
    "phase,start_date,message",
    [
        ("conditioning", "2026-09-06", "Phase"),
        ("cold-crash", "2026-08-28", "after"),
        ("cold-crash", "2026-08-29", "same day"),
        ("cold-crash", "9999-12-31", "future"),
    ],
)
def test_append_phase_rejects_invalid_enum_order_and_future(
    tmp_path, monkeypatch, phase, start_date, message
):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    original = path.read_bytes()

    with pytest.raises(ValueError, match=message):
        append_fermentation_phase(vessel_id="tank", phase=phase, start_date=start_date)

    assert path.read_bytes() == original
    assert not (tmp_path / ".fermentation-contexts.json.lock").exists()


def test_append_rejects_noop_and_requires_active_context(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    with pytest.raises(ValueError, match="no-op"):
        append_fermentation_phase(vessel_id="tank", phase="fermentation", start_date="2026-09-01")
    append_fermentation_phase(vessel_id="tank", phase="cold-crash", start_date="2026-09-06")
    with pytest.raises(ValueError, match="no-op"):
        append_fermentation_phase(vessel_id="tank", phase="cold-crash", start_date="2026-09-07")
    with pytest.raises(ValueError, match="Reverse"):
        append_fermentation_phase(vessel_id="tank", phase="fermentation", start_date="2026-09-08")
    close_fermentation_context("tank")
    with pytest.raises(ValueError, match="no active"):
        append_fermentation_phase(vessel_id="tank", phase="cold-crash", start_date="2026-09-06")


def test_phase_history_does_not_bleed_across_close_and_switch(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    first = _start()
    append_fermentation_phase(vessel_id="tank", phase="cold-crash", start_date="2026-09-06")
    second = _start(batch_display_name="Second", fermentation_start_date="2026-09-07", switch=True)
    contexts = load_fermentation_contexts()

    assert contexts[0]["context_id"] == first["context_id"]
    assert len(context_phase_history(contexts[0])) == 2
    assert contexts[1]["context_id"] == second["context_id"]
    assert context_phase_history(contexts[1]) == [
        {"phase": "fermentation", "start_date": "2026-09-07"}
    ]


@pytest.mark.parametrize(
    "phase_history",
    [
        {},
        [{"phase": "warm", "start_date": "2026-09-06"}],
        [{"phase": "cold-crash", "start_date": "bad"}],
        [{"phase": "cold-crash", "start_date": "2026-09-06", "extra": True}],
        [1],
    ],
)
def test_malformed_persisted_phase_history_fails_closed(tmp_path, monkeypatch, phase_history):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["contexts"][0]["phase_history"] = phase_history
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError):
        load_fermentation_contexts()


def test_persisted_phase_history_rejects_duplicate_key_and_bound(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        '"status": "active"',
        '"phase_history": [{"phase":"cold-crash","phase":"cold-crash",'
        '"start_date":"2026-09-06"}], "status": "active"',
    )
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate"):
        load_fermentation_contexts()

    payload = json.loads(
        text.replace('"phase":"cold-crash","phase":"cold-crash",', '"phase":"cold-crash",')
    )
    payload["contexts"][0]["phase_history"] = [
        {"phase": "cold-crash", "start_date": "2026-09-06"}
    ] * (MAX_PHASE_EVENTS + 1)
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="too many"):
        load_fermentation_contexts()


def test_phase_append_atomic_failure_cleans_lock_and_preserves_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(_binding())
    _start()
    path = tmp_path / "fermentation-contexts.json"
    original = path.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("synthetic")

    monkeypatch.setattr(context_module, "atomic_write_text", fail_write)
    with pytest.raises(OSError):
        append_fermentation_phase(vessel_id="tank", phase="cold-crash", start_date="2026-09-06")
    assert path.read_bytes() == original
    assert not (tmp_path / ".fermentation-contexts.json.lock").exists()
