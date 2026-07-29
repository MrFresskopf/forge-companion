import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

_SCHEMA_PATH = (
    Path(__file__).parents[1] / "docs" / "schemas" / "inventory-audit-v1.schema.json"
)


def _validator() -> Draft202012Validator:
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _inventory_ok() -> dict[str, Any]:
    return {
        "schema": "forge-companion-inventory-audit-v1",
        "generated_at": "2026-07-29T06:00:00Z",
        "command": "inventory",
        "status": "ok",
        "request_count": 0,
        "as_of": "2026-07-29",
        "snapshot": {
            "format": "forge-companion-collection-snapshot-v2",
            "integrity": "verified",
            "collection_count": 7,
            "record_count": 10,
        },
        "findings": [],
        "errors": [],
    }


def test_inventory_schema_is_valid_and_accepts_expected_outcomes() -> None:
    validator = _validator()
    validator.validate(_inventory_ok())
    validator.validate(
        {
            "schema": "forge-companion-inventory-audit-v1",
            "generated_at": "2026-07-29T06:00:00Z",
            "command": "inventory",
            "status": "error",
            "request_count": 0,
            "as_of": None,
            "snapshot": None,
            "findings": [],
            "errors": [{"code": "invalid-as-of", "message": "Use YYYY-MM-DD."}],
        }
    )
    validator.validate(
        {
            "schema": "forge-companion-inventory-audit-v1",
            "generated_at": "2026-07-29T06:00:00Z",
            "command": "inventory",
            "status": "error",
            "request_count": 0,
            "as_of": "2026-07-29",
            "snapshot": None,
            "findings": [],
            "errors": [
                {"code": "snapshot-not-found", "message": "Snapshot not found."}
            ],
        }
    )


@pytest.mark.parametrize(
    "case",
    [
        "impossible-date",
        "ok-without-as-of-date",
        "ok-without-snapshot",
        "ok-with-command-error",
        "error-without-errors",
        "invalid-as-of-with-resolved-date",
        "snapshot-error-without-resolved-date",
        "error-with-finding",
        "legacy-with-verified-integrity",
        "v2-with-unavailable-integrity",
        "duplicate-without-related-id",
        "duplicate-with-empty-related-id",
        "finding-with-empty-item-id",
        "unknown-command-error-before-date-resolution",
        "unknown-command-error-after-date-resolution",
        "network-request",
    ],
)
def test_inventory_schema_rejects_contradictory_outcomes(case: str) -> None:
    validator = _validator()
    document = _inventory_ok()
    if case == "impossible-date":
        document["as_of"] = "2026-02-31"
    elif case == "ok-without-as-of-date":
        document["as_of"] = None
    elif case == "ok-without-snapshot":
        document["snapshot"] = None
    elif case == "ok-with-command-error":
        document["errors"] = [{"code": "snapshot-invalid", "message": "Invalid snapshot."}]
    elif case == "error-without-errors":
        document["status"] = "error"
        document["snapshot"] = None
        document["findings"] = []
    elif case == "invalid-as-of-with-resolved-date":
        document["status"] = "error"
        document["snapshot"] = None
        document["findings"] = []
        document["errors"] = [
            {"code": "invalid-as-of", "message": "Use YYYY-MM-DD."}
        ]
    elif case == "snapshot-error-without-resolved-date":
        document["status"] = "error"
        document["as_of"] = None
        document["snapshot"] = None
        document["findings"] = []
        document["errors"] = [
            {"code": "snapshot-not-found", "message": "Snapshot not found."}
        ]
    elif case == "error-with-finding":
        document["status"] = "error"
        document["snapshot"] = None
        document["errors"] = [{"code": "snapshot-invalid", "message": "Invalid snapshot."}]
        document["findings"] = [
            {
                "code": "expired",
                "severity": "warning",
                "category": "yeasts",
                "item_id": "yeast-1",
                "name": "Example Yeast",
                "message": "expired",
            }
        ]
    elif case == "legacy-with-verified-integrity":
        document["snapshot"]["format"] = "forge-companion-collection-snapshot-v1"
    elif case == "v2-with-unavailable-integrity":
        document["snapshot"]["integrity"] = "unavailable"
    elif case == "duplicate-without-related-id":
        document["findings"] = [
            {
                "code": "possible-duplicate",
                "severity": "warning",
                "category": "hops",
                "item_id": "hop-2",
                "name": "Example Hop",
                "message": "possible duplicate",
            }
        ]
    elif case == "duplicate-with-empty-related-id":
        document["findings"] = [
            {
                "code": "possible-duplicate",
                "severity": "warning",
                "category": "hops",
                "item_id": "hop-2",
                "related_item_id": "",
                "name": "Example Hop",
                "message": "possible duplicate",
            }
        ]
    elif case == "finding-with-empty-item-id":
        document["findings"] = [
            {
                "code": "expired",
                "severity": "warning",
                "category": "yeasts",
                "item_id": "",
                "name": "Example Yeast",
                "message": "expired",
            }
        ]
    elif case == "unknown-command-error-before-date-resolution":
        document["status"] = "error"
        document["as_of"] = None
        document["snapshot"] = None
        document["findings"] = []
        document["errors"] = [{"code": "future-error", "message": "future error"}]
    elif case == "unknown-command-error-after-date-resolution":
        document["status"] = "error"
        document["snapshot"] = None
        document["findings"] = []
        document["errors"] = [{"code": "future-error", "message": "future error"}]
    else:
        document["request_count"] = 1

    with pytest.raises(ValidationError):
        validator.validate(document)


def test_inventory_schema_allows_additive_members_and_finding_codes() -> None:
    validator = _validator()
    document = deepcopy(_inventory_ok())
    document["future_top_level"] = True
    document["snapshot"]["future_snapshot_field"] = "value"
    document["findings"] = [
        {
            "code": "future-advisory",
            "severity": "info",
            "category": "hops",
            "item_id": "hop-1",
            "name": "Example Hop",
            "message": "future advisory",
            "future_finding_field": True,
        }
    ]
    validator.validate(document)

    error_document = {
        "schema": "forge-companion-inventory-audit-v1",
        "generated_at": "2026-07-29T06:00:00Z",
        "command": "inventory",
        "status": "error",
        "request_count": 0,
        "as_of": None,
        "snapshot": None,
        "findings": [],
        "errors": [
            {
                "code": "invalid-as-of",
                "message": "Use YYYY-MM-DD.",
                "future_error_field": True,
            }
        ],
    }
    validator.validate(error_document)
