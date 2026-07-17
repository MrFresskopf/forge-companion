"""Create portable, read-only snapshots of supported BrewForge collections."""

import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from forge_companion.client import ReadClient

_RESOURCES = {
    "brews": ("brews", True),
    "inventory_fermentables": ("inventory/fermentables", True),
    "inventory_hops": ("inventory/hops", True),
    "inventory_yeasts": ("inventory/yeasts", True),
    "inventory_miscs": ("inventory/miscs", True),
    "profiles_equipment": ("profiles/equipment", False),
    "profiles_styles": ("profiles/styles", False),
}


def _collect(client: ReadClient, path: str, require_pagination: bool) -> list[object]:
    items: list[object] = []
    page = 1
    max_pages = 100
    while page <= max_pages:
        payload = client.get(path, params={"page": page, "limit": 100})
        if "data" not in payload:
            raise TypeError(f"BrewForge resource {path!r}: data field is missing")
        data = payload["data"]
        if not isinstance(data, list):
            raise TypeError(f"BrewForge resource {path!r} returned non-list data")
        items.extend(data)
        if "pagination" not in payload:
            if require_pagination:
                raise TypeError(f"BrewForge resource {path!r}: pagination is missing")
            return items
        pagination = payload["pagination"]
        if not isinstance(pagination, dict):
            raise TypeError(f"BrewForge resource {path!r}: pagination is not an object")
        if "hasMore" not in pagination:
            raise TypeError(f"BrewForge resource {path!r}: pagination has no hasMore field")

        has_more = pagination["hasMore"]
        if not isinstance(has_more, bool):
            raise TypeError(f"BrewForge resource {path!r}: hasMore is not a boolean")
        total = pagination.get("total")
        if total is not None and (not isinstance(total, int) or isinstance(total, bool)):
            raise TypeError(f"BrewForge resource {path!r} returned an invalid total")
        if isinstance(total, int) and len(items) > total:
            raise ValueError(
                f"BrewForge resource {path!r}: received more than the declared {total} items"
            )
        if not has_more:
            if isinstance(total, int) and len(items) != total:
                raise ValueError(
                    f"BrewForge resource {path!r}: expected {total} items, "
                    f"received {len(items)}"
                )
            return items
        if not data:
            raise ValueError(
                f"BrewForge resource {path!r}: no items while hasMore is true"
            )
        if isinstance(total, int) and len(items) >= total:
            raise ValueError(
                f"BrewForge resource {path!r}: hasMore is true after receiving total items"
            )
        page += 1
    raise ValueError(f"BrewForge resource {path!r}: exceeded {max_pages} pages")


def create_backup(client: ReadClient, now: datetime | None = None) -> dict[str, Any]:
    """Read supported top-level collections into a versioned snapshot object."""
    timestamp = now or datetime.now(UTC)
    resources = {
        name: _collect(client, path, require_pagination)
        for name, (path, require_pagination) in _RESOURCES.items()
    }
    return {
        "format": "forge-companion-collection-snapshot-v1",
        "created_at": timestamp.isoformat(),
        "resources": resources,
    }


def write_backup(payload: dict[str, Any], destination: Path) -> None:
    """Atomically write a collection snapshot as formatted UTF-8 JSON."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
