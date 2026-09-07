from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest

from forge_companion.rapt import (
    RaptAuthenticationError,
    RaptClient,
    RaptRequestError,
    RaptResponseError,
    RaptTransportError,
)


def test_list_hydrometers_authenticates_once_and_uses_fixed_read_only_endpoint() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "id.rapt.io":
            return httpx.Response(
                200,
                json={"access_token": "short-lived-token", "expires_in": 3600},
            )
        return httpx.Response(200, json=[{"id": "11111111-1111-1111-1111-111111111111"}])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    assert client.list_hydrometers() == [{"id": "11111111-1111-1111-1111-111111111111"}]
    assert [request.method for request in requests] == ["POST", "GET"]
    assert str(requests[0].url) == "https://id.rapt.io/connect/token"
    assert requests[0].headers["content-type"].startswith("application/x-www-form-urlencoded")
    assert requests[0].content == (
        b"client_id=rapt-user&grant_type=password&username=brewer%40example.com&password=api-secret"
    )
    assert str(requests[1].url) == "https://api.rapt.io/api/Hydrometers/GetHydrometers"
    assert requests[1].headers["authorization"] == "Bearer short-lived-token"
    assert requests[1].headers["accept"] == "application/json"


def test_list_temperature_controllers_reuses_the_access_token() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    client.list_hydrometers()
    assert client.list_temperature_controllers() == []

    assert [request.method for request in requests] == ["POST", "GET", "GET"]
    assert str(requests[2].url) == (
        "https://api.rapt.io/api/TemperatureControllers/GetTemperatureControllers"
    )


def test_hydrometer_telemetry_validates_and_sends_an_explicit_utc_window() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    payload = client.get_hydrometer_telemetry(
        device_id="11111111-1111-1111-1111-111111111111",
        start=datetime(2026, 9, 1, 8, tzinfo=UTC),
        end=datetime(2026, 9, 2, 8, tzinfo=UTC),
    )

    assert payload == []
    assert str(requests[1].url) == (
        "https://api.rapt.io/api/Hydrometers/GetTelemetry?"
        "hydrometerId=11111111-1111-1111-1111-111111111111&"
        "startDate=2026-09-01T08%3A00%3A00%2B00%3A00&"
        "endDate=2026-09-02T08%3A00%3A00%2B00%3A00"
    )


def test_temperature_controller_telemetry_uses_its_distinct_endpoint_and_parameter() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token", "expires_in": 3600})
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    client.get_temperature_controller_telemetry(
        device_id="22222222-2222-2222-2222-222222222222",
        start=datetime(2026, 9, 1, 8, tzinfo=UTC),
        end=datetime(2026, 9, 2, 8, tzinfo=UTC),
    )

    assert str(requests[1].url).startswith(
        "https://api.rapt.io/api/TemperatureControllers/GetTelemetry?"
        "temperatureControllerId=22222222-2222-2222-2222-222222222222&"
    )


def test_access_token_is_refreshed_after_its_reported_lifetime() -> None:
    now = 100.0
    issued = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.host == "id.rapt.io":
            issued += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{issued}", "expires_in": 60},
            )
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(
        username="brewer@example.com",
        api_secret="api-secret",
        http=http,
        clock=lambda: now,
    )

    client.list_hydrometers()
    now = 151.0
    client.list_hydrometers()

    assert issued == 2


def test_missing_token_lifetime_uses_the_documented_sixty_minute_fallback() -> None:
    now = 100.0
    issued = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.host == "id.rapt.io":
            issued += 1
            return httpx.Response(200, json={"access_token": f"token-{issued}"})
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(
        username="brewer@example.com",
        api_secret="api-secret",
        http=http,
        clock=lambda: now,
    )

    client.list_hydrometers()
    now = 3691.0
    client.list_hydrometers()

    assert issued == 2


def test_one_unauthorized_get_reauthenticates_and_retries_once() -> None:
    requests: list[httpx.Request] = []
    token_number = 0
    get_number = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_number, get_number
        requests.append(request)
        if request.url.host == "id.rapt.io":
            token_number += 1
            return httpx.Response(
                200,
                json={"access_token": f"token-{token_number}", "expires_in": 3600},
            )
        get_number += 1
        if get_number == 1:
            return httpx.Response(401, json={"error": "expired secret-looking-body"})
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    assert client.list_hydrometers() == []
    assert [request.method for request in requests] == ["POST", "GET", "POST", "GET"]
    assert requests[1].headers["authorization"] == "Bearer token-1"
    assert requests[3].headers["authorization"] == "Bearer token-2"


def test_second_unauthorized_get_raises_a_privacy_safe_domain_error() -> None:
    issued = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal issued
        if request.url.host == "id.rapt.io":
            issued += 1
            return httpx.Response(
                200,
                json={"access_token": f"private-token-{issued}", "expires_in": 3600},
            )
        return httpx.Response(401, text="private-response-body")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(
        username="private-user@example.com",
        api_secret="private-api-secret",
        http=http,
    )

    with pytest.raises(
        RaptAuthenticationError,
        match=r"^RAPT API authentication failed \(HTTP 401\)$",
    ) as captured:
        client.get_hydrometer_telemetry(
            device_id="11111111-1111-1111-1111-111111111111",
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 9, 2, tzinfo=UTC),
        )

    rendered = repr(captured.value)
    for private_value in (
        "private-user@example.com",
        "private-api-secret",
        "private-token-1",
        "private-token-2",
        "private-response-body",
        "11111111-1111-1111-1111-111111111111",
    ):
        assert private_value not in rendered
    assert issued == 2


def test_token_rejection_raises_a_privacy_safe_authentication_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, text="private-api-secret invalid for private-user@example.com")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(
        username="private-user@example.com",
        api_secret="private-api-secret",
        http=http,
    )

    with pytest.raises(
        RaptAuthenticationError,
        match=r"^RAPT authentication failed \(HTTP 400\)$",
    ) as captured:
        client.list_hydrometers()

    rendered = repr(captured.value)
    assert "private-user@example.com" not in rendered
    assert "private-api-secret" not in rendered


def test_api_rejection_raises_a_privacy_safe_request_error_without_retry() -> None:
    api_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal api_calls
        if request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "private-token"})
        api_calls += 1
        return httpx.Response(403, text="private-device-response")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    with pytest.raises(
        RaptRequestError,
        match=r"^RAPT API request failed \(HTTP 403\)$",
    ) as captured:
        client.get_hydrometer_telemetry(
            device_id="11111111-1111-1111-1111-111111111111",
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 9, 2, tzinfo=UTC),
        )

    assert "private-device-response" not in repr(captured.value)
    assert "11111111-1111-1111-1111-111111111111" not in repr(captured.value)
    assert api_calls == 1


def test_api_transport_failure_does_not_expose_the_device_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "private-token"})
        raise httpx.ConnectError("transport failed for private device", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(username="brewer@example.com", api_secret="api-secret", http=http)

    with pytest.raises(
        RaptTransportError,
        match=r"^RAPT API transport failed$",
    ) as captured:
        client.get_hydrometer_telemetry(
            device_id="11111111-1111-1111-1111-111111111111",
            start=datetime(2026, 9, 1, tzinfo=UTC),
            end=datetime(2026, 9, 2, tzinfo=UTC),
        )

    assert "private device" not in repr(captured.value)
    assert "11111111-1111-1111-1111-111111111111" not in repr(captured.value)


def test_authentication_transport_failure_does_not_expose_credentials() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            "transport failed for private-user@example.com private-api-secret",
            request=request,
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = RaptClient(
        username="private-user@example.com",
        api_secret="private-api-secret",
        http=http,
    )

    with pytest.raises(
        RaptTransportError,
        match=r"^RAPT authentication transport failed$",
    ) as captured:
        client.list_hydrometers()

    rendered = repr(captured.value)
    assert "private-user@example.com" not in rendered
    assert "private-api-secret" not in rendered


@pytest.mark.parametrize("authentication", [True, False])
def test_oversized_stream_is_stopped_and_closed(authentication: bool) -> None:
    class LargeStream(httpx.SyncByteStream):
        chunks = 0
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            for _ in range(10000):
                self.chunks += 1
                yield b" " * 65536

        def close(self) -> None:
            self.closed = True

    stream = LargeStream()

    def handler(request: httpx.Request) -> httpx.Response:
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptResponseError, match="response exceeds"):
            client.list_hydrometers()
    assert stream.closed
    assert stream.chunks <= (2 if authentication else 65)


@pytest.mark.parametrize("authentication", [True, False])
def test_encoded_responses_are_rejected_before_decompression(authentication: bool) -> None:
    import gzip

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(
            200,
            headers={"Content-Encoding": "gzip"},
            stream=httpx.ByteStream(gzip.compress(b" " * 100000)),
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptResponseError, match="content encoding"):
            client.list_hydrometers()


@pytest.mark.parametrize(
    ("authentication", "body"),
    [
        (True, b'{"access_token":"private-first","access_token":"private-second"}'),
        (False, b'[{"id":"private-first","id":"private-second"}]'),
        (False, b'[{"nested":{"id":1,"id":2}}]'),
    ],
)
def test_duplicate_json_keys_are_rejected(authentication: bool, body: bytes) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, content=body)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptResponseError, match="invalid JSON") as captured:
            client.list_hydrometers()
    assert "private" not in str(captured.value)


@pytest.mark.parametrize("ttl", ["NaN", "Infinity", "-Infinity", "1e999", "9" * 400])
def test_token_lifetime_must_be_finite(ttl: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, content=('{"access_token":"token","expires_in":' + ttl + "}").encode()
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptResponseError):
            client.list_hydrometers()
    assert calls == 1


@pytest.mark.parametrize("token", ["private-\u00e4", "private-\x00", "private-\x7f"])
def test_invalid_bearer_tokens_are_rejected_before_api_request(token: str) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"access_token": token})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptResponseError, match="invalid payload"):
            client.list_hydrometers()
    assert calls == 1


@pytest.mark.parametrize("authentication", [True, False])
def test_deep_json_is_a_safe_domain_failure(authentication: bool) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, content=b"[" * 2000 + b"0" + b"]" * 2000)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        # Python versions differ in how deeply the JSON decoder can recurse.
        # Either decoder rejection or schema rejection must stay a private domain error.
        with pytest.raises(RaptResponseError) as captured:
            client.list_hydrometers()
        assert str(captured.value) in {
            "RAPT authentication returned invalid JSON",
            "RAPT authentication returned an invalid payload",
            "RAPT returned invalid JSON",
            "RAPT returned an invalid collection payload",
        }


def test_injected_client_cannot_override_authentication_or_disable_timeouts() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.extensions["timeout"] == {
            "connect": 20.0,
            "read": 20.0,
            "write": 20.0,
            "pool": 20.0,
        }
        if request.url.host == "id.rapt.io":
            assert "authorization" not in request.headers
            return httpx.Response(200, json={"access_token": "token"})
        assert request.headers["authorization"] == "Bearer token"
        return httpx.Response(200, json=[])

    with httpx.Client(
        transport=httpx.MockTransport(handler), auth=("other-user", "other-secret"), timeout=None
    ) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        assert client.list_hydrometers() == []


@pytest.mark.parametrize("authentication", [True, False])
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirects_are_never_followed(authentication: bool, status: int) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(status, headers={"Location": "https://untrusted.invalid/collect"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        error = RaptAuthenticationError if authentication else RaptRequestError
        with pytest.raises(error, match=f"HTTP {status}"):
            client.list_hydrometers()
    assert len(requests) == (1 if authentication else 2)
    assert all(request.url.host in {"id.rapt.io", "api.rapt.io"} for request in requests)


def test_borrowed_http_client_remains_open_after_context_exit() -> None:
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200))) as http:
        with RaptClient(username="user", api_secret="secret", http=http):
            pass
        assert not http.is_closed


def test_owned_http_client_is_closed_after_context_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    monkeypatch.setattr("forge_companion.rapt.httpx.Client", lambda **kwargs: http)
    with RaptClient(username="user", api_secret="secret"):
        pass
    assert http.is_closed


@pytest.mark.parametrize("authentication", [True, False])
def test_stream_read_failure_is_sanitized_and_closed(authentication: bool) -> None:
    class FailingStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            yield b" "
            raise httpx.ReadError("private-user private-secret private-device")

        def close(self) -> None:
            self.closed = True

    stream = FailingStream()

    def handler(request: httpx.Request) -> httpx.Response:
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(200, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        with pytest.raises(RaptTransportError, match="transport failed") as captured:
            client.list_hydrometers()
    assert stream.closed
    assert "private" not in str(captured.value)
    assert captured.value.__suppress_context__


@pytest.mark.parametrize("authentication", [True, False])
def test_rejected_response_body_is_not_consumed(authentication: bool) -> None:
    class UnreadableStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self) -> Iterator[bytes]:
            raise AssertionError("Error body must never be consumed")
            yield b""  # pragma: no cover

        def close(self) -> None:
            self.closed = True

    stream = UnreadableStream()

    def handler(request: httpx.Request) -> httpx.Response:
        if not authentication and request.url.host == "id.rapt.io":
            return httpx.Response(200, json={"access_token": "token"})
        return httpx.Response(403, stream=stream)

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = RaptClient(username="user", api_secret="secret", http=http)
        error = RaptAuthenticationError if authentication else RaptRequestError
        with pytest.raises(error):
            client.list_hydrometers()
    assert stream.closed


def test_public_client_surface_has_only_fixed_reads_and_lifecycle_methods() -> None:
    assert {name for name in dir(RaptClient) if not name.startswith("_")} == {
        "close",
        "list_hydrometers",
        "list_temperature_controllers",
        "get_hydrometer_telemetry",
        "get_temperature_controller_telemetry",
    }
