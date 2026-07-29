"""Versioned, data-minimal machine output for BrewForge diagnostics."""

import json
from typing import Literal

from forge_companion.diagnostics import EndpointCheck

DoctorSetupErrorCode = Literal[
    "authentication_required",
    "client_setup_error",
    "credential_store_error",
    "invalid_environment_credential",
    "invalid_stored_credential",
]

SCHEMA_VERSION = "forge-companion-doctor-v1"


def build_doctor_document(
    checks: list[EndpointCheck],
    *,
    error_code: DoctorSetupErrorCode | None = None,
) -> dict[str, object]:
    """Build one closed v1 result without response bodies or exception text."""
    if error_code is not None:
        return {
            "schema_version": SCHEMA_VERSION,
            "status": "error",
            "checks": [],
            "error": {"code": error_code},
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "ok" if all(check.ok for check in checks) else "failed",
        "checks": [
            {
                "path": check.path,
                "status": "ok" if check.ok else "failed",
                "http_status": check.status,
                "error_code": check.error_code,
            }
            for check in checks
        ],
        "error": None,
    }


def render_doctor_json(document: dict[str, object]) -> str:
    """Render deterministic compact JSON suitable for stdout automation."""
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
