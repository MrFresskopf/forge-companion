"""Narrow read-only client and actuator for Shelly Cloud v2."""

import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Any

import httpx

_MAX_CLOUD_STATUS_RESPONSE_BYTES = 64 * 1024
CLOUD_REQUEST_INTERVAL_SECONDS = 1.25
_SHELLY_CLOUD_SERVER_PATTERN = re.compile(
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+shelly\.cloud"
)
_SHELLY_DEVICE_ID_PATTERN = re.compile(r"[0-9a-f]{12}")


def normalize_cloud_server(server: str) -> str:
    """Validate and normalize a Shelly Cloud tenant hostname."""
    normalized = server.lower()
    if not _SHELLY_CLOUD_SERVER_PATTERN.fullmatch(normalized):
        raise ValueError("invalid Shelly Cloud server")
    return normalized


def normalize_cloud_device_id(device_id: str) -> str:
    """Validate and normalize one Shelly hardware device ID."""
    normalized = device_id.lower()
    if not _SHELLY_DEVICE_ID_PATTERN.fullmatch(normalized):
        raise ValueError("invalid Shelly Cloud device ID")
    return normalized


def normalize_cloud_auth_key(auth_key: str) -> str:
    """Validate a cloud key without exposing it."""
    normalized = auth_key.strip()
    if not normalized or any(character.isspace() for character in normalized):
        raise ValueError("invalid Shelly Cloud auth key")
    return normalized


@dataclass(frozen=True)
class ShellyCloudSwitchStatus:
    """Validated cloud status fields safe for consumers."""

    device_id: str
    channel: int
    online: bool
    output: bool | None
    source: str | None


@dataclass(frozen=True)
class ShellyCloudPulseResult:
    """Result of one cloud actuator pulse request."""

    accepted: bool
    readback: ShellyCloudSwitchStatus | None


class ShellyCloudResponseError(ValueError):
    """Report an invalid cloud response without reflecting its content."""


def _invalid_cloud_status() -> ShellyCloudResponseError:
    return ShellyCloudResponseError("Shelly Cloud returned an invalid status payload")


def _read_cloud_status_content(response: httpx.Response) -> bytes:
    declared_length = response.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError:
            raise _invalid_cloud_status() from None
        if not 0 <= parsed_length <= _MAX_CLOUD_STATUS_RESPONSE_BYTES:
            raise _invalid_cloud_status()

    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > _MAX_CLOUD_STATUS_RESPONSE_BYTES:
            raise _invalid_cloud_status()
        content.extend(chunk)
    return bytes(content)


def _decode_cloud_status_json(content: bytes) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in pairs:
            if key in payload:
                raise _invalid_cloud_status()
            payload[key] = value
        return payload

    def reject_constant(value: str) -> None:
        raise _invalid_cloud_status()

    try:
        return json.loads(
            content,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError, ValueError):
        raise _invalid_cloud_status() from None


def _parse_cloud_switch_status(
    payload: object,
    *,
    device_id: str,
    channel: int,
) -> ShellyCloudSwitchStatus:
    if not isinstance(payload, list) or len(payload) != 1:
        raise _invalid_cloud_status()
    device = payload[0]
    if not isinstance(device, dict) or device.get("id") != device_id:
        raise _invalid_cloud_status()
    online = device.get("online")
    if not isinstance(online, int) or isinstance(online, bool) or online not in {0, 1}:
        raise _invalid_cloud_status()
    if online == 0:
        return ShellyCloudSwitchStatus(
            device_id=device_id,
            channel=channel,
            online=False,
            output=None,
            source=None,
        )
    status_payload = device.get("status")
    if not isinstance(status_payload, dict):
        raise _invalid_cloud_status()
    switch = status_payload.get(f"switch:{channel}")
    if not isinstance(switch, dict):
        raise _invalid_cloud_status()
    response_channel = switch.get("id")
    output = switch.get("output")
    source = switch.get("source")
    if (
        not isinstance(response_channel, int)
        or isinstance(response_channel, bool)
        or response_channel != channel
        or type(output) is not bool
        or not isinstance(source, str)
    ):
        raise _invalid_cloud_status()
    return ShellyCloudSwitchStatus(
        device_id=device_id,
        channel=channel,
        online=True,
        output=output,
        source=source,
    )


class ShellyCloudReadOnlyClient:
    """Read one Shelly switch through the fixed Cloud v2 status endpoint."""

    def __init__(
        self,
        *,
        server: str,
        device_id: str,
        auth_key: str,
        http: httpx.Client | None = None,
    ) -> None:
        self._base_url = f"https://{normalize_cloud_server(server)}"
        self._device_id = normalize_cloud_device_id(device_id)
        self._auth_key = normalize_cloud_auth_key(auth_key)
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=5.0, trust_env=False)

    def __enter__(self) -> "ShellyCloudReadOnlyClient":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only an internally owned HTTP client."""
        if self._owns_http:
            self._http.close()

    def get_switch_status(self, channel: int = 0) -> ShellyCloudSwitchStatus:
        """Read one selected switch component without exposing a generic cloud API."""
        if type(channel) is not int or not 0 <= channel <= 255:
            raise ValueError("invalid Shelly Cloud channel")
        with self._http.stream(
            "POST",
            f"{self._base_url}/v2/devices/api/get",
            params={"auth_key": self._auth_key},
            json={
                "ids": [self._device_id],
                "select": ["status"],
                "pick": {"status": [f"switch:{channel}"]},
            },
            headers={"Accept": "application/json"},
            follow_redirects=False,
        ) as response:
            response.raise_for_status()
            content = _read_cloud_status_content(response)
        payload = _decode_cloud_status_json(content)
        return _parse_cloud_switch_status(
            payload,
            device_id=self._device_id,
            channel=channel,
        )


class ShellyCloudActuator:
    """Send one switch pulse through the Shelly Cloud Control API v2."""

    def __init__(
        self,
        *,
        server: str,
        device_id: str,
        auth_key: str,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = f"https://{normalize_cloud_server(server)}"
        self._device_id = normalize_cloud_device_id(device_id)
        self._auth_key = normalize_cloud_auth_key(auth_key)
        self._owns_http = http is None
        self._http = http if http is not None else httpx.Client(timeout=5.0, trust_env=False)
        self._sleep = sleep

    def __enter__(self) -> "ShellyCloudActuator":
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def close(self) -> None:
        """Close only an internally owned HTTP client."""
        if self._owns_http:
            self._http.close()

    def _read_status(self, channel: int = 0) -> ShellyCloudSwitchStatus:
        """Read one switch state through the cloud status endpoint."""
        try:
            with self._http.stream(
                "POST",
                f"{self._base_url}/v2/devices/api/get",
                params={"auth_key": self._auth_key},
                json={
                    "ids": [self._device_id],
                    "select": ["status"],
                    "pick": {"status": [f"switch:{channel}"]},
                },
                headers={"Accept": "application/json"},
                follow_redirects=False,
            ) as response:
                response.raise_for_status()
                content = _read_cloud_status_content(response)
        except httpx.HTTPError:
            raise ShellyCloudResponseError("Shelly Cloud pulse read-back failed") from None
        payload = _decode_cloud_status_json(content)
        return _parse_cloud_switch_status(
            payload,
            device_id=self._device_id,
            channel=channel,
        )

    def pulse(self, channel: int = 0, toggle_after_seconds: float = 1.0) -> ShellyCloudPulseResult:
        """Send exactly one channel-0 ON pulse with auto-off. Never retries."""
        if type(channel) is not int or channel != 0:
            raise ValueError("invalid Shelly Cloud channel")
        if (
            isinstance(toggle_after_seconds, bool)
            or not isinstance(toggle_after_seconds, (int, float))
            or not isfinite(float(toggle_after_seconds))
            or toggle_after_seconds <= 0
            or toggle_after_seconds > 1.0
        ):
            raise ValueError("toggle_after_seconds must be a positive float up to 1.0")
        try:
            with self._http.stream(
                "POST",
                f"{self._base_url}/v2/devices/api/set/switch",
                params={"auth_key": self._auth_key},
                json={
                    "id": self._device_id,
                    "channel": channel,
                    "on": True,
                    "toggle_after": toggle_after_seconds,
                },
                headers={"Accept": "application/json"},
                follow_redirects=False,
            ) as response:
                accepted = response.status_code == 200
        except httpx.HTTPError:
            raise ShellyCloudResponseError("Shelly Cloud pulse request failed") from None
        if not accepted:
            return ShellyCloudPulseResult(accepted=False, readback=None)
        self._sleep(max(CLOUD_REQUEST_INTERVAL_SECONDS, float(toggle_after_seconds)))
        readback = self._read_status(channel=channel)
        return ShellyCloudPulseResult(accepted=True, readback=readback)
