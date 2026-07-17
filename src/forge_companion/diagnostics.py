"""Read-only connectivity and endpoint diagnostics."""

from dataclasses import dataclass

import httpx

from forge_companion.client import ReadClient


@dataclass(frozen=True)
class EndpointCheck:
    """Result of probing one documented BrewForge collection."""

    path: str
    ok: bool
    status: int | None
    error: str | None = None


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
            checks.append(
                EndpointCheck(
                    path=path,
                    ok=False,
                    status=error.response.status_code,
                    error=f"HTTP {error.response.status_code}",
                )
            )
        except httpx.HTTPError as error:
            checks.append(EndpointCheck(path=path, ok=False, status=None, error=str(error)))
        except (TypeError, ValueError) as error:
            checks.append(
                EndpointCheck(
                    path=path,
                    ok=False,
                    status=None,
                    error=f"invalid response: {error}",
                )
            )
        else:
            checks.append(EndpointCheck(path=path, ok=True, status=200))
    return checks
