"""Small read-only client for BrewForge's public API."""

from collections.abc import Mapping
from typing import Any, Protocol

import httpx


class ReadClient(Protocol):
    """Shared interface for read-only BrewForge consumers."""

    def get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]: ...


class BrewForgeClient:
    """Access documented BrewForge GET endpoints."""

    def __init__(
        self,
        token: str,
        http: httpx.Client | None = None,
        base_url: str = "https://brewforge.sh/api/v1",
    ) -> None:
        if not token.strip():
            raise ValueError("BrewForge API token must not be empty")
        self._http = http or httpx.Client(timeout=20.0)
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        }

    def get(
        self,
        path: str,
        params: Mapping[str, str | int] | None = None,
    ) -> dict[str, Any]:
        """Fetch one API resource and return its JSON object."""
        response = self._http.get(
            f"{self._base_url}/{path.lstrip('/')}",
            params=params,
            headers=self._headers,
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise TypeError("BrewForge returned JSON that is not an object")
        return payload
