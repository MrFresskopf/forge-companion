"""Offline, read-only checks for BrewForge inventory snapshots."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Severity(StrEnum):
    """Finding severity for human and machine-readable reports."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True)
class Finding:
    """One actionable inventory observation."""

    code: str
    severity: Severity
    category: str
    item_id: str
    name: str
    message: str


_DUPLICATE_FIELDS = {
    "fermentables": ("name", "type", "lotNumber"),
    "hops": ("name", "form", "year", "lotNumber"),
    "yeasts": ("name", "form", "lotNumber", "manufacturingDate"),
    "miscs": ("name", "type", "use", "unit", "lotNumber"),
}


def _normalized(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def audit_inventory(
    resources: Mapping[str, object],
    as_of: date,
) -> list[Finding]:
    """Audit inventory resources from a collection snapshot."""
    findings: list[Finding] = []
    for resource_name, raw_items in resources.items():
        if not resource_name.startswith("inventory_") or not isinstance(raw_items, list):
            continue
        category = resource_name.removeprefix("inventory_")
        seen_signatures: dict[tuple[str, ...], str] = {}
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item_id = str(raw_item.get("id", ""))
            name = str(raw_item.get("name", "Unnamed item"))
            signature_fields = _DUPLICATE_FIELDS.get(category)
            if signature_fields is not None:
                signature = tuple(_normalized(raw_item.get(field)) for field in signature_fields)
                if all(signature):
                    previous_id = seen_signatures.get(signature)
                    if previous_id is None:
                        seen_signatures[signature] = item_id
                    else:
                        findings.append(
                            Finding(
                                code="possible-duplicate",
                                severity=Severity.WARNING,
                                category=category,
                                item_id=item_id,
                                name=name,
                                message=f"same identity fields as item {previous_id}",
                            )
                        )
            quantity = raw_item.get("quantity")
            unit_field = {"yeasts": "quantityUnit", "miscs": "unit"}.get(category)
            if unit_field is not None and not _normalized(raw_item.get(unit_field)):
                findings.append(
                    Finding(
                        code="missing-unit",
                        severity=Severity.WARNING,
                        category=category,
                        item_id=item_id,
                        name=name,
                        message=f"{unit_field} is missing",
                    )
                )
            if (
                isinstance(quantity, (int, float))
                and not isinstance(quantity, bool)
                and quantity < 0
            ):
                findings.append(
                    Finding(
                        code="negative-quantity",
                        severity=Severity.ERROR,
                        category=category,
                        item_id=item_id,
                        name=name,
                        message=f"quantity is negative: {quantity}",
                    )
                )
            expiry_raw = raw_item.get("expiryDate")
            if not isinstance(expiry_raw, str) or not expiry_raw:
                continue
            expiry = date.fromisoformat(expiry_raw[:10])
            if expiry < as_of:
                findings.append(
                    Finding(
                        code="expired",
                        severity=Severity.WARNING,
                        category=category,
                        item_id=item_id,
                        name=name,
                        message=f"expired on {expiry.isoformat()}",
                    )
                )
    return findings
