import httpx

from forge_companion.client import BrewForgeClient


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
