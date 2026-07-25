import json
from collections.abc import Iterator

import httpx
import pytest

import forge_companion.shelly_cloud as shelly_cloud_module
from forge_companion.shelly_cloud import (
    ShellyCloudReadOnlyClient,
    ShellyCloudResponseError,
    ShellyCloudSwitchStatus,
)


def test_cloud_client_reads_only_selected_switch_status() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(
            200,
            json=[
                {
                    "id": "aabbccddeeff",
                    "type": "relay",
                    "code": "S3SW-001X16EU",
                    "gen": "G3",
                    "online": 1,
                    "status": {
                        "switch:0": {
                            "id": 0,
                            "output": False,
                            "source": "init",
                        }
                    },
                }
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="AABBCCDDEEFF",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    status = client.get_switch_status(channel=0)

    assert status == ShellyCloudSwitchStatus(
        device_id="aabbccddeeff",
        channel=0,
        online=True,
        output=False,
        source="init",
    )
    assert seen_request is not None
    assert seen_request.method == "POST"
    assert seen_request.url.host == "shelly-82-eu.shelly.cloud"
    assert seen_request.url.path == "/v2/devices/api/get"
    assert dict(seen_request.url.params) == {"auth_key": "synthetic-cloud-key"}
    assert json.loads(seen_request.content) == {
        "ids": ["aabbccddeeff"],
        "select": ["status"],
        "pick": {"status": ["switch:0"]},
    }


@pytest.mark.parametrize(
    "server",
    [
        "example.com",
        "shelly.cloud",
        "https://shelly-82-eu.shelly.cloud",
        "shelly-82-eu.shelly.cloud/private",
        "user@shelly-82-eu.shelly.cloud",
        "shelly-82-eu.shelly.cloud.evil.example",
        "shelly-82-eu.shelly.cloud:443",
    ],
)
def test_cloud_client_rejects_noncanonical_server_before_request(server: str) -> None:
    with pytest.raises(ValueError, match="invalid Shelly Cloud server"):
        ShellyCloudReadOnlyClient(
            server=server,
            device_id="aabbccddeeff",
            auth_key="synthetic-cloud-key",
            http=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )


@pytest.mark.parametrize(
    "device_id",
    ["", "aabbccddeef", "aabbccddeeff00", "aabbccddeefg", "aa:bb:cc:dd:ee:ff", "../private"],
)
def test_cloud_client_rejects_invalid_device_id_before_request(device_id: str) -> None:
    with pytest.raises(ValueError, match="invalid Shelly Cloud device ID"):
        ShellyCloudReadOnlyClient(
            server="shelly-82-eu.shelly.cloud",
            device_id=device_id,
            auth_key="synthetic-cloud-key",
            http=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )


@pytest.mark.parametrize("auth_key", ["", "   ", "key with space", "key\nwith-control"])
def test_cloud_client_rejects_invalid_auth_key_before_request(auth_key: str) -> None:
    with pytest.raises(ValueError, match="invalid Shelly Cloud auth key"):
        ShellyCloudReadOnlyClient(
            server="shelly-82-eu.shelly.cloud",
            device_id="aabbccddeeff",
            auth_key=auth_key,
            http=httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500))),
        )


@pytest.mark.parametrize("channel", [-1, True, 256, 1.5])
def test_cloud_client_rejects_invalid_channel_before_request(channel: object) -> None:
    request_sent = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_sent
        request_sent = True
        return httpx.Response(500)

    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    with pytest.raises(ValueError, match="invalid Shelly Cloud channel"):
        client.get_switch_status(channel=channel)  # type: ignore[arg-type]

    assert request_sent is False


def test_cloud_client_disables_environment_proxies_for_internal_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_options: dict[str, object] = {}

    def client_factory(**options: object) -> object:
        seen_options.update(options)
        return object()

    monkeypatch.setattr(shelly_cloud_module.httpx, "Client", client_factory)

    ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
    )

    assert seen_options["trust_env"] is False
    assert seen_options["timeout"] == 5.0


def test_cloud_client_context_closes_internal_http(monkeypatch: pytest.MonkeyPatch) -> None:
    class TrackingClient:
        def __init__(self, **options: object) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    http = TrackingClient()
    monkeypatch.setattr(shelly_cloud_module.httpx, "Client", lambda **options: http)

    with ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
    ):
        assert http.closed is False

    assert http.closed is True


def test_cloud_client_rejects_declared_oversized_response_before_body_read() -> None:
    class TrackingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.read_started = False
            self.closed = False

        def __iter__(self) -> Iterator[bytes]:
            self.read_started = True
            yield b"private oversized cloud content"

        def close(self) -> None:
            self.closed = True

    stream = TrackingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"Content-Length": "1000000"},
            stream=stream,
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)

    assert stream.read_started is False
    assert stream.closed is True


def test_cloud_client_rejects_duplicate_json_keys() -> None:
    raw_json = (
        b'[{"id":"aabbccddeeff","online":1,"status":{"switch:0":'
        b'{"id":0,"output":false,"output":true,"source":"init"}}}]'
    )

    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=raw_json,
                headers={"Content-Type": "application/json"},
            )
        )
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


def test_cloud_client_rejects_nonfinite_json_constants() -> None:
    raw_json = (
        b'[{"id":"aabbccddeeff","online":1,"private":NaN,"status":'
        b'{"switch:0":{"id":0,"output":false,"source":"init"}}}]'
    )
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, content=raw_json))
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        [],
        [
            {"id": "aabbccddeeff", "online": 1, "status": {}},
            {"id": "aabbccddeeff", "online": 1, "status": {}},
        ],
        [
            {
                "id": "112233445566",
                "online": 1,
                "status": {"switch:0": {"id": 0, "output": False, "source": "init"}},
            }
        ],
        [
            {
                "id": "aabbccddeeff",
                "online": True,
                "status": {"switch:0": {"id": 0, "output": False, "source": "init"}},
            }
        ],
        [
            {
                "id": "aabbccddeeff",
                "online": 1,
                "status": {"switch:0": {"id": 1, "output": False, "source": "init"}},
            }
        ],
        [
            {
                "id": "aabbccddeeff",
                "online": 1,
                "status": {"switch:0": {"id": 0, "output": "false", "source": "init"}},
            }
        ],
    ],
)
def test_cloud_client_rejects_invalid_or_mismatched_status_schema(payload: object) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)


def test_cloud_client_reports_offline_without_trusting_stale_output() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=[
                    {
                        "id": "aabbccddeeff",
                        "online": 0,
                        "status": {"switch:0": {"id": 0, "output": True, "source": "cloud"}},
                    }
                ],
            )
        )
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    assert client.get_switch_status(channel=0) == ShellyCloudSwitchStatus(
        device_id="aabbccddeeff",
        channel=0,
        online=False,
        output=None,
        source=None,
    )


def test_cloud_client_enforces_runtime_size_limit_without_content_length() -> None:
    class OversizedStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.closed = False

        def __iter__(self) -> Iterator[bytes]:
            yield b"x" * (64 * 1024)
            yield b"private-extra-byte"

        def close(self) -> None:
            self.closed = True

    stream = OversizedStream()
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, stream=stream)
        )
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)

    assert stream.closed is True


@pytest.mark.parametrize("content_length", ["-1", "invalid", "65537"])
def test_cloud_client_rejects_invalid_content_length_before_body_read(
    content_length: str,
) -> None:
    class TrackingStream(httpx.SyncByteStream):
        def __init__(self) -> None:
            self.read_started = False

        def __iter__(self) -> Iterator[bytes]:
            self.read_started = True
            yield b"private"

    stream = TrackingStream()
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers={"Content-Length": content_length},
                stream=stream,
            )
        )
    )
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="invalid status payload"):
        client.get_switch_status(channel=0)

    assert stream.read_started is False


def test_cloud_client_never_follows_redirects() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.example/private"})

    http = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    client = ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(httpx.HTTPStatusError):
        client.get_switch_status(channel=0)

    assert len(requests) == 1
    assert requests[0].url.host == "shelly-82-eu.shelly.cloud"


def test_cloud_client_context_does_not_close_injected_http() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(500))
    )

    with ShellyCloudReadOnlyClient(
        server="shelly-82-eu.shelly.cloud",
        device_id="aabbccddeeff",
        auth_key="synthetic-cloud-key",
        http=http,
    ):
        pass

    assert http.is_closed is False
    http.close()
