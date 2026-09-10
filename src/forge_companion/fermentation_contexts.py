"""Strict, offline fermentation context persistence (experimental version 1)."""

import json
import os
import re
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TypeAlias
from uuid import UUID, uuid4

from forge_companion.file_io import atomic_write_text
from forge_companion.preferences import preferences_path
from forge_companion.vessels import load_vessels

SCHEMA_VERSION = "forge-companion-fermentation-contexts-v1"
MAX_CONTEXT_FILE_BYTES = 256 * 1024
MAX_CONTEXTS = 1024
MAX_PHASE_EVENTS = 64
MAX_DISPLAY_LENGTH = 120
SG_MIN = Decimal("1.000")
SG_MAX = Decimal("1.200")

Context: TypeAlias = dict[str, object]
_ROOT_FIELDS = {"schema_version", "contexts"}
_CONTEXT_FIELDS = {
    "context_id",
    "vessel_id",
    "batch_display_name",
    "original_gravity_sg",
    "expected_final_gravity_sg",
    "yeast",
    "fermentation_start_date",
    "start_instant",
    "same_day_telemetry_attribution",
    "authoritative_temperature_role",
    "source_devices",
    "status",
}
_OPTIONAL_CONTEXT_FIELDS = {"phase_history"}
_SOURCE_FIELDS = {"hydrometer", "temperature_controller"}
_PHASE_EVENT_FIELDS = {"phase", "start_date"}
_PHASES = {"fermentation", "cold-crash"}
_VESSEL_ID = re.compile(r"[a-z0-9](?:[a-z0-9_-]{0,63})\Z")
_SG = re.compile(r"1\.\d{3}\Z")
_MULTILINE_CHARACTERS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


class FermentationContextValidationError(ValueError):
    """Report invalid context input or persisted context data."""


class FermentationContextBusyError(RuntimeError):
    """Report that another process is changing fermentation contexts."""


def fermentation_contexts_path() -> Path:
    """Return the fixed platform-native context path."""
    return preferences_path().with_name("fermentation-contexts.json")


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise FermentationContextValidationError("Duplicate JSON object key.")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise FermentationContextValidationError(f"Invalid JSON numeric literal: {value}.")


def _display(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise FermentationContextValidationError(f"{field} must be text.")
    if not value or len(value) > MAX_DISPLAY_LENGTH or value != value.strip():
        raise FermentationContextValidationError(f"{field} is invalid.")
    if any(
        character in _MULTILINE_CHARACTERS or unicodedata.category(character).startswith("C")
        for character in value
    ):
        raise FermentationContextValidationError(
            f"{field} contains control or multiline characters."
        )
    return value


def _vessel(value: object) -> str:
    if not isinstance(value, str) or _VESSEL_ID.fullmatch(value) is None:
        raise FermentationContextValidationError("Vessel ID is invalid.")
    return value


def _uuid(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise FermentationContextValidationError(f"{field} must be a UUID string.")
    try:
        parsed = UUID(value)
    except (AttributeError, ValueError):
        raise FermentationContextValidationError(f"{field} is not a UUID.") from None
    if str(parsed) != value:
        raise FermentationContextValidationError(f"{field} must be a canonical lowercase UUID.")
    return value


def _sg(value: object, field: str) -> str:
    if not isinstance(value, str) or _SG.fullmatch(value) is None:
        raise FermentationContextValidationError(f"{field} must be a decimal in 1.xxx form.")
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise FermentationContextValidationError(f"{field} is invalid.") from None
    if not parsed.is_finite() or not SG_MIN <= parsed <= SG_MAX:
        raise FermentationContextValidationError(f"{field} must be between {SG_MIN} and {SG_MAX}.")
    return value


def _calendar_date(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise FermentationContextValidationError("Start date must use YYYY-MM-DD.")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise FermentationContextValidationError("Start date is not a calendar date.") from None
    if parsed.isoformat() != value:
        raise FermentationContextValidationError("Start date must use YYYY-MM-DD.")
    return value


def _validate_phase_history(value: object, fermentation_start_date: str) -> list[Context]:
    if not isinstance(value, list):
        raise FermentationContextValidationError("Phase history must be a list.")
    if len(value) > MAX_PHASE_EVENTS:
        raise FermentationContextValidationError("Phase history contains too many events.")
    events: list[Context] = []
    previous_phase = "fermentation"
    previous_date = date.fromisoformat(fermentation_start_date)
    for raw_event in value:
        if not isinstance(raw_event, dict) or set(raw_event) != _PHASE_EVENT_FIELDS:
            raise FermentationContextValidationError("Phase event has an unsupported shape.")
        phase = raw_event["phase"]
        if not isinstance(phase, str) or phase not in _PHASES:
            raise FermentationContextValidationError("Phase is not supported.")
        start_date = _calendar_date(raw_event["start_date"])
        parsed_date = date.fromisoformat(start_date)
        if parsed_date > date.today():
            raise FermentationContextValidationError("Phase start date cannot be in the future.")
        if parsed_date == previous_date:
            raise FermentationContextValidationError(
                "Phase transitions on the same day are ambiguous."
            )
        if parsed_date < previous_date:
            raise FermentationContextValidationError(
                "Phase start date must be after the previous recorded phase."
            )
        if phase == previous_phase:
            raise FermentationContextValidationError("Phase transition is a no-op.")
        if previous_phase == "cold-crash" and phase == "fermentation":
            raise FermentationContextValidationError("Reverse phase transition is not supported.")
        events.append({"phase": phase, "start_date": start_date})
        previous_phase = phase
        previous_date = parsed_date
    return events


def _validate_context(value: object) -> Context:
    if (
        not isinstance(value, dict)
        or not set(value) >= _CONTEXT_FIELDS
        or not set(value) <= _CONTEXT_FIELDS | _OPTIONAL_CONTEXT_FIELDS
    ):
        raise FermentationContextValidationError("Context has an unsupported shape.")
    sources = value["source_devices"]
    if not isinstance(sources, dict) or set(sources) != _SOURCE_FIELDS:
        raise FermentationContextValidationError("Context source devices are invalid.")
    original = _sg(value["original_gravity_sg"], "Original gravity")
    expected = _sg(value["expected_final_gravity_sg"], "Expected final gravity")
    if Decimal(original) <= Decimal(expected):
        raise FermentationContextValidationError(
            "Original gravity must exceed expected final gravity."
        )
    role = value["authoritative_temperature_role"]
    if not isinstance(role, str) or role not in {"hydrometer", "controller"}:
        raise FermentationContextValidationError("Authoritative temperature role is invalid.")
    status = value["status"]
    if not isinstance(status, str) or status not in {"active", "closed"}:
        raise FermentationContextValidationError("Context status is invalid.")
    if value["start_instant"] is not None:
        raise FermentationContextValidationError(
            "Date-only context cannot contain a start instant."
        )
    if value["same_day_telemetry_attribution"] != "unavailable":
        raise FermentationContextValidationError("Same-day attribution must be unavailable.")
    hydrometer = _uuid(sources["hydrometer"], "Hydrometer ID")
    controller = _uuid(sources["temperature_controller"], "Temperature controller ID")
    if hydrometer == controller:
        raise FermentationContextValidationError("Context source devices must be distinct.")
    fermentation_start_date = _calendar_date(value["fermentation_start_date"])
    result: Context = {
        "context_id": _uuid(value["context_id"], "Context ID"),
        "vessel_id": _vessel(value["vessel_id"]),
        "batch_display_name": _display(value["batch_display_name"], "Batch display name"),
        "original_gravity_sg": original,
        "expected_final_gravity_sg": expected,
        "yeast": _display(value["yeast"], "Yeast"),
        "fermentation_start_date": fermentation_start_date,
        "start_instant": None,
        "same_day_telemetry_attribution": "unavailable",
        "authoritative_temperature_role": role,
        "source_devices": {
            "hydrometer": hydrometer,
            "temperature_controller": controller,
        },
        "status": status,
    }
    if "phase_history" in value:
        result["phase_history"] = _validate_phase_history(
            value["phase_history"], fermentation_start_date
        )
    return result


def context_phase_history(context: Context) -> list[Context]:
    """Return the derived fermentation event followed by explicit transitions."""
    validated = _validate_context(context)
    explicit = validated.get("phase_history", [])
    if not isinstance(explicit, list):
        raise FermentationContextValidationError("Phase history must be a list.")
    return [
        {
            "phase": "fermentation",
            "start_date": validated["fermentation_start_date"],
        },
        *explicit,
    ]


def _validate_payload(payload: object) -> list[Context]:
    if not isinstance(payload, dict) or set(payload) != _ROOT_FIELDS:
        raise FermentationContextValidationError("Context file has an unsupported shape.")
    raw = payload["contexts"]
    if payload["schema_version"] != SCHEMA_VERSION or not isinstance(raw, list):
        raise FermentationContextValidationError("Context file has an unsupported schema.")
    if len(raw) > MAX_CONTEXTS:
        raise FermentationContextValidationError("Context file contains too many records.")
    contexts = [_validate_context(item) for item in raw]
    ids: set[str] = set()
    active_vessels: set[str] = set()
    for item in contexts:
        context_id = str(item["context_id"])
        vessel_id = str(item["vessel_id"])
        if context_id in ids:
            raise FermentationContextValidationError("Context IDs must be unique.")
        if item["status"] == "active" and vessel_id in active_vessels:
            raise FermentationContextValidationError("A vessel has multiple active contexts.")
        ids.add(context_id)
        if item["status"] == "active":
            active_vessels.add(vessel_id)
    return contexts


def load_fermentation_contexts() -> list[Context]:
    """Load strictly validated contexts, or return an empty absent store."""
    source = fermentation_contexts_path()
    try:
        with source.open("rb") as handle:
            content = handle.read(MAX_CONTEXT_FILE_BYTES + 1)
    except FileNotFoundError:
        return []
    if len(content) > MAX_CONTEXT_FILE_BYTES:
        raise FermentationContextValidationError("Context file is too large.")
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, json.JSONDecodeError, RecursionError):
        raise FermentationContextValidationError("Context file is invalid or unreadable.") from None
    return _validate_payload(payload)


@contextmanager
def _contexts_lock() -> Iterator[None]:
    destination = fermentation_contexts_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.with_name(f".{destination.name}.lock")
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise FermentationContextBusyError("Fermentation contexts are busy or locked.") from None
    try:
        os.close(descriptor)
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def _write_contexts(contexts: list[Context]) -> None:
    validated = _validate_payload({"schema_version": SCHEMA_VERSION, "contexts": contexts})
    content = json.dumps(
        {"schema_version": SCHEMA_VERSION, "contexts": validated},
        ensure_ascii=False,
        sort_keys=True,
        allow_nan=False,
    )
    if len(content.encode("utf-8")) + 1 > MAX_CONTEXT_FILE_BYTES:
        raise FermentationContextValidationError("Context file would be too large.")
    atomic_write_text(content + "\n", fermentation_contexts_path(), newline="\n")


def start_fermentation_context(
    *,
    vessel_id: str,
    batch_display_name: str,
    original_gravity_sg: str,
    expected_final_gravity_sg: str,
    yeast: str,
    fermentation_start_date: str,
    authoritative_temperature_role: str,
    switch: bool = False,
) -> Context:
    """Start an offline context, snapshotting the current vessel device binding."""
    if not isinstance(switch, bool):
        raise FermentationContextValidationError("Switch must be a boolean.")
    vessel_id = _vessel(vessel_id)
    candidate_inputs = {
        "batch_display_name": _display(batch_display_name, "Batch display name"),
        "original_gravity_sg": _sg(original_gravity_sg, "Original gravity"),
        "expected_final_gravity_sg": _sg(expected_final_gravity_sg, "Expected final gravity"),
        "yeast": _display(yeast, "Yeast"),
        "fermentation_start_date": _calendar_date(fermentation_start_date),
    }
    if Decimal(str(candidate_inputs["original_gravity_sg"])) <= Decimal(
        str(candidate_inputs["expected_final_gravity_sg"])
    ):
        raise FermentationContextValidationError(
            "Original gravity must exceed expected final gravity."
        )
    if authoritative_temperature_role not in {"hydrometer", "controller"}:
        raise FermentationContextValidationError("Authoritative temperature role is invalid.")

    with _contexts_lock():
        contexts = load_fermentation_contexts()
        binding = next((item for item in load_vessels() if item["vessel_id"] == vessel_id), None)
        if binding is None:
            raise FermentationContextValidationError(
                "Vessel must be bound before starting a context."
            )
        active = next(
            (
                item
                for item in contexts
                if item["vessel_id"] == vessel_id and item["status"] == "active"
            ),
            None,
        )
        if active is not None and not switch:
            raise FermentationContextValidationError(
                "Vessel already has an active context; close it or use switch explicitly."
            )
        if len(contexts) >= MAX_CONTEXTS:
            raise FermentationContextValidationError("Context record limit reached.")
        if active is not None:
            active["status"] = "closed"
        candidate: Context = {
            "context_id": str(uuid4()),
            "vessel_id": vessel_id,
            **candidate_inputs,
            "start_instant": None,
            "same_day_telemetry_attribution": "unavailable",
            "authoritative_temperature_role": authoritative_temperature_role,
            "source_devices": {
                "hydrometer": binding["hydrometer"],
                "temperature_controller": binding["temperature_controller"],
            },
            "status": "active",
        }
        candidate = _validate_context(candidate)
        _write_contexts([*contexts, candidate])
        return candidate


def close_fermentation_context(vessel_id: str) -> Context:
    """Close and retain the active context for one vessel."""
    vessel_id = _vessel(vessel_id)
    with _contexts_lock():
        contexts = load_fermentation_contexts()
        active = next(
            (
                item
                for item in contexts
                if item["vessel_id"] == vessel_id and item["status"] == "active"
            ),
            None,
        )
        if active is None:
            raise FermentationContextValidationError("Vessel has no active context.")
        active["status"] = "closed"
        _write_contexts(contexts)
        return active


def append_fermentation_phase(*, vessel_id: str, phase: str, start_date: str) -> Context:
    """Append an explicit date-only phase transition to one active context."""
    vessel_id = _vessel(vessel_id)
    if not isinstance(phase, str) or phase not in _PHASES:
        raise FermentationContextValidationError("Phase is not supported.")
    start_date = _calendar_date(start_date)
    if date.fromisoformat(start_date) > date.today():
        raise FermentationContextValidationError("Phase start date cannot be in the future.")
    with _contexts_lock():
        contexts = load_fermentation_contexts()
        active = next(
            (
                item
                for item in contexts
                if item["vessel_id"] == vessel_id and item["status"] == "active"
            ),
            None,
        )
        if active is None:
            raise FermentationContextValidationError("Vessel has no active context.")
        stored_history = active.get("phase_history", [])
        if not isinstance(stored_history, list):
            raise FermentationContextValidationError("Phase history must be a list.")
        explicit = list(stored_history)
        explicit.append({"phase": phase, "start_date": start_date})
        candidate = {**active, "phase_history": explicit}
        candidate = _validate_context(candidate)
        contexts[contexts.index(active)] = candidate
        _write_contexts(contexts)
        return candidate


def context_binding_status(context: Context, bindings: list[dict[str, str]]) -> str:
    """Return current, changed, or missing without substituting new source IDs."""
    binding = next((item for item in bindings if item["vessel_id"] == context["vessel_id"]), None)
    if binding is None:
        return "missing"
    sources = context["source_devices"]
    if not isinstance(sources, dict):
        raise FermentationContextValidationError("Context source devices are invalid.")
    return (
        "current"
        if sources
        == {
            "hydrometer": binding["hydrometer"],
            "temperature_controller": binding["temperature_controller"],
        }
        else "changed"
    )
