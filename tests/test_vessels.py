import json
from io import BytesIO

import pytest

import forge_companion.vessels as vessel_module
from forge_companion.vessels import MAX_FILE_BYTES, bind_vessel, load_vessels

PILL = "11111111-1111-4111-8111-111111111111"
CONTROLLER = "22222222-2222-4222-8222-222222222222"


def binding():
    return {
        "vessel_id": "tank",
        "hydrometer": PILL,
        "temperature_controller": CONTROLLER,
        "gravity_interpretation": "unknown",
    }


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
