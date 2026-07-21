import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest

import forge_companion.backup as backup
from forge_companion.backup import create_backup, write_backup


class StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def get(self, path: str, params: object = None) -> dict[str, object]:
        self.calls.append((path, params))
        page = params["page"] if isinstance(params, dict) else 1
        if path == "brews" and page == 1:
            return {
                "data": [{"id": "brew-1"}],
                "pagination": {"hasMore": True, "total": 2},
            }
        if path == "brews" and page == 2:
            return {
                "data": [{"id": "brew-2"}],
                "pagination": {"hasMore": False, "total": 2},
            }
        return {"data": [], "pagination": {"hasMore": False, "total": 0}}


def test_create_backup_collects_every_page_and_supported_resource() -> None:
    client = StubClient()
    now = datetime(2026, 7, 17, 12, 30, tzinfo=UTC)

    result = create_backup(client, now=now)

    assert result["format"] == "forge-companion-collection-snapshot-v2"
    assert result["created_at"] == "2026-07-17T12:30:00+00:00"
    assert result["resources"]["brews"] == [{"id": "brew-1"}, {"id": "brew-2"}]
    assert set(result["resources"]) == {
        "brews",
        "inventory_fermentables",
        "inventory_hops",
        "inventory_yeasts",
        "inventory_miscs",
        "profiles_equipment",
        "profiles_styles",
    }
    assert result["manifest"] == {
        "generator": {"name": "forge-companion", "version": "0.1.1"},
        "collections": {
            "brews": 2,
            "inventory_fermentables": 0,
            "inventory_hops": 0,
            "inventory_yeasts": 0,
            "inventory_miscs": 0,
            "profiles_equipment": 0,
            "profiles_styles": 0,
        },
        "excluded": [
            "brew_details",
            "brew_notes",
            "brew_readings",
            "undocumented_resources",
        ],
        "integrity": {
            "algorithm": "sha256",
            "canonicalization": "json-sort-keys-compact-utf8-without-digest",
            "digest": result["manifest"]["integrity"]["digest"],
        },
    }
    unsigned = deepcopy(result)
    digest = unsigned["manifest"]["integrity"].pop("digest")
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert digest == sha256(canonical).hexdigest()
    assert client.calls[:2] == [
        ("brews", {"page": 1, "limit": 100}),
        ("brews", {"page": 2, "limit": 100}),
    ]


def test_validate_backup_returns_data_free_summary_for_generated_snapshot() -> None:
    payload = create_backup(
        StubClient(),
        now=datetime(2026, 7, 17, 12, 30, tzinfo=UTC),
    )

    summary = backup.validate_backup(payload)

    assert summary.format == "forge-companion-collection-snapshot-v2"
    assert summary.created_at == "2026-07-17T12:30:00+00:00"
    assert summary.generator_version == "0.1.1"
    assert summary.collection_count == 7
    assert summary.record_count == 2
    assert summary.digest == payload["manifest"]["integrity"]["digest"]


def test_create_backup_rejects_naive_creation_time() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        create_backup(StubClient(), now=datetime(2026, 7, 17, 12, 30))


def test_create_backup_normalizes_aware_creation_time_to_utc() -> None:
    payload = create_backup(
        StubClient(),
        now=datetime(2026, 7, 17, 14, 30, tzinfo=timezone(timedelta(hours=2))),
    )

    assert payload["created_at"] == "2026-07-17T12:30:00+00:00"
    backup.validate_backup(payload)


def test_validate_backup_rejects_tampered_resource_data() -> None:
    payload = create_backup(StubClient())
    payload["resources"]["brews"][0]["id"] = "tampered"

    with pytest.raises(ValueError, match="integrity check failed"):
        backup.validate_backup(payload)


def test_validate_backup_rejects_resigned_unsupported_format() -> None:
    payload = create_backup(StubClient())
    payload["format"] = "forge-companion-collection-snapshot-v1"
    payload["manifest"]["integrity"]["digest"] = backup._snapshot_digest(payload)

    with pytest.raises(backup.SnapshotValidationError, match="unsupported format"):
        backup.validate_backup(payload)


@pytest.mark.parametrize(
    "case",
    [
        "wrong-count",
        "missing-collection",
        "non-object-record",
        "wrong-generator",
        "empty-generator-version",
        "wrong-exclusions",
        "wrong-algorithm",
        "wrong-canonicalization",
        "naive-created-at",
        "extra-top-level-field",
    ],
)
def test_validate_backup_rejects_resigned_schema_violation(case: str) -> None:
    payload = create_backup(StubClient())
    if case == "wrong-count":
        payload["manifest"]["collections"]["brews"] = 3
    elif case == "missing-collection":
        payload["resources"].pop("inventory_hops")
        payload["manifest"]["collections"].pop("inventory_hops")
    elif case == "non-object-record":
        payload["resources"]["brews"][0] = "not-an-object"
    elif case == "wrong-generator":
        payload["manifest"]["generator"]["name"] = "other-tool"
    elif case == "empty-generator-version":
        payload["manifest"]["generator"]["version"] = ""
    elif case == "wrong-exclusions":
        payload["manifest"]["excluded"] = []
    elif case == "wrong-algorithm":
        payload["manifest"]["integrity"]["algorithm"] = "md5"
    elif case == "wrong-canonicalization":
        payload["manifest"]["integrity"]["canonicalization"] = "unspecified"
    elif case == "naive-created-at":
        payload["created_at"] = "2026-07-17T12:30:00"
    elif case == "extra-top-level-field":
        payload["unexpected"] = True
    payload["manifest"]["integrity"]["digest"] = backup._snapshot_digest(payload)

    with pytest.raises(backup.SnapshotValidationError, match="schema validation failed"):
        backup.validate_backup(payload)


def test_validate_backup_file_accepts_atomic_generated_snapshot(tmp_path: object) -> None:
    from pathlib import Path

    destination = Path(str(tmp_path)) / "snapshot.json"
    write_backup(create_backup(StubClient()), destination)

    summary = backup.validate_backup_file(destination)

    assert summary.collection_count == 7
    assert summary.record_count == 2


@pytest.mark.parametrize("invalid_literal", ["NaN", "Infinity", "-Infinity"])
def test_validate_backup_file_rejects_non_json_numeric_literals(
    tmp_path: object,
    invalid_literal: str,
) -> None:
    from pathlib import Path

    source = Path(str(tmp_path)) / "invalid.json"
    source.write_text(f'{{"value": {invalid_literal}}}', encoding="utf-8")

    with pytest.raises(backup.SnapshotValidationError, match="strict JSON"):
        backup.validate_backup_file(source)


def test_validate_backup_file_rejects_duplicate_object_keys(tmp_path: object) -> None:
    from pathlib import Path

    source = Path(str(tmp_path)) / "duplicate.json"
    payload = create_backup(StubClient())
    serialized = json.dumps(payload)
    duplicated = serialized.replace(
        '"format": "forge-companion-collection-snapshot-v2"',
        '"format": "forge-companion-collection-snapshot-v2", '
        '"format": "forge-companion-collection-snapshot-v2"',
        1,
    )
    source.write_text(duplicated, encoding="utf-8")

    with pytest.raises(backup.SnapshotValidationError, match="strict JSON"):
        backup.validate_backup_file(source)


def test_write_backup_creates_json_without_secret_material(tmp_path: object) -> None:
    from pathlib import Path

    destination = Path(str(tmp_path)) / "backup.json"
    payload = {
        "format": "forge-companion-collection-snapshot-v1",
        "created_at": "2026-07-17T12:30:00+00:00",
        "resources": {"brews": []},
    }

    write_backup(payload, destination)

    assert json.loads(destination.read_text(encoding="utf-8")) == payload
    assert "token" not in destination.read_text(encoding="utf-8").lower()


def test_write_backup_does_not_reuse_predictable_temp_file(tmp_path: object) -> None:
    from pathlib import Path

    destination = Path(str(tmp_path)) / "snapshot.json"
    predictable_temp = Path(str(destination) + ".tmp")
    predictable_temp.write_text("other process", encoding="utf-8")
    payload = {"format": "forge-companion-collection-snapshot-v1", "resources": {}}

    write_backup(payload, destination)

    assert predictable_temp.read_text(encoding="utf-8") == "other process"


def test_write_backup_rejects_non_json_numbers_without_destination(tmp_path: object) -> None:
    from pathlib import Path

    destination = Path(str(tmp_path)) / "invalid.json"

    with pytest.raises(ValueError):
        write_backup({"value": float("nan")}, destination)

    assert not destination.exists()


def test_backup_rejects_has_more_without_page_progress() -> None:
    class StalledClient:
        calls = 0

        def get(self, path: str, params: object = None) -> dict[str, object]:
            self.calls += 1
            return {"data": [], "pagination": {"hasMore": True, "total": 1}}

    client = StalledClient()

    with pytest.raises(ValueError, match="no items while hasMore is true"):
        create_backup(client)

    assert client.calls == 1


def test_backup_rejects_terminal_page_that_does_not_match_total() -> None:
    class IncompleteClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {
                "data": [{"id": "only-item"}],
                "pagination": {"hasMore": False, "total": 2},
            }

    with pytest.raises(ValueError, match="expected 2 items, received 1"):
        create_backup(IncompleteClient())


def test_backup_rejects_non_object_pagination() -> None:
    class MalformedPaginationClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [{"id": "item"}], "pagination": ["malformed"]}

    with pytest.raises(TypeError, match="pagination is not an object"):
        create_backup(MalformedPaginationClient())


def test_backup_rejects_non_boolean_has_more_without_retrying() -> None:
    class MalformedHasMoreClient:
        calls = 0

        def get(self, path: str, params: object = None) -> dict[str, object]:
            self.calls += 1
            return {"data": [{"id": "item"}], "pagination": {"hasMore": "false"}}

    client = MalformedHasMoreClient()

    with pytest.raises(TypeError, match="hasMore is not a boolean"):
        create_backup(client)

    assert client.calls == 1


def test_backup_rejects_missing_pagination_for_paginated_collection() -> None:
    class MissingPaginationClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [{"id": "first-page-only"}]}

    with pytest.raises(TypeError, match="brews.*pagination is missing"):
        create_backup(MissingPaginationClient())


def test_backup_allows_missing_pagination_for_profile_collections() -> None:
    class NonPaginatedProfilesClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            if path.startswith("profiles/"):
                return {"data": [{"id": path}]}
            return {"data": [], "pagination": {"hasMore": False, "total": 0}}

    result = create_backup(NonPaginatedProfilesClient())

    assert result["resources"]["profiles_equipment"] == [{"id": "profiles/equipment"}]
    assert result["resources"]["profiles_styles"] == [{"id": "profiles/styles"}]


def test_backup_rejects_missing_data_field() -> None:
    class MissingDataClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"pagination": {"hasMore": False, "total": 0}}

    with pytest.raises(TypeError, match="data field is missing"):
        create_backup(MissingDataClient())


def test_backup_rejects_non_object_collection_record() -> None:
    class NonObjectRecordClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": ["not-an-object"], "pagination": {"hasMore": False, "total": 1}}

    with pytest.raises(TypeError, match="collection record is not an object"):
        create_backup(NonObjectRecordClient())
