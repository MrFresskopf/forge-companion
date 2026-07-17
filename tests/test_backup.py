import json
from datetime import UTC, datetime

import pytest

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

    assert result["format"] == "forge-companion-collection-snapshot-v1"
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
    assert client.calls[:2] == [
        ("brews", {"page": 1, "limit": 100}),
        ("brews", {"page": 2, "limit": 100}),
    ]


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

    assert result["resources"]["profiles_equipment"] == [
        {"id": "profiles/equipment"}
    ]
    assert result["resources"]["profiles_styles"] == [{"id": "profiles/styles"}]


def test_backup_rejects_missing_data_field() -> None:
    class MissingDataClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"pagination": {"hasMore": False, "total": 0}}

    with pytest.raises(TypeError, match="data field is missing"):
        create_backup(MissingDataClient())
