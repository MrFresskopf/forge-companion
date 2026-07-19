import httpx

from forge_companion.diagnostics import run_doctor


class StubClient:
    def __init__(self) -> None:
        self.paths: list[str] = []

    def get(self, path: str, params: object = None) -> dict[str, object]:
        self.paths.append(path)
        if path == "inventory/hops":
            request = httpx.Request("GET", "https://brewforge.sh/api/v1/inventory/hops")
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("broken", request=request, response=response)
        return {"data": []}


def test_doctor_checks_supported_collections_and_reports_failures() -> None:
    client = StubClient()

    checks = run_doctor(client)

    assert client.paths == [
        "brews",
        "inventory/fermentables",
        "inventory/hops",
        "inventory/yeasts",
        "inventory/miscs",
        "profiles/equipment",
        "profiles/styles",
    ]
    assert [(check.path, check.ok, check.status) for check in checks] == [
        ("brews", True, 200),
        ("inventory/fermentables", True, 200),
        ("inventory/hops", False, 500),
        ("inventory/yeasts", True, 200),
        ("inventory/miscs", True, 200),
        ("profiles/equipment", True, 200),
        ("profiles/styles", True, 200),
    ]


def test_doctor_reports_invalid_payload_and_continues() -> None:
    class InvalidPayloadClient:
        paths: list[str]

        def __init__(self) -> None:
            self.paths = []

        def get(self, path: str, params: object = None) -> dict[str, object]:
            self.paths.append(path)
            if path == "inventory/miscs":
                raise ValueError("invalid JSON")
            return {"data": []}

    client = InvalidPayloadClient()

    checks = run_doctor(client)

    misc = next(check for check in checks if check.path == "inventory/miscs")
    assert misc.ok is False
    assert misc.status is None
    assert misc.error == "invalid response: invalid JSON"
    assert client.paths[-1] == "profiles/styles"


def test_doctor_does_not_echo_token_from_transport_exception() -> None:
    token = "test-token-secret"

    class BrokenTransportClient:
        def get(self, path: str, params: object = None) -> dict[str, object]:
            raise httpx.RequestError(f"transport reflected {token}\x1b[31m")

    checks = run_doctor(BrokenTransportClient())

    assert len(checks) == 7
    assert {check.error for check in checks} == {"API request failed"}
    assert all(check.status is None and not check.ok for check in checks)
    assert all(token not in (check.error or "") for check in checks)
    assert all("\x1b" not in (check.error or "") for check in checks)
