"""Narrow read-only client for the public RAPT API."""

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from math import isfinite
from time import monotonic
from typing import Any
from uuid import UUID

import httpx


class RaptError(RuntimeError):
    """Base class for privacy-safe RAPT failures."""


class RaptAuthenticationError(RaptError):
    """Report rejected API authentication without reflecting request details."""


class RaptRequestError(RaptError):
    """Report a rejected API request without reflecting request details."""


class RaptTransportError(RaptError):
    """Report an API transport failure without reflecting request details."""


class RaptResponseError(RaptError):
    """Report an invalid RAPT response without reflecting its content."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


class RaptClient:
    """Read supported RAPT resources through fixed API endpoints."""

    def __init__(
        self,
        *,
        username: str,
        api_secret: str,
        http: httpx.Client | None = None,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        normalized_username = username.strip()
        normalized_secret = api_secret.strip()
        if not normalized_username or any(character.isspace() for character in normalized_username):
            raise ValueError("RAPT username must not be empty or contain whitespace")
        if not normalized_secret or any(character.isspace() for character in normalized_secret):
            raise ValueError("RAPT API secret must not be empty or contain whitespace")
        self._username = normalized_username
        self._api_secret = normalized_secret
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=20.0, trust_env=False)
        self._clock = clock
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0

    def __enter__(self) -> "RaptClient":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only an internally owned HTTP client."""
        if self._owns_http:
            self._http.close()

    @staticmethod
    def _read_bounded(response: httpx.Response, limit: int) -> bytes:
        # Rejected requests need no body; do not buffer arbitrary error pages.
        if not response.is_success:
            return b""
        if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
            raise RaptResponseError("RAPT returned unsupported content encoding")
        content = bytearray()
        for chunk in response.iter_bytes():
            if len(chunk) > limit - len(content):
                raise RaptResponseError("RAPT response exceeds the size limit")
            content.extend(chunk)
        return bytes(content)

    def _authenticate(self) -> str:
        try:
            with self._http.stream(
                "POST",
                "https://id.rapt.io/connect/token",
                data={
                    "client_id": "rapt-user",
                    "grant_type": "password",
                    "username": self._username,
                    "password": self._api_secret,
                },
                headers={"Accept": "application/json", "Accept-Encoding": "identity"},
                follow_redirects=False,
                timeout=20.0,
                auth=None,
            ) as response:
                content = self._read_bounded(response, 64 * 1024)
        except httpx.HTTPError:
            raise RaptTransportError("RAPT authentication transport failed") from None
        if not response.is_success:
            raise RaptAuthenticationError(
                f"RAPT authentication failed (HTTP {response.status_code})"
            )
        try:
            payload = json.loads(content, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise RaptResponseError("RAPT authentication returned invalid JSON") from None
        if not isinstance(payload, dict):
            raise RaptResponseError("RAPT authentication returned an invalid payload")
        token = payload.get("access_token")
        expires_in = payload.get("expires_in", 3600)
        if (
            not isinstance(token, str)
            or not token
            or any(not 33 <= ord(character) <= 126 for character in token)
            or not isinstance(expires_in, (int, float))
            or isinstance(expires_in, bool)
            or expires_in <= 0
        ):
            raise RaptResponseError("RAPT authentication returned an invalid payload")
        try:
            lifetime = float(expires_in)
            expires_at = self._clock() + max(0.0, lifetime - 10.0)
        except OverflowError:
            raise RaptResponseError("RAPT authentication returned an invalid payload") from None
        if not isfinite(lifetime) or not isfinite(expires_at):
            raise RaptResponseError("RAPT authentication returned an invalid payload")
        self._access_token = token
        self._access_token_expires_at = expires_at
        return token

    def _valid_access_token(self) -> str:
        if self._access_token is None or self._clock() >= self._access_token_expires_at:
            return self._authenticate()
        return self._access_token

    def _get_response(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> tuple[httpx.Response, bytes]:
        for attempt in range(2):
            token = self._valid_access_token()
            try:
                with self._http.stream(
                    "GET",
                    f"https://api.rapt.io{path}",
                    params=params,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                        "Accept-Encoding": "identity",
                    },
                    follow_redirects=False,
                    timeout=20.0,
                    auth=None,
                ) as response:
                    content = self._read_bounded(response, 4 * 1024 * 1024)
            except httpx.HTTPError:
                raise RaptTransportError("RAPT API transport failed") from None
            if response.status_code != 401 or attempt == 1:
                return response, content
            self._access_token = None
            self._access_token_expires_at = 0.0
        raise AssertionError("unreachable")

    def _get_list(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        response, content = self._get_response(path, params=params)
        if response.status_code == 401:
            raise RaptAuthenticationError("RAPT API authentication failed (HTTP 401)")
        if not response.is_success:
            raise RaptRequestError(f"RAPT API request failed (HTTP {response.status_code})")
        try:
            payload = json.loads(content, object_pairs_hook=_unique_object)
        except (ValueError, RecursionError):
            raise RaptResponseError("RAPT returned invalid JSON") from None
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise RaptResponseError("RAPT returned an invalid collection payload")
        return payload

    def list_hydrometers(self) -> list[dict[str, Any]]:
        """List account hydrometers through the fixed read-only endpoint."""
        return self._get_list("/api/Hydrometers/GetHydrometers")

    def list_temperature_controllers(self) -> list[dict[str, Any]]:
        """List account temperature controllers through the fixed read-only endpoint."""
        return self._get_list("/api/TemperatureControllers/GetTemperatureControllers")

    def _get_telemetry(
        self,
        *,
        path: str,
        parameter_name: str,
        device_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        canonical_device_id = str(UUID(device_id))
        if device_id != canonical_device_id:
            raise ValueError("RAPT device ID must be a canonical UUID")
        if start.tzinfo is None or end.tzinfo is None:
            raise ValueError("RAPT telemetry interval must be timezone-aware")
        utc_start = start.astimezone(UTC)
        utc_end = end.astimezone(UTC)
        if utc_start >= utc_end:
            raise ValueError("RAPT telemetry start must be before end")
        return self._get_list(
            path,
            params={
                parameter_name: canonical_device_id,
                "startDate": utc_start.isoformat(),
                "endDate": utc_end.isoformat(),
            },
        )

    def get_hydrometer_telemetry(
        self,
        *,
        device_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Read one hydrometer over an explicit UTC interval."""
        return self._get_telemetry(
            path="/api/Hydrometers/GetTelemetry",
            parameter_name="hydrometerId",
            device_id=device_id,
            start=start,
            end=end,
        )

    def get_temperature_controller_telemetry(
        self,
        *,
        device_id: str,
        start: datetime,
        end: datetime,
    ) -> list[dict[str, Any]]:
        """Read one temperature controller over an explicit UTC interval."""
        return self._get_telemetry(
            path="/api/TemperatureControllers/GetTelemetry",
            parameter_name="temperatureControllerId",
            device_id=device_id,
            start=start,
            end=end,
        )
