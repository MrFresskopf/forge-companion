"""Read-only connectivity and endpoint diagnostics."""

from dataclasses import dataclass
from typing import Literal

import httpx

from forge_companion.client import ReadClient


@dataclass(frozen=True)
class EndpointCheck:
    """Result of probing one documented BrewForge collection."""

    path: str
    ok: bool
    status: int | None
    error: str | None = None
    error_code: Literal["http_error", "request_error", "invalid_response"] | None = None


_ENDPOINTS = (
    "brews",
    "inventory/fermentables",
    "inventory/hops",
    "inventory/yeasts",
    "inventory/miscs",
    "profiles/equipment",
    "profiles/styles",
)


def run_doctor(client: ReadClient) -> list[EndpointCheck]:
    """Probe all collections without modifying server state."""
    checks: list[EndpointCheck] = []
    for path in _ENDPOINTS:
        try:
            client.get(path)
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if 100 <= status <= 199 or 300 <= status <= 599:
                checks.append(
                    EndpointCheck(
                        path=path,
                        ok=False,
                        status=status,
                        error=f"HTTP {status}",
                        error_code="http_error",
                    )
                )
            else:
                checks.append(
                    EndpointCheck(
                        path=path,
                        ok=False,
                        status=None,
                        error="invalid response",
                        error_code="invalid_response",
                    )
                )
        except httpx.HTTPError:
            checks.append(
                EndpointCheck(
                    path=path,
                    ok=False,
                    status=None,
                    error="API request failed",
                    error_code="request_error",
                )
            )
        except (TypeError, ValueError, RecursionError):
            checks.append(
                EndpointCheck(
                    path=path,
                    ok=False,
                    status=None,
                    error="invalid response",
                    error_code="invalid_response",
                )
            )
        else:
            checks.append(EndpointCheck(path=path, ok=True, status=200))
    return checks
