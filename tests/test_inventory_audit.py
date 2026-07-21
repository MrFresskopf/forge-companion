from datetime import date

from forge_companion.inventory_audit import Severity, audit_inventory


def test_audit_reports_expired_inventory_item() -> None:
    resources = {
        "inventory_yeasts": [
            {
                "id": "yeast-1",
                "name": "Example Lager Yeast",
                "quantity": 2,
                "quantityUnit": "pkg",
                "expiryDate": "2026-07-01",
            }
        ]
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == "expired"
    assert finding.severity is Severity.WARNING
    assert finding.category == "yeasts"
    assert finding.item_id == "yeast-1"
    assert finding.name == "Example Lager Yeast"
    assert finding.message == "expired on 2026-07-01"


def test_audit_does_not_mark_item_expiring_today_as_expired() -> None:
    resources = {
        "inventory_hops": [
            {
                "id": "hop-1",
                "name": "Example Hop",
                "quantity": 100,
                "expiryDate": "2026-07-17",
            }
        ]
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    assert findings == []


def test_audit_reports_negative_quantity_as_error() -> None:
    resources = {
        "inventory_fermentables": [{"id": "malt-1", "name": "Example Malt", "quantity": -250}]
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    assert [(finding.code, finding.severity, finding.message) for finding in findings] == [
        ("negative-quantity", Severity.ERROR, "quantity is negative: -250")
    ]


def test_audit_flags_only_conservative_duplicate_hop_signature() -> None:
    resources = {
        "inventory_hops": [
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
            {
                "id": "hop-3",
                "name": "Citra",
                "quantity": 300,
                "form": "pellet",
                "year": 2023,
                "lotNumber": "LOT-A",
            },
        ]
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    duplicates = [finding for finding in findings if finding.code == "possible-duplicate"]
    assert len(duplicates) == 1
    assert duplicates[0].item_id == "hop-2"
    assert duplicates[0].message == "same identity fields as item hop-1"


def test_audit_does_not_compare_incomplete_duplicate_signatures() -> None:
    resources = {
        "inventory_yeasts": [
            {
                "id": "yeast-1",
                "name": "W-34/70",
                "quantity": 1,
                "quantityUnit": "pkg",
                "form": "dry",
            },
            {
                "id": "yeast-2",
                "name": "W-34/70",
                "quantity": 1,
                "quantityUnit": "pkg",
                "form": "dry",
            },
        ]
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    assert not any(finding.code == "possible-duplicate" for finding in findings)


def test_audit_reports_missing_category_specific_unit() -> None:
    resources = {
        "inventory_yeasts": [{"id": "yeast-1", "name": "Dry Yeast", "quantity": 2}],
        "inventory_miscs": [{"id": "misc-1", "name": "Lactic Acid", "quantity": 50, "unit": ""}],
    }

    findings = audit_inventory(resources, as_of=date(2026, 7, 17))

    missing_units = [finding for finding in findings if finding.code == "missing-unit"]
    assert [(finding.category, finding.item_id) for finding in missing_units] == [
        ("yeasts", "yeast-1"),
        ("miscs", "misc-1"),
    ]
