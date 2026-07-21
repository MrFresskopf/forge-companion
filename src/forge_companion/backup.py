"""Create portable, read-only snapshots of supported BrewForge collections."""

import json
import os
import tempfile
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Any

from forge_companion import __version__
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
_EXCLUDED = [
    "brew_details",
    "brew_notes",
    "brew_readings",
    "undocumented_resources",
]
_FORMAT = "forge-companion-collection-snapshot-v2"
_CANONICALIZATION = "json-sort-keys-compact-utf8-without-digest"


@dataclass(frozen=True)
class SnapshotSummary:
    """Validated snapshot metadata that never includes collection records."""

    format: str
    created_at: str
    generator_version: str
    collection_count: int
    record_count: int
    digest: str


class SnapshotValidationError(ValueError):
    """Report an invalid or unverifiable local collection snapshot."""


def _snapshot_digest(payload: dict[str, Any]) -> str:
    unsigned = deepcopy(payload)
    unsigned["manifest"]["integrity"].pop("digest", None)
    canonical = json.dumps(
        unsigned,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def validate_backup(payload: dict[str, Any]) -> SnapshotSummary:
    """Validate a v2 collection snapshot and return data-free metadata."""
    if payload.get("format") != _FORMAT:
        raise SnapshotValidationError("Snapshot has an unsupported format.")
    if set(payload) != {"format", "created_at", "manifest", "resources"}:
        raise SnapshotValidationError("Snapshot schema validation failed.")

    created_at = payload["created_at"]
    manifest = payload["manifest"]
    resources = payload["resources"]
    if (
        not isinstance(created_at, str)
        or not isinstance(manifest, dict)
        or not isinstance(resources, dict)
    ):
        raise SnapshotValidationError("Snapshot schema validation failed.")
    try:
        timestamp = datetime.fromisoformat(created_at)
    except ValueError:
        raise SnapshotValidationError("Snapshot schema validation failed.") from None
    if timestamp.tzinfo is None or timestamp.utcoffset() != UTC.utcoffset(timestamp):
        raise SnapshotValidationError("Snapshot schema validation failed.")

    if set(manifest) != {"generator", "collections", "excluded", "integrity"}:
        raise SnapshotValidationError("Snapshot schema validation failed.")
    generator = manifest["generator"]
    collections = manifest["collections"]
    excluded = manifest["excluded"]
    integrity = manifest["integrity"]
    if not all(isinstance(value, dict) for value in (generator, collections, integrity)):
        raise SnapshotValidationError("Snapshot schema validation failed.")
    if set(generator) != {"name", "version"} or generator.get("name") != "forge-companion":
        raise SnapshotValidationError("Snapshot schema validation failed.")
    generator_version = generator.get("version")
    if not isinstance(generator_version, str) or not generator_version:
        raise SnapshotValidationError("Snapshot schema validation failed.")
    if excluded != _EXCLUDED:
        raise SnapshotValidationError("Snapshot schema validation failed.")

    resource_names = set(_RESOURCES)
    if set(resources) != resource_names or set(collections) != resource_names:
        raise SnapshotValidationError("Snapshot schema validation failed.")
    for name in _RESOURCES:
        records = resources[name]
        count = collections[name]
        if (
            not isinstance(records, list)
            or any(not isinstance(record, dict) for record in records)
            or not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
            or count != len(records)
        ):
            raise SnapshotValidationError("Snapshot schema validation failed.")

    if set(integrity) != {"algorithm", "canonicalization", "digest"}:
        raise SnapshotValidationError("Snapshot schema validation failed.")
    if (
        integrity.get("algorithm") != "sha256"
        or integrity.get("canonicalization") != _CANONICALIZATION
    ):
        raise SnapshotValidationError("Snapshot schema validation failed.")
    digest = integrity.get("digest")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise SnapshotValidationError("Snapshot schema validation failed.")
    try:
        expected_digest = _snapshot_digest(payload)
    except (KeyError, TypeError, ValueError):
        raise SnapshotValidationError("Snapshot schema validation failed.") from None
    if not compare_digest(digest, expected_digest):
        raise SnapshotValidationError("Snapshot integrity check failed.")
    return SnapshotSummary(
        format=_FORMAT,
        created_at=created_at,
        generator_version=generator_version,
        collection_count=len(collections),
        record_count=sum(collections.values()),
        digest=digest,
    )


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"invalid JSON numeric literal: {value}")


def _load_strict_json_object(source: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            source.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (OSError, UnicodeError, ValueError, RecursionError):
        raise SnapshotValidationError("Snapshot is not readable strict JSON.") from None
    if not isinstance(payload, dict):
        raise SnapshotValidationError("Snapshot schema validation failed.")
    return payload


def load_snapshot_file(
    source: Path,
    *,
    allow_legacy_v1: bool = False,
) -> dict[str, Any]:
    """Load a strict v2 snapshot or an explicitly allowed legacy v1 snapshot."""
    payload = _load_strict_json_object(source)
    snapshot_format = payload.get("format")
    if snapshot_format == _FORMAT:
        validate_backup(payload)
    elif allow_legacy_v1 and snapshot_format == "forge-companion-collection-snapshot-v1":
        if not isinstance(payload.get("resources"), dict):
            raise SnapshotValidationError("Snapshot schema validation failed.")
    else:
        raise SnapshotValidationError("Snapshot has an unsupported format.")
    return payload


def validate_backup_file(source: Path) -> SnapshotSummary:
    """Read and validate one local collection snapshot without network access."""
    return validate_backup(_load_strict_json_object(source))


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
        if any(not isinstance(record, dict) for record in data):
            raise TypeError(f"BrewForge resource {path!r}: collection record is not an object")
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
                    f"BrewForge resource {path!r}: expected {total} items, received {len(items)}"
                )
            return items
        if not data:
            raise ValueError(f"BrewForge resource {path!r}: no items while hasMore is true")
        if isinstance(total, int) and len(items) >= total:
            raise ValueError(
                f"BrewForge resource {path!r}: hasMore is true after receiving total items"
            )
        page += 1
    raise ValueError(f"BrewForge resource {path!r}: exceeded {max_pages} pages")


def create_backup(client: ReadClient, now: datetime | None = None) -> dict[str, Any]:
    """Read supported top-level collections into a versioned snapshot object."""
    timestamp = now or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Snapshot creation time must be timezone-aware.")
    timestamp = timestamp.astimezone(UTC)
    resources = {
        name: _collect(client, path, require_pagination)
        for name, (path, require_pagination) in _RESOURCES.items()
    }
    payload: dict[str, Any] = {
        "format": _FORMAT,
        "created_at": timestamp.isoformat(),
        "manifest": {
            "generator": {"name": "forge-companion", "version": __version__},
            "collections": {name: len(items) for name, items in resources.items()},
            "excluded": list(_EXCLUDED),
            "integrity": {
                "algorithm": "sha256",
                "canonicalization": _CANONICALIZATION,
            },
        },
        "resources": resources,
    }
    payload["manifest"]["integrity"]["digest"] = _snapshot_digest(payload)
    return payload


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
            handle.write(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(destination)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
