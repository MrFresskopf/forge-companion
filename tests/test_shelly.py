import httpx
import pytest

from forge_companion.shelly import (
    ShellyReadOnlyClient,
    ShellyResponseError,
    ShellySwitchStatus,
)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "ftp://192.0.2.1",
        "http://user:private-password@192.0.2.1",
        "http://192.0.2.1/private-path",
        "http://192.0.2.1?private=query",
        "http://192.0.2.1#private-fragment",
    ],
)
def test_read_only_client_rejects_invalid_or_ambiguous_base_url(base_url: str) -> None:
    with pytest.raises(ValueError, match="invalid Shelly base URL"):
        ShellyReadOnlyClient(base_url=base_url)


@pytest.mark.parametrize("channel", [-1, True, 256, 1.5])
def test_get_switch_status_rejects_invalid_channel_before_request(channel: object) -> None:
    request_was_sent = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_was_sent
        request_was_sent = True
        return httpx.Response(500)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ValueError, match="invalid Shelly channel"):
        client.get_switch_status(channel=channel)  # type: ignore[arg-type]

    assert request_was_sent is False


def test_get_switch_status_uses_only_documented_read_endpoint() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json={
                "id": 0,
                "source": "HTTP_in",
                "tag": None,
                "output": False,
                "counts": {
                    "on_time": 0,
                    "on_time_rst_ts": 0,
                    "switch_on": 19,
                    "switch_on_rst_ts": 0,
                },
                "temperature": {"tC": 43.2, "tF": 109.8},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    status = client.get_switch_status(channel=0)

    assert status == ShellySwitchStatus(
        channel=0,
        output=False,
        source="HTTP_in",
        switch_on_count=19,
        temperature_c=43.2,
    )
    assert seen_request is not None
    assert seen_request.method == "GET"
    assert str(seen_request.url) == "http://192.0.2.1/rpc/Switch.GetStatus?id=0"


def test_get_switch_status_never_follows_redirect_to_another_rpc() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                302,
                headers={"Location": "/rpc/Switch.Set?id=0&on=true"},
            )
        return httpx.Response(
            200,
            json={
                "id": 0,
                "source": "HTTP_in",
                "output": True,
                "counts": {"switch_on": 20},
                "temperature": {"tC": 43.5},
            },
        )

    http = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
    )
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(httpx.HTTPStatusError):
        client.get_switch_status(channel=0)

    assert len(requests) == 1
    assert requests[0].url.path == "/rpc/Switch.GetStatus"


def test_get_switch_status_rejects_response_for_different_channel() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 1,
                "source": "HTTP_in",
                "output": False,
                "counts": {"switch_on": 19},
                "temperature": {"tC": 43.2},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="channel mismatch"):
        client.get_switch_status(channel=0)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {
            "id": 0,
            "source": "HTTP_in",
            "output": "false",
            "counts": {"switch_on": 19},
            "temperature": {"tC": 43.2},
        },
        {
            "id": 0,
            "source": 7,
            "output": False,
            "counts": {"switch_on": 19},
            "temperature": {"tC": 43.2},
        },
        {
            "id": 0,
            "source": "HTTP_in",
            "output": False,
            "counts": {"switch_on": -1},
            "temperature": {"tC": 43.2},
        },
    ],
)
def test_get_switch_status_rejects_invalid_status_payload(payload: object) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


def test_get_switch_status_rejects_nonfinite_temperature() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=(
                b'{"id":0,"source":"HTTP_in","output":false,'
                b'"counts":{"switch_on":19},"temperature":{"tC":1e999}}'
            ),
            headers={"Content-Type": "application/json"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


def test_get_switch_status_rejects_temperature_that_overflows_float() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": 0,
                "source": "HTTP_in",
                "output": False,
                "counts": {"switch_on": 19},
                "temperature": {"tC": 10**400},
            },
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


def test_get_switch_status_rejects_excessively_nested_json() -> None:
    raw_json = b"[" * 10_000 + b"0" + b"]" * 10_000

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


@pytest.mark.parametrize(
    "raw_json",
    [
        b'{"id":0',
        (
            b'{"id":0,"source":"HTTP_in","output":false,"output":true,'
            b'"counts":{"switch_on":19},"temperature":{"tC":43.2}}'
        ),
    ],
)
def test_get_switch_status_rejects_invalid_or_ambiguous_json(raw_json: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=raw_json,
            headers={"Content-Type": "application/json"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyReadOnlyClient(base_url="http://192.0.2.1", http=http)

    with pytest.raises(ShellyResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)
