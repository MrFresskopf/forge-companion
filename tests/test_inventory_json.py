import json
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from typer.testing import CliRunner

import forge_companion.cli as cli
from forge_companion import backup
from forge_companion.cli import app
from forge_companion.inventory_audit import Finding, Severity
from forge_companion.inventory_output import (
    build_inventory_error_document,
    build_inventory_success_document,
    render_inventory_json,
)

runner = CliRunner()

_RESOURCES = (
    "brews",
    "inventory_fermentables",
    "inventory_hops",
    "inventory_yeasts",
    "inventory_miscs",
    "profiles_equipment",
    "profiles_styles",
)


def _legacy_snapshot(**resource_overrides: object) -> dict[str, object]:
    resources: dict[str, object] = {name: [] for name in _RESOURCES}
    resources.update(resource_overrides)
    return {
        "format": "forge-companion-collection-snapshot-v1",
        "created_at": "2026-07-29T06:00:00+00:00",
        "resources": resources,
    }


def _assert_inventory_schema(document: object) -> None:
    schema = json.loads(
        files("forge_companion")
        .joinpath("schemas/inventory-audit-v1.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert list(validator.iter_errors(document)) == []


def test_inventory_json_success_is_one_schema_valid_offline_document(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(json.dumps(_legacy_snapshot()), encoding="utf-8")

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 0
    assert len(result.output.splitlines()) == 1
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document == {
        "as_of": "2026-07-29",
        "command": "inventory",
        "errors": [],
        "findings": [],
        "generated_at": document["generated_at"],
        "request_count": 0,
        "schema": "forge-companion-inventory-audit-v1",
        "snapshot": {
            "collection_count": 7,
            "format": "forge-companion-collection-snapshot-v1",
            "integrity": "unavailable",
            "record_count": 0,
        },
        "status": "ok",
    }
    assert document["generated_at"].endswith("Z")


def test_inventory_json_invalid_as_of_is_structured_before_snapshot_access(
    tmp_path: Path,
) -> None:
    private_value = "private-invalid-date"
    missing_snapshot = tmp_path / "private-snapshot-name.json"

    result = runner.invoke(
        app,
        [
            "inventory",
            str(missing_snapshot),
            "--as-of",
            private_value,
            "--json",
        ],
    )

    assert result.exit_code == 2
    assert len(result.output.splitlines()) == 1
    assert private_value not in result.output
    assert str(missing_snapshot) not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["status"] == "error"
    assert document["as_of"] is None
    assert document["snapshot"] is None
    assert document["findings"] == []
    assert document["errors"] == [
        {"code": "invalid-as-of", "message": "Use YYYY-MM-DD."}
    ]


def test_inventory_json_missing_snapshot_uses_fixed_private_error(tmp_path: Path) -> None:
    missing_snapshot = tmp_path / "private-brew-name.json"

    result = runner.invoke(
        app,
        ["inventory", str(missing_snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    assert str(missing_snapshot) not in result.output
    assert missing_snapshot.name not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["as_of"] == "2026-07-29"
    assert document["errors"] == [
        {"code": "snapshot-not-found", "message": "Snapshot not found."}
    ]


def test_inventory_json_missing_snapshot_does_not_use_separate_metadata_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "private-brew-name.json"
    original_exists = Path.exists

    def fail_exists(self: Path) -> bool:
        if self == snapshot:
            raise AssertionError("separate existence probe attempted")
        return original_exists(self)

    monkeypatch.setattr(Path, "exists", fail_exists)

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    assert snapshot.name not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["errors"] == [
        {"code": "snapshot-not-found", "message": "Snapshot not found."}
    ]


def test_inventory_json_invalid_snapshot_does_not_reflect_contents(tmp_path: Path) -> None:
    secret_like_value = "private-token-like-value"
    snapshot = tmp_path / "private-brew-name.json"
    snapshot.write_text(f'{{"secret":"{secret_like_value}"}}', encoding="utf-8")

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    assert secret_like_value not in result.output
    assert snapshot.name not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["as_of"] == "2026-07-29"
    assert document["errors"] == [
        {"code": "snapshot-invalid", "message": "Snapshot is invalid."}
    ]


def test_inventory_json_snapshot_read_failure_is_data_minimal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = tmp_path / "private-brew-name.json"
    snapshot.write_text("{}", encoding="utf-8")
    private_error = "private operating-system detail"

    def fail_read(source: Path, *, allow_legacy_v1: bool = False) -> dict[str, object]:
        raise OSError(private_error)

    monkeypatch.setattr(cli, "load_snapshot_file", fail_read)

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    assert private_error not in result.output
    assert snapshot.name not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["errors"] == [
        {"code": "snapshot-read-failed", "message": "Snapshot could not be read."}
    ]


def test_inventory_json_duplicate_serializes_distinct_related_item_id(
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            _legacy_snapshot(
                inventory_hops=[
                    {
                        "id": "hop-1",
                        "name": "Citra",
                        "quantity": 100,
                        "form": "pellet",
                        "year": 2024,
                        "lotNumber": "LOT-A",
                    },
                    {
                        "id": "hop-2",
                        "name": " citra ",
                        "quantity": 200,
                        "form": "PELLET",
                        "year": 2024,
                        "lotNumber": "lot-a",
                    },
                ]
            )
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 0
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    duplicate = next(
        finding
        for finding in document["findings"]
        if finding["code"] == "possible-duplicate"
    )
    assert duplicate["item_id"] == "hop-2"
    assert duplicate["related_item_id"] == "hop-1"
    assert duplicate["related_item_id"] != duplicate["item_id"]


def test_inventory_json_rejects_duplicate_with_equal_item_ids(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            _legacy_snapshot(
                inventory_hops=[
                    {
                        "id": "same-hop-id",
                        "name": "Citra",
                        "quantity": 100,
                        "form": "pellet",
                        "year": 2024,
                        "lotNumber": "LOT-A",
                    },
                    {
                        "id": "same-hop-id",
                        "name": "Citra",
                        "quantity": 200,
                        "form": "pellet",
                        "year": 2024,
                        "lotNumber": "LOT-A",
                    },
                ]
            )
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["status"] == "error"
    assert document["findings"] == []
    assert document["errors"] == [
        {"code": "snapshot-invalid", "message": "Snapshot is invalid."}
    ]


def test_inventory_json_allowlists_fields_and_never_initializes_network_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    secret_like_value = "managed-secret-must-not-appear"
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            _legacy_snapshot(
                inventory_yeasts=[
                    {
                        "id": "yeast-1",
                        "name": "Example Yeast",
                        "quantity": 1,
                        "quantityUnit": "pkg",
                        "expiryDate": "2026-07-01",
                        "accessToken": secret_like_value,
                    }
                ]
            )
        ),
        encoding="utf-8",
    )

    def fail_client(*args: object, **kwargs: object) -> object:
        pytest.fail("offline inventory initialized a BrewForge client")

    monkeypatch.setattr(cli, "BrewForgeClient", fail_client)

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
        env={"BREWFORGE_API_TOKEN": secret_like_value},
    )

    assert result.exit_code == 0
    assert secret_like_value not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["request_count"] == 0
    assert set(document["findings"][0]) == {
        "category",
        "code",
        "item_id",
        "message",
        "name",
        "severity",
    }


def test_inventory_json_v2_snapshot_reports_verified_integrity(tmp_path: Path) -> None:
    class EmptyClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            return {"data": [], "pagination": {"hasMore": False, "total": 0}}

    snapshot = tmp_path / "snapshot-v2.json"
    backup.write_backup(backup.create_backup(EmptyClient()), snapshot)

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 0
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["snapshot"] == {
        "collection_count": 7,
        "format": "forge-companion-collection-snapshot-v2",
        "integrity": "verified",
        "record_count": 0,
    }


def test_inventory_json_invalid_inventory_field_stays_structured(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            _legacy_snapshot(
                inventory_yeasts=[
                    {
                        "id": "yeast-1",
                        "name": "Example Yeast",
                        "quantity": 1,
                        "quantityUnit": "pkg",
                        "expiryDate": "not-a-date",
                    }
                ]
            )
        ),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert len(result.output.splitlines()) == 1
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["errors"] == [
        {"code": "snapshot-invalid", "message": "Snapshot is invalid."}
    ]


@pytest.mark.parametrize("field", ["id", "name"])
def test_inventory_json_rejects_nested_reflected_fields_without_leaking(
    field: str, tmp_path: Path
) -> None:
    private_value = "nested-private-value"
    item: dict[str, object] = {
        "id": "yeast-1",
        "name": "Example Yeast",
        "quantity": 1,
    }
    item[field] = {"private": private_value} if field == "id" else [private_value]
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(_legacy_snapshot(inventory_yeasts=[item])),
        encoding="utf-8",
    )

    result = runner.invoke(
        app,
        ["inventory", str(snapshot), "--as-of", "2026-07-29", "--json"],
    )

    assert result.exit_code == 1
    assert private_value not in result.output
    document = json.loads(result.output)
    _assert_inventory_schema(document)
    assert document["errors"] == [
        {"code": "snapshot-invalid", "message": "Snapshot is invalid."}
    ]


def test_inventory_json_lone_surrogate_is_safe_in_real_subprocess(tmp_path: Path) -> None:
    snapshot = tmp_path / "snapshot.json"
    snapshot.write_text(
        json.dumps(
            _legacy_snapshot(
                inventory_yeasts=[
                    {
                        "id": "yeast-1",
                        "name": "\ud800",
                        "quantity": 1,
                    }
                ]
            )
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    environment.pop("PYTHONHOME", None)

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from forge_companion.cli import app; app()",
            "inventory",
            str(snapshot),
            "--as-of",
            "2026-07-29",
            "--json",
        ],
        check=False,
        capture_output=True,
        env=environment,
    )

    assert result.returncode == 0
    assert result.stderr == b""
    assert result.stdout.endswith(b"\n")
    assert result.stdout.count(b"\n") == 1
    assert b"\\ud800" in result.stdout
    document = json.loads(result.stdout)
    _assert_inventory_schema(document)
    assert document["findings"][0]["name"] == "\ud800"


@pytest.mark.parametrize(
    ("code", "as_of"),
    [
        ("invalid-as-of", date(2026, 7, 29)),
        ("snapshot-not-found", None),
        ("snapshot-invalid", None),
        ("snapshot-read-failed", None),
    ],
)
def test_inventory_error_builder_rejects_contradictory_chronology(
    code: str, as_of: date | None
) -> None:
    with pytest.raises(ValueError, match="as_of"):
        build_inventory_error_document(code, "fixed message", as_of=as_of)


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("future-error", "fixed message"),
        ("snapshot-invalid", {"private": "nested-private-value"}),
    ],
)
def test_inventory_error_builder_rejects_schema_invalid_runtime_values(
    code: object, message: object
) -> None:
    with pytest.raises(ValueError):
        build_inventory_error_document(code, message, as_of=date(2026, 7, 29))


def test_inventory_success_builder_rejects_unsupported_snapshot_format() -> None:
    payload = _legacy_snapshot()
    payload["format"] = "future-format"

    with pytest.raises(ValueError, match="format"):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))


@pytest.mark.parametrize(
    "finding",
    [
        Finding(
            code="Future-Finding",
            severity=Severity.WARNING,
            category="yeasts",
            item_id="yeast-1",
            name="Example Yeast",
            message="future advisory",
        ),
        Finding(
            code="future-finding",
            severity=Severity.WARNING,
            category="yeasts",
            item_id="yeast-1",
            name={"private": "nested-private-value"},
            message="future advisory",
        ),
    ],
)
def test_inventory_success_builder_rejects_schema_invalid_findings(
    finding: Finding,
) -> None:
    with pytest.raises(ValueError):
        build_inventory_success_document(
            _legacy_snapshot(), [finding], as_of=date(2026, 7, 29)
        )


@pytest.mark.parametrize(
    "code",
    ["snapshot-not-found", "snapshot-invalid", "snapshot-read-failed"],
)
def test_inventory_error_builder_rejects_datetime_as_of(code: str) -> None:
    with pytest.raises(ValueError, match="date"):
        build_inventory_error_document(
            code,
            "fixed message",
            as_of=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
        )


def test_inventory_success_builder_rejects_datetime_as_of() -> None:
    with pytest.raises(ValueError, match="date"):
        build_inventory_success_document(
            _legacy_snapshot(),
            [],
            as_of=datetime(2026, 7, 29, 1, 2, 3, tzinfo=UTC),
        )


@pytest.mark.parametrize(
    "records",
    [
        "nested-private-value",
        {"private": "nested-private-value"},
        ["nested-private-value"],
    ],
)
def test_inventory_success_builder_rejects_malformed_resource_records(
    records: object,
) -> None:
    payload = {
        "format": "forge-companion-collection-snapshot-v1",
        "resources": {"inventory_yeasts": records},
    }

    with pytest.raises(ValueError):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))


def test_inventory_success_builder_rejects_incomplete_snapshot_resources() -> None:
    payload = _legacy_snapshot()
    del payload["resources"]["inventory_hops"]

    with pytest.raises(ValueError):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))


def test_inventory_success_builder_rejects_v1_payload_relabelled_as_v2() -> None:
    payload = _legacy_snapshot()
    payload["format"] = "forge-companion-collection-snapshot-v2"

    with pytest.raises(ValueError):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))


def test_inventory_builders_reject_date_and_datetime_subclasses() -> None:
    class HostileDate(date):
        def isoformat(self) -> str:
            return "not-a-date"

    class HostileDatetime(datetime):
        def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
            return "not-a-datetime"

    with pytest.raises(ValueError):
        build_inventory_error_document(
            "snapshot-invalid",
            "fixed message",
            as_of=HostileDate(2026, 7, 29),
        )
    with pytest.raises(ValueError):
        build_inventory_success_document(
            _legacy_snapshot(), [], as_of=HostileDate(2026, 7, 29)
        )
    hostile_timestamp = HostileDatetime(2026, 7, 29, tzinfo=UTC)
    with pytest.raises(ValueError):
        build_inventory_error_document(
            "snapshot-invalid",
            "fixed message",
            as_of=date(2026, 7, 29),
            generated_at=hostile_timestamp,
        )
    with pytest.raises(ValueError):
        build_inventory_success_document(
            _legacy_snapshot(),
            [],
            as_of=date(2026, 7, 29),
            generated_at=hostile_timestamp,
        )


@pytest.mark.parametrize("generated_at", [False, 0, "", [], {}])
def test_inventory_builders_reject_falsy_generated_at(generated_at: object) -> None:
    with pytest.raises(ValueError):
        build_inventory_error_document(
            "snapshot-invalid",
            "fixed message",
            as_of=date(2026, 7, 29),
            generated_at=generated_at,
        )
    with pytest.raises(ValueError):
        build_inventory_success_document(
            _legacy_snapshot(),
            [],
            as_of=date(2026, 7, 29),
            generated_at=generated_at,
        )


@pytest.mark.parametrize("findings", [{}, set()])
def test_inventory_success_builder_rejects_non_sequence_findings(
    findings: object,
) -> None:
    with pytest.raises(ValueError, match="findings"):
        build_inventory_success_document(
            _legacy_snapshot(), findings, as_of=date(2026, 7, 29)
        )


@pytest.mark.parametrize("document", [{}, []])
def test_inventory_renderer_rejects_documents_not_built_by_producer(
    document: object,
) -> None:
    with pytest.raises(ValueError, match="producer"):
        render_inventory_json(document)


def test_inventory_renderer_rejects_mutated_producer_document() -> None:
    document = build_inventory_success_document(
        _legacy_snapshot(), [], as_of=date(2026, 7, 29)
    )
    document["status"] = "error"

    with pytest.raises(ValueError, match="modified"):
        render_inventory_json(document)


def test_inventory_success_builder_rejects_snapshot_string_subclasses() -> None:
    class SpoofedResourceName(str):
        def __hash__(self) -> int:
            return hash("brews")

        def __eq__(self, other: object) -> bool:
            return other == "brews"

    class HostileTimestamp(str):
        pass

    payload = _legacy_snapshot()
    brews = payload["resources"].pop("brews")
    payload["resources"][SpoofedResourceName("not-brews")] = brews
    with pytest.raises(ValueError, match="plain JSON"):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))

    payload = _legacy_snapshot()
    payload["created_at"] = HostileTimestamp(payload["created_at"])
    with pytest.raises(ValueError, match="plain JSON"):
        build_inventory_success_document(payload, [], as_of=date(2026, 7, 29))


@pytest.mark.parametrize("related_item_id", [False, 0, [], {}, "unexpected"])
def test_non_duplicate_finding_rejects_related_item_id(
    related_item_id: object,
) -> None:
    finding = Finding(
        code="future-advisory",
        severity=Severity.WARNING,
        category="yeasts",
        item_id="y-1",
        name="Yeast",
        message="Advisory",
        related_item_id=related_item_id,
    )

    with pytest.raises(ValueError, match="related item ID"):
        build_inventory_success_document(
            _legacy_snapshot(), [finding], as_of=date(2026, 7, 29)
        )
