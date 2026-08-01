"""Versioned, data-minimal machine output for offline inventory audits."""

import json
import math
import re
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any, Literal, cast

from forge_companion.backup import validate_snapshot_payload
from forge_companion.inventory_audit import Finding, Severity

SCHEMA_VERSION = "forge-companion-inventory-audit-v1"
InventoryErrorCode = Literal[
    "invalid-as-of",
    "snapshot-not-found",
    "snapshot-invalid",
    "snapshot-read-failed",
]
_INVENTORY_ERROR_CODES = {
    "invalid-as-of",
    "snapshot-not-found",
    "snapshot-invalid",
    "snapshot-read-failed",
}
_SNAPSHOT_FORMATS = {
    "forge-companion-collection-snapshot-v1",
    "forge-companion-collection-snapshot-v2",
}
_FINDING_TOKEN = re.compile(r"^[a-z][a-z0-9-]*$")
_FINDING_SEVERITIES = {"info", "warning", "error"}
_DOCUMENT_TOKEN = object()


def _encode_json(document: Mapping[str, object]) -> str:
    return json.dumps(document, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


class _InventoryDocument(dict[str, object]):
    """Builder-owned dict whose canonical serialized form detects later mutation."""

    def __init__(self, value: dict[str, object], *, token: object) -> None:
        if token is not _DOCUMENT_TOKEN:
            raise ValueError("inventory document must be created by a producer")
        super().__init__(value)
        self._sealed_json = _encode_json(self)

    def render(self) -> str:
        if _encode_json(self) != self._sealed_json:
            raise ValueError("inventory producer document was modified")
        return self._sealed_json


def _seal_document(value: dict[str, object]) -> dict[str, object]:
    return _InventoryDocument(value, token=_DOCUMENT_TOKEN)


def _require_plain_json(value: object, active: set[int] | None = None) -> None:
    """Reject custom Python behavior at a boundary normally populated by JSON."""
    value_type = type(value)
    if value is None or value_type in (str, int, bool):
        return
    if value_type is float:
        if not math.isfinite(cast(float, value)):
            raise ValueError("snapshot payload must contain finite plain JSON values")
        return
    if value_type not in (dict, list):
        raise ValueError("snapshot payload must contain only plain JSON values")

    seen = active if active is not None else set()
    marker = id(value)
    if marker in seen:
        raise ValueError("snapshot payload must not contain cycles")
    seen.add(marker)
    try:
        if value_type is list:
            for item in cast(list[object], value):
                _require_plain_json(item, seen)
        else:
            for key, item in cast(dict[object, object], value).items():
                if type(key) is not str:
                    raise ValueError("snapshot payload must use plain JSON string keys")
                _require_plain_json(item, seen)
    finally:
        seen.remove(marker)


def _require_plain_date(value: object) -> date:
    if not isinstance(value, date) or isinstance(value, datetime) or type(value) is not date:
        raise ValueError("as_of must be a date without a time")
    return value


def _format_generated_at(value: object | None) -> str:
    if value is None:
        timestamp = datetime.now(UTC)
    elif not isinstance(value, datetime) or type(value) is not datetime:
        raise ValueError("generated_at must be a datetime")
    else:
        timestamp = value
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must include a timezone")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _serialize_finding(finding: Finding) -> dict[str, object]:
    """Allowlist contract fields and enforce producer-only ID relationships."""
    if type(finding) is not Finding:
        raise ValueError("inventory findings must use the Finding type")
    if type(finding.severity) is not Severity:
        raise ValueError("inventory finding has an invalid severity")
    severity = finding.severity.value
    if type(finding.code) is not str or _FINDING_TOKEN.fullmatch(finding.code) is None:
        raise ValueError("inventory finding has an invalid code")
    if severity not in _FINDING_SEVERITIES:
        raise ValueError("inventory finding has an invalid severity")
    if (
        type(finding.category) is not str
        or _FINDING_TOKEN.fullmatch(finding.category) is None
    ):
        raise ValueError("inventory finding has an invalid category")
    if type(finding.item_id) is not str or not finding.item_id.strip():
        raise ValueError("inventory finding has no item ID")
    if type(finding.name) is not str or type(finding.message) is not str:
        raise ValueError("inventory finding text must be strings")
    serialized: dict[str, object] = {
        "code": finding.code,
        "severity": severity,
        "category": finding.category,
        "item_id": finding.item_id,
        "name": finding.name,
        "message": finding.message,
    }
    if finding.code == "possible-duplicate":
        related_item_id = finding.related_item_id
        if (
            type(related_item_id) is not str
            or not related_item_id.strip()
            or related_item_id == finding.item_id
        ):
            raise ValueError("duplicate finding has an invalid related item ID")
        serialized["related_item_id"] = related_item_id
    elif finding.related_item_id is not None:
        raise ValueError("non-duplicate finding must not have a related item ID")
    return serialized


def build_inventory_error_document(
    code: InventoryErrorCode,
    message: str,
    *,
    as_of: date | None,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build one data-minimal inventory-v1 command error."""
    if type(code) is not str or code not in _INVENTORY_ERROR_CODES:
        raise ValueError("unsupported inventory error code")
    if type(message) is not str:
        raise ValueError("inventory error message must be a string")
    if code == "invalid-as-of":
        if as_of is not None:
            raise ValueError("invalid-as-of requires as_of to be null")
        validated_as_of = None
    else:
        if as_of is None:
            raise ValueError(f"{code} requires a resolved as_of date")
        validated_as_of = _require_plain_date(as_of)

    return _seal_document({
        "schema": SCHEMA_VERSION,
        "generated_at": _format_generated_at(generated_at),
        "command": "inventory",
        "status": "error",
        "request_count": 0,
        "as_of": validated_as_of.isoformat() if validated_as_of is not None else None,
        "snapshot": None,
        "findings": [],
        "errors": [{"code": code, "message": message}],
    })


def build_inventory_success_document(
    payload: dict[str, Any],
    findings: Sequence[Finding],
    *,
    as_of: date,
    generated_at: datetime | None = None,
) -> dict[str, object]:
    """Build one successful inventory-v1 document from a validated snapshot."""
    validated_as_of = _require_plain_date(as_of)
    if type(payload) is not dict:
        raise ValueError("snapshot payload is not an object")
    if type(findings) not in (list, tuple):
        raise ValueError("findings must be a list or tuple")
    if any(type(finding) is not Finding for finding in findings):
        raise ValueError("findings must contain Finding objects")
    _require_plain_json(payload)
    validate_snapshot_payload(payload, allow_legacy_v1=True)
    resources = payload["resources"]
    if type(resources) is not dict:
        raise ValueError("snapshot resources is not an object")
    if any(
        type(records) is not list
        or any(type(record) is not dict for record in records)
        for records in resources.values()
    ):
        raise ValueError("snapshot resources must contain lists of objects")
    snapshot_format = payload["format"]
    if type(snapshot_format) is not str or snapshot_format not in _SNAPSHOT_FORMATS:
        raise ValueError("unsupported inventory snapshot format")
    return _seal_document({
        "schema": SCHEMA_VERSION,
        "generated_at": _format_generated_at(generated_at),
        "command": "inventory",
        "status": "ok",
        "request_count": 0,
        "as_of": validated_as_of.isoformat(),
        "snapshot": {
            "format": snapshot_format,
            "integrity": (
                "verified"
                if snapshot_format == "forge-companion-collection-snapshot-v2"
                else "unavailable"
            ),
            "collection_count": len(resources),
            "record_count": sum(len(records) for records in resources.values()),
        },
        "findings": [_serialize_finding(finding) for finding in findings],
        "errors": [],
    })


def render_inventory_json(document: Mapping[str, object]) -> str:
    """Render deterministic compact JSON suitable for stdout automation."""
    if type(document) is not _InventoryDocument:
        raise ValueError("inventory document must be created by a producer")
    return document.render()
