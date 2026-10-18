"""Narrow read-only client for Brewfather batch readings."""

from __future__ import annotations

import json
from typing import Any

import httpx


class BrewfatherError(RuntimeError):
    """Base class for privacy-safe Brewfather failures."""


class BrewfatherRequestError(BrewfatherError):
    """Report a rejected Brewfather request without reflecting response content."""

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code
        super().__init__(f"Brewfather API request failed (HTTP {status_code})")


class BrewfatherResponseError(BrewfatherError):
    """Report an invalid Brewfather response without reflecting its content."""


class BrewfatherTransportError(BrewfatherError):
    """Report an HTTP transport failure without reflecting request details."""


def _valid_opaque_id(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or normalized != value
        or any(character.isspace() for character in normalized)
        or any(character in normalized for character in "/?#%")
    ):
        raise ValueError("Brewfather batch ID is invalid")
    return normalized


class BrewfatherClient:
    """Access one documented read-only Brewfather batch-readings endpoint."""

    def __init__(
        self,
        *,
        user_id: str,
        api_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._user_id = _valid_opaque_id(user_id)
        self._api_key = _valid_opaque_id(api_key)
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=20.0, trust_env=False)

    def __enter__(self) -> BrewfatherClient:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only an internally owned client."""
        if self._owns_http:
            self._http.close()

    def get_batch_readings(self, batch_id: str) -> list[dict[str, object]]:
        """Return the raw, validated object records for one explicit Brewfather batch."""
        normalized_batch_id = _valid_opaque_id(batch_id)
        try:
            response = self._http.get(
                f"https://api.brewfather.app/v2/batches/{normalized_batch_id}/readings",
                headers={"Accept": "application/json"},
                auth=httpx.BasicAuth(self._user_id, self._api_key),
                follow_redirects=False,
                timeout=20.0,
            )
        except httpx.HTTPError:
            raise BrewfatherTransportError("Brewfather API transport failed") from None
        if response.status_code == 401:
            raise BrewfatherRequestError(401)
        if response.status_code != 200:
            raise BrewfatherRequestError(response.status_code)
        try:
            payload: Any = json.loads(response.content)
        except (TypeError, ValueError, RecursionError):
            raise BrewfatherResponseError("Brewfather returned invalid JSON") from None
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise BrewfatherResponseError("Brewfather returned an invalid readings payload")
        return [dict(item) for item in payload]
