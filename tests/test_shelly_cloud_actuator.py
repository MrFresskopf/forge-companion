import json
from collections.abc import Iterator

import httpx
import pytest

from forge_companion import shelly_cloud
from forge_companion.shelly_cloud import (
    ShellyCloudActuator,
    ShellyCloudPulseResult,
    ShellyCloudResponseError,
    ShellyCloudSwitchStatus,
)


def test_cloud_actuator_sends_one_pulse_and_reads_back_the_state() -> None:
    seen: dict[str, object] = {}
    get_requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/devices/api/set/switch":
            seen["method"] = request.method
            seen["set_url"] = request.url
            seen["set_body"] = json.loads(request.content)
            return httpx.Response(200, content=b"")
        # get status
        get_requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "id": "5432046e5f58",
                    "online": 1,
                    "status": {
                        "switch:0": {"id": 0, "output": False, "source": "timer"},
                    },
                }
            ],
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    result = actuator.pulse(channel=0, toggle_after_seconds=1.0)

    # Verify the set request
    assert seen["method"] == "POST"
    set_url = seen["set_url"]
    assert isinstance(set_url, httpx.URL)
    assert set_url.scheme == "https"
    assert set_url.host == "shelly-82-eu.shelly.cloud"
    assert set_url.path == "/v2/devices/api/set/switch"
    assert dict(set_url.params) == {"auth_key": "synthetic-cloud-key"}
    assert seen["set_body"] == {
        "id": "5432046e5f58",
        "channel": 0,
        "on": True,
        "toggle_after": 1.0,
    }
    # Verify pulse result
    assert result == ShellyCloudPulseResult(
        accepted=True,
        readback=ShellyCloudSwitchStatus(
            device_id="5432046e5f58",
            channel=0,
            online=True,
            output=False,
            source="timer",
        ),
    )
    # Verify exactly one status read-back was made
    assert len(get_requests) == 1


def test_cloud_actuator_ignores_http_200_set_response_body() -> None:
    requests: list[httpx.Request] = []

    class ForbiddenSetBody(httpx.SyncByteStream):
        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("set response body must not be read")

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/set/switch"):
            return httpx.Response(
                200,
                headers={"Content-Length": str(128 * 1024)},
                stream=ForbiddenSetBody(),
            )
        return httpx.Response(
            200,
            json=[
                {
                    "id": "5432046e5f58",
                    "online": 1,
                    "status": {"switch:0": {"id": 0, "output": False, "source": "timer"}},
                }
            ],
        )

    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda seconds: None,
    )

    result = actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert result.accepted is True
    assert result.readback is not None
    assert result.readback.output is False
    assert [request.url.path for request in requests] == [
        "/v2/devices/api/set/switch",
        "/v2/devices/api/get",
    ]


@pytest.mark.parametrize(
    "toggle_after",
    [0, -1.0, True, 1.01, 31.0, "1.5", float("nan"), float("inf")],
)
def test_cloud_actuator_rejects_invalid_toggle_after(
    toggle_after: object,
) -> None:
    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"isok": True}))
        ),
    )

    with pytest.raises(ValueError, match="toggle_after"):
        actuator.pulse(channel=0, toggle_after_seconds=toggle_after)  # type: ignore[arg-type]


@pytest.mark.parametrize("channel", [1, -1, 256, True])
def test_cloud_actuator_rejects_every_channel_except_zero(channel: object) -> None:
    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"isok": True}))
        ),
    )

    with pytest.raises(ValueError, match="invalid Shelly Cloud channel"):
        actuator.pulse(channel=channel)  # type: ignore[arg-type]


def test_cloud_actuator_returns_not_accepted_when_cloud_rejects() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(202, content=b""))
    )
    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    result = actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert result == ShellyCloudPulseResult(accepted=False, readback=None)


def test_cloud_actuator_context_leaves_injected_http_open() -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"isok": True}))
    )
    with ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=http,
    ):
        assert http.is_closed is False

    assert http.is_closed is False  # injected client is not owned


def test_cloud_actuator_context_closes_proxy_isolated_internal_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    internal_http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(500)))

    def create_http(**kwargs: object) -> httpx.Client:
        created.update(kwargs)
        return internal_http

    monkeypatch.setattr(shelly_cloud.httpx, "Client", create_http)

    with ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
    ):
        assert internal_http.is_closed is False

    assert created == {"timeout": 5.0, "trust_env": False}
    assert internal_http.is_closed is True


def test_cloud_actuator_does_not_follow_set_redirect() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"Location": "https://example.invalid/redirect"})

    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert result == ShellyCloudPulseResult(accepted=False, readback=None)
    assert len(requests) == 1
    assert requests[0].url.host == "shelly-82-eu.shelly.cloud"


def test_cloud_actuator_never_retries_on_http_error() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("connection refused", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=http,
    )

    with pytest.raises(ShellyCloudResponseError, match="pulse request failed") as exc_info:
        actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert call_count == 1
    assert "synthetic-cloud-key" not in str(exc_info.value)


def test_cloud_actuator_waits_for_device_timer_before_readback() -> None:
    events: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/set/switch"):
            events.append("set")
            return httpx.Response(200, json={"isok": True})
        events.append("readback")
        return httpx.Response(
            200,
            json=[
                {
                    "id": "5432046e5f58",
                    "online": 1,
                    "status": {"switch:0": {"id": 0, "output": False, "source": "timer"}},
                }
            ],
        )

    def wait(seconds: float) -> None:
        assert seconds == 1.0
        events.append("wait")

    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=wait,
    )

    actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert events == ["set", "wait", "readback"]


def test_cloud_actuator_observes_one_request_per_second_before_readback() -> None:
    waits: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/set/switch"):
            return httpx.Response(200, json={"isok": True})
        return httpx.Response(
            200,
            json=[
                {
                    "id": "5432046e5f58",
                    "online": 1,
                    "status": {"switch:0": {"id": 0, "output": False, "source": "timer"}},
                }
            ],
        )

    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=waits.append,
    )

    actuator.pulse(channel=0, toggle_after_seconds=0.1)

    assert waits == [1.0]


def test_cloud_actuator_sanitizes_readback_transport_error_without_retry() -> None:
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        if request.url.path.endswith("/set/switch"):
            return httpx.Response(200, content=b"")
        raise httpx.ConnectError("connection refused", request=request)

    actuator = ShellyCloudActuator(
        server="shelly-82-eu.shelly.cloud",
        device_id="5432046E5F58",
        auth_key="synthetic-cloud-key",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda seconds: None,
    )

    with pytest.raises(ShellyCloudResponseError, match="read-back failed") as exc_info:
        actuator.pulse(channel=0, toggle_after_seconds=1.0)

    assert call_count == 2
    assert "synthetic-cloud-key" not in str(exc_info.value)
