import httpx

from forge_companion.client import BrewForgeClient
from forge_companion.fermentation import parse_readings


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
