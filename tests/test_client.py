import httpx
import pytest

from forge_companion.client import BrewForgeClient
from forge_companion.fermentation import parse_readings


def test_default_client_ignores_environment_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_options: dict[str, object] = {}

    class StubHttpClient:
        def __init__(self, **options: object) -> None:
            created_options.update(options)

    monkeypatch.setattr(httpx, "Client", StubHttpClient)

    BrewForgeClient(token="secret-token")

    assert created_options == {"timeout": 20.0, "trust_env": False}


def test_get_sends_bearer_token_and_uses_api_base_url() -> None:
    seen_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_request
        seen_request = request
        return httpx.Response(200, json={"data": []})

    transport = httpx.MockTransport(handler)
    http = httpx.Client(transport=transport)
    client = BrewForgeClient(token="secret-token", http=http)

    payload = client.get("brews", params={"page": 1})

    assert payload == {"data": []}
    assert seen_request is not None
    assert str(seen_request.url) == "https://brewforge.sh/api/v1/brews?page=1"
    assert seen_request.headers["Authorization"] == "Bearer secret-token"
    assert seen_request.headers["Accept"] == "application/json"


def test_get_decodes_oversized_integer_for_record_local_rejection() -> None:
    oversized_integer = "9" * 5_000
    raw_json = (
        '{"data":['
        '{"id":"bad","timestamp":"2026-07-17T08:00:00Z","gravity":'
        f"{oversized_integer}"
        "},"
        '{"id":"good","timestamp":"2026-07-17T09:00:00Z","gravity":1}'
        "]}"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=raw_json, headers={"Content-Type": "application/json"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    payload = BrewForgeClient(token="secret-token", http=http).get("readings")

    assert isinstance(payload["data"][1]["gravity"], int)
    parsed = parse_readings(payload)
    assert [reading.id for reading in parsed.readings] == ["good"]
    assert parsed.rejected == ("reading 0: gravity must be a finite number",)


def test_get_accepts_additional_response_fields() -> None:
    payload = {
        "data": [],
        "pagination": {"hasMore": False, "total": 0, "future": "allowed"},
        "future_top_level": {"also": "allowed"},
    }

    http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))
    )

    assert BrewForgeClient(token="secret-token", http=http).get("brews") == payload


def test_get_rejects_non_object_json_response() -> None:
    http = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=[])))

    with pytest.raises(TypeError, match="JSON that is not an object"):
        BrewForgeClient(token="secret-token", http=http).get("brews")


def test_get_rejects_invalid_json_without_reflecting_response_content() -> None:
    private_content = "private-note secret-token\x1b[31m"
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=f'{{"data": ["{private_content}"]',
                headers={"Content-Type": "application/json"},
            )
        )
    )

    with pytest.raises(ValueError) as captured:
        BrewForgeClient(token="secret-token", http=http).get("brews")

    message = str(captured.value)
    assert message == "BrewForge returned invalid JSON"
    assert private_content not in message
    assert "secret-token" not in message
    assert "\x1b" not in message


def test_get_does_not_retry_rate_limit_response() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            json={"private": "do not expose"},
            headers={"Retry-After": "60"},
        )

    http = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.HTTPStatusError) as captured:
        BrewForgeClient(token="secret-token", http=http).get("brews")

    assert captured.value.response.status_code == 429
    assert calls == 1


def test_get_does_not_retry_timeout() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("reflected secret-token\x1b[31m", request=request)

    http = httpx.Client(transport=httpx.MockTransport(handler))

    with pytest.raises(httpx.ReadTimeout):
        BrewForgeClient(token="secret-token", http=http).get("brews")

    assert calls == 1
