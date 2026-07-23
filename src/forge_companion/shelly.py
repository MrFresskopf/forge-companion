"""Narrow read-only client for a local Shelly switch status."""

import json
import math
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx


class ShellyResponseError(ValueError):
    """Report an invalid Shelly response without reflecting device content."""


@dataclass(frozen=True)
class ShellySwitchStatus:
    """Validated status fields safe to expose to Forge Companion consumers."""

    channel: int
    output: bool
    source: str
    switch_on_count: int
    temperature_c: float


def _invalid_status() -> ShellyResponseError:
    return ShellyResponseError("Shelly returned an invalid status payload")


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise _invalid_status()
        payload[key] = value
    return payload


def _reject_json_constant(value: str) -> None:
    raise _invalid_status()


def _decode_status_json(content: bytes) -> Any:
    try:
        return json.loads(
            content,
            object_pairs_hook=_unique_json_object,
            parse_constant=_reject_json_constant,
        )
    except (json.JSONDecodeError, RecursionError, UnicodeDecodeError, ValueError):
        raise _invalid_status() from None


def _normalize_base_url(base_url: str) -> str:
    try:
        parsed = urlsplit(base_url)
        _ = parsed.port
    except ValueError:
        raise ValueError("invalid Shelly base URL") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or bool(parsed.query)
        or bool(parsed.fragment)
        or "\\" in base_url
        or any(ord(character) <= 32 or ord(character) == 127 for character in base_url)
    ):
        raise ValueError("invalid Shelly base URL")
    return f"{parsed.scheme}://{parsed.netloc}"


def _parse_switch_status(payload: object, *, channel: int) -> ShellySwitchStatus:
    if not isinstance(payload, dict):
        raise _invalid_status()
    response_channel = payload.get("id")
    if not isinstance(response_channel, int) or isinstance(response_channel, bool):
        raise _invalid_status()
    if response_channel != channel:
        raise ShellyResponseError("Shelly response channel mismatch")

    output = payload.get("output")
    source = payload.get("source")
    counts = payload.get("counts")
    temperature = payload.get("temperature")
    if type(output) is not bool or not isinstance(source, str):
        raise _invalid_status()
    if not isinstance(counts, dict) or not isinstance(temperature, dict):
        raise _invalid_status()

    switch_on_count = counts.get("switch_on")
    temperature_c = temperature.get("tC")
    if (
        not isinstance(switch_on_count, int)
        or isinstance(switch_on_count, bool)
        or switch_on_count < 0
    ):
        raise _invalid_status()
    if not isinstance(temperature_c, (int, float)) or isinstance(temperature_c, bool):
        raise _invalid_status()
    try:
        normalized_temperature_c = float(temperature_c)
    except (OverflowError, ValueError):
        raise _invalid_status() from None
    if not math.isfinite(normalized_temperature_c):
        raise _invalid_status()

    return ShellySwitchStatus(
        channel=response_channel,
        output=output,
        source=source,
        switch_on_count=switch_on_count,
        temperature_c=normalized_temperature_c,
    )


class ShellyReadOnlyClient:
    """Read switch status without exposing generic or mutating RPC methods."""

    def __init__(self, base_url: str, http: httpx.Client | None = None) -> None:
        self._base_url = _normalize_base_url(base_url)
        self._http = http or httpx.Client(timeout=5.0)

    def get_switch_status(self, channel: int = 0) -> ShellySwitchStatus:
        """Read one switch channel through Shelly's documented GET-only RPC."""
        if type(channel) is not int or not 0 <= channel <= 255:
            raise ValueError("invalid Shelly channel")
        response = self._http.get(
            f"{self._base_url}/rpc/Switch.GetStatus",
            params={"id": channel},
            headers={"Accept": "application/json"},
            follow_redirects=False,
        )
        response.raise_for_status()
        payload = _decode_status_json(response.content)
        return _parse_switch_status(payload, channel=channel)
