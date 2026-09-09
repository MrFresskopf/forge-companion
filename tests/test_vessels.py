import json
from io import BytesIO

import pytest

import forge_companion.vessels as vessel_module
from forge_companion.vessels import (
    MAX_FILE_BYTES,
    VesselBusyError,
    bind_vessel,
    load_vessels,
)

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"
OTHER_PILL = "33333333-3333-4333-8333-333333333333"
OTHER_CONTROLLER = "44444444-4444-4444-8444-444444444444"


def binding():
    return {
        "vessel_id": "tank",
        "hydrometer": PILL,
        "temperature_controller": CONTROLLER,
        "gravity_interpretation": "unknown",
    }


def other_binding():
    return {
        "vessel_id": "other-tank",
        "hydrometer": OTHER_PILL,
        "temperature_controller": OTHER_CONTROLLER,
        "gravity_interpretation": "unknown",
    }


def test_existing_exclusive_lock_rejects_bind_without_changing_files(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(binding())
    path = tmp_path / "vessels.json"
    original = path.read_bytes()
    lock_path = tmp_path / ".vessels.json.lock"
    lock_content = b"owned by another writer"
    lock_path.write_bytes(lock_content)

    with pytest.raises(VesselBusyError, match="busy or locked"):
        bind_vessel(other_binding())

    assert path.read_bytes() == original
    assert lock_path.read_bytes() == lock_content


def test_lock_is_released_after_validation_failure_inside_lock(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(binding())
    path = tmp_path / "vessels.json"
    original = path.read_bytes()

    with pytest.raises(ValueError, match="already bound"):
        bind_vessel(binding())

    assert path.read_bytes() == original
    assert not (tmp_path / ".vessels.json.lock").exists()


def test_lock_is_released_and_config_preserved_when_atomic_write_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    bind_vessel(binding())
    path = tmp_path / "vessels.json"
    original = path.read_bytes()

    def fail_write(*args, **kwargs):
        raise OSError("synthetic write failure")

    monkeypatch.setattr(vessel_module, "atomic_write_text", fail_write)

    with pytest.raises(OSError, match="synthetic write failure"):
        bind_vessel(other_binding())

    assert path.read_bytes() == original
    assert not (tmp_path / ".vessels.json.lock").exists()


def test_lock_is_released_after_successful_bind(tmp_path, monkeypatch):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))

    bind_vessel(binding())

    assert load_vessels() == [binding()]
    assert not (tmp_path / ".vessels.json.lock").exists()


@pytest.mark.parametrize(
    "field,value",
    [
        ("vessel_id", "../outside"),
        ("vessel_id", "a/b"),
        ("vessel_id", "A"),
        ("vessel_id", "x" * 65),
        ("vessel_id", ""),
        ("hydrometer", "not-uuid"),
        ("hydrometer", PILL.replace("-", "")),
        ("hydrometer", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"),
        ("temperature_controller", PILL),
        ("gravity_interpretation", "sg"),
        ("gravity_interpretation", None),
        ("api_secret", "secret-value"),
    ],
)
def test_invalid_binding_never_writes(tmp_path, monkeypatch, field, value):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    item = {**binding(), field: value}
    with pytest.raises(ValueError):
        bind_vessel(item)
    assert not (tmp_path / "vessels.json").exists()


@pytest.mark.parametrize(
    "text",
    [
        "{",
        "[]",
        "null",
        '{"schema_version":"future","vessels":[]}',
        '{"schema_version":"forge-companion-vessels-v1","vessels":[],"password":"x"}',
        '{"schema_version":"forge-companion-vessels-v1","vessels":[],"vessels":[]}',
        '{"schema_version":"forge-companion-vessels-v1","vessels":[{}]}',
        '{"schema_version":"forge-companion-vessels-v1","vessels":null}',
        '{"schema_version":"forge-companion-vessels-v1","vessels":NaN}',
        "[" * 2000,
        " " * 65537,
        json.dumps({"schema_version": "forge-companion-vessels-v1", "vessels": [binding()] * 2}),
        json.dumps(
            {
                "schema_version": "forge-companion-vessels-v1",
                "vessels": [binding(), {**binding(), "vessel_id": "other"}],
            }
        ),
    ],
    ids=[f"malformed-{i}" for i in range(13)],
)
def test_malformed_file_fails_closed_without_replacement(tmp_path, monkeypatch, text):
    monkeypatch.setenv("FORGE_COMPANION_CONFIG_DIR", str(tmp_path))
    path = tmp_path / "vessels.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        load_vessels()
    with pytest.raises(ValueError):
        bind_vessel(binding(), replace=True)
    assert path.read_text(encoding="utf-8") == text


def test_load_vessels_bounds_the_file_read(monkeypatch):
    class TrackedFile(BytesIO):
        def read(self, size=-1):
            assert size == MAX_FILE_BYTES + 1
            return super().read(size)

    class Source:
        def open(self, mode):
            assert mode == "rb"
            return TrackedFile(b" " * (MAX_FILE_BYTES + 1))

    monkeypatch.setattr(vessel_module, "vessels_path", lambda: Source())

    with pytest.raises(ValueError, match="too large"):
        load_vessels()
