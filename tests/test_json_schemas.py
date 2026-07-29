import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError

_SCHEMA_DIR = Path(__file__).parents[1] / "docs" / "schemas"
_RESOURCES = [
    "brews",
    "inventory/fermentables",
    "inventory/hops",
    "inventory/yeasts",
    "inventory/miscs",
    "profiles/equipment",
    "profiles/styles",
]


def _validator(name: str) -> Draft202012Validator:
    schema = json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _doctor_check(resource: str) -> dict[str, object]:
    return {
        "resource": resource,
        "status": "ok",
        "http_status": 200,
        "code": "ok",
        "message": "OK",
    }


def _doctor_ok() -> dict[str, Any]:
    return {
        "schema": "forge-companion-doctor-v1",
        "generated_at": "2026-07-29T06:00:00Z",
        "command": "doctor",
        "status": "ok",
        "request_count": 7,
        "checks": [_doctor_check(resource) for resource in _RESOURCES],
        "errors": [],
    }


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


def test_draft_machine_output_schemas_are_valid_and_accept_expected_outcomes() -> None:
    doctor = _validator("doctor-v1.schema.json")
    inventory = _validator("inventory-audit-v1.schema.json")
    doctor.validate(_doctor_ok())

    partial = _doctor_ok()
    partial["status"] = "partial"
    partial["checks"][2] = {
        "resource": "inventory/hops",
        "status": "fail",
        "http_status": 500,
        "code": "http-error",
        "message": "HTTP 500",
    }
    doctor.validate(partial)

    future_failure = deepcopy(partial)
    future_failure["checks"][2]["code"] = "future-check-failed"
    future_failure["checks"][2]["http_status"] = None
    doctor.validate(future_failure)
    doctor.validate(
        {
            "schema": "forge-companion-doctor-v1",
            "generated_at": "2026-07-29T06:00:00Z",
            "command": "doctor",
            "status": "error",
            "request_count": 0,
            "checks": [],
            "errors": [
                {
                    "code": "authentication-not-configured",
                    "message": "Authentication is required.",
                }
            ],
        }
    )

    inventory.validate(_inventory_ok())
    inventory.validate(
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
    inventory.validate(
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
        "ok-without-checks",
        "partial-without-failure",
        "error-with-checks",
        "error-without-errors",
        "duplicate-resource",
        "reordered-resources",
        "truncated-checks",
        "oversized-checks",
        "ok-with-http-error",
        "ok-with-null-http-status",
        "fail-with-ok-code",
        "future-failure-with-success-http",
    ],
)
def test_doctor_schema_rejects_contradictory_outcomes(case: str) -> None:
    validator = _validator("doctor-v1.schema.json")
    document = _doctor_ok()
    if case == "ok-without-checks":
        document["request_count"] = 0
        document["checks"] = []
    elif case == "partial-without-failure":
        document["status"] = "partial"
    elif case == "error-with-checks":
        document["status"] = "error"
        document["request_count"] = 0
        document["errors"] = [{"code": "setup-failed", "message": "Setup failed."}]
    elif case == "error-without-errors":
        document["status"] = "error"
        document["request_count"] = 0
        document["checks"] = []
    elif case == "duplicate-resource":
        document["checks"][1]["resource"] = "brews"
    elif case == "reordered-resources":
        document["checks"][0], document["checks"][1] = (
            document["checks"][1],
            document["checks"][0],
        )
    elif case == "truncated-checks":
        document["checks"].pop()
    elif case == "oversized-checks":
        document["checks"].append(_doctor_check("brews"))
    elif case == "ok-with-http-error":
        document["checks"][0].update(
            status="ok",
            http_status=500,
            code="http-error",
        )
    elif case == "ok-with-null-http-status":
        document["checks"][0]["http_status"] = None
    elif case == "fail-with-ok-code":
        document["status"] = "partial"
        document["checks"][0].update(
            status="fail",
            http_status=None,
            code="ok",
        )
    else:
        document["status"] = "partial"
        document["checks"][0].update(
            status="fail",
            http_status=200,
            code="future-check-failed",
        )

    with pytest.raises(ValidationError):
        validator.validate(document)


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
        "network-request",
    ],
)
def test_inventory_schema_rejects_contradictory_outcomes(case: str) -> None:
    validator = _validator("inventory-audit-v1.schema.json")
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
    else:
        document["request_count"] = 1

    with pytest.raises(ValidationError):
        validator.validate(document)


def test_machine_output_schemas_allow_additive_object_members() -> None:
    doctor = _doctor_ok()
    doctor["future_top_level"] = True
    doctor["checks"][0]["future_check_field"] = "value"
    _validator("doctor-v1.schema.json").validate(doctor)

    doctor_error = {
        "schema": "forge-companion-doctor-v1",
        "generated_at": "2026-07-29T06:00:00Z",
        "command": "doctor",
        "status": "error",
        "request_count": 0,
        "checks": [],
        "errors": [
            {
                "code": "authentication-not-configured",
                "message": "Authentication is required.",
                "future_error_field": True,
            }
        ],
    }
    _validator("doctor-v1.schema.json").validate(doctor_error)

    inventory = deepcopy(_inventory_ok())
    inventory["future_top_level"] = True
    inventory["snapshot"]["future_snapshot_field"] = "value"
    inventory["findings"] = [
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
    _validator("inventory-audit-v1.schema.json").validate(inventory)

    inventory_error = {
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
    _validator("inventory-audit-v1.schema.json").validate(inventory_error)
