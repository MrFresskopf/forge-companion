import json
from copy import deepcopy
from importlib.resources import files

import httpx
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from typer.main import get_command
from typer.testing import CliRunner

import forge_companion.cli_brewforge as cli
from forge_companion import credentials
from forge_companion.cli import app
from forge_companion.client import BrewForgeClient

runner = CliRunner()

_ENDPOINTS = [
    "brews",
    "profiles/equipment",
    "profiles/styles",
]


def _doctor_v2_errors(document: object) -> list[object]:
    schema = json.loads(
        files("forge_companion")
        .joinpath("schemas/doctor-v2.schema.json")
        .read_text(encoding="utf-8")
    )
    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return list(validator.iter_errors(document))


def _assert_doctor_v2_schema(document: object) -> None:
    assert _doctor_v2_errors(document) == []


def _success_document() -> dict[str, object]:
    return {
        "schema_version": "forge-companion-doctor-v2",
        "status": "ok",
        "checks": [
            {
                "path": path,
                "status": "ok",
                "http_status": 200,
                "error_code": None,
            }
            for path in _ENDPOINTS
        ],
        "error": None,
    }


def test_doctor_v2_schema_rejects_contradictory_or_extended_documents() -> None:
    wrong_status = _success_document()
    wrong_status["status"] = "failed"

    wrong_order = _success_document()
    checks = wrong_order["checks"]
    assert isinstance(checks, list)
    checks[0], checks[1] = checks[1], checks[0]

    wrong_http_tuple = _success_document()
    checks = wrong_http_tuple["checks"]
    assert isinstance(checks, list)
    checks[0] = {
        "path": "brews",
        "status": "failed",
        "http_status": 200,
        "error_code": "http_error",
    }
    wrong_http_tuple["status"] = "failed"

    nonstandard_http = _success_document()
    checks = nonstandard_http["checks"]
    assert isinstance(checks, list)
    checks[0] = {
        "path": "brews",
        "status": "failed",
        "http_status": 600,
        "error_code": "http_error",
    }
    nonstandard_http["status"] = "failed"

    setup_with_checks = deepcopy(_success_document())
    setup_with_checks["status"] = "error"
    setup_with_checks["error"] = {"code": "authentication_required"}

    unknown_field = _success_document()
    unknown_field["future"] = True

    for document in (
        wrong_status,
        wrong_order,
        wrong_http_tuple,
        nonstandard_http,
        setup_with_checks,
        unknown_field,
    ):
        assert _doctor_v2_errors(document)


class StubClient:
    def __init__(self, failed_path: str | None = None, failed_status: int = 503) -> None:
        self.failed_path = failed_path
        self.failed_status = failed_status
        self.paths: list[str] = []

    def get(self, path: str, params: object = None) -> dict[str, object]:
        self.paths.append(path)
        if path == self.failed_path:
            request = httpx.Request("GET", f"https://brewforge.sh/api/v1/{path}")
            response = httpx.Response(self.failed_status, request=request)
            raise httpx.HTTPStatusError(
                "private upstream detail", request=request, response=response
            )
        return {"data": []}


def _use_client(monkeypatch: pytest.MonkeyPatch, client: object) -> None:
    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: credentials.ResolvedToken(token="test-token", source="keyring"),
    )
    monkeypatch.setattr(cli, "BrewForgeClient", lambda token: client)


def test_doctor_json_reports_stable_success_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StubClient()
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 0
    assert client.paths == _ENDPOINTS
    assert result.output.count("\n") == 1
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document == {
        "schema_version": "forge-companion-doctor-v2",
        "status": "ok",
        "checks": [
            {
                "path": path,
                "status": "ok",
                "http_status": 200,
                "error_code": None,
            }
            for path in _ENDPOINTS
        ],
        "error": None,
    }


def test_doctor_json_reports_all_checks_and_failed_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StubClient(failed_path="profiles/equipment")
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert client.paths == _ENDPOINTS
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document["schema_version"] == "forge-companion-doctor-v2"
    assert document["status"] == "failed"
    assert document["error"] is None
    assert document["checks"][1] == {
        "path": "profiles/equipment",
        "status": "failed",
        "http_status": 503,
        "error_code": "http_error",
    }
    assert "private upstream detail" not in result.output


def test_doctor_json_schema_accepts_standard_informational_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = StubClient(failed_path="brews", failed_status=101)
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document["checks"][0] == {
        "path": "brews",
        "status": "failed",
        "http_status": 101,
        "error_code": "http_error",
    }


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("request", "request_error"),
        ("invalid", "invalid_response"),
    ],
)
def test_doctor_json_uses_fixed_codes_without_exception_details(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: str,
) -> None:
    class BrokenClient(StubClient):
        def get(self, path: str, params: object = None) -> dict[str, object]:
            self.paths.append(path)
            if failure == "request":
                request = httpx.Request("GET", f"https://brewforge.sh/api/v1/{path}")
                raise httpx.RequestError("private transport detail", request=request)
            raise ValueError("private response detail")

    client = BrokenClient()
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert client.paths == _ENDPOINTS
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document["status"] == "failed"
    assert [check["error_code"] for check in document["checks"]] == [expected_code] * 3
    assert "private" not in result.output


def test_doctor_json_classifies_deeply_nested_response_and_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    depth = 10_000
    body = '{"data":' + "[" * depth + "0" + "]" * depth + "}"
    request_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        return httpx.Response(
            200,
            content=body,
            headers={"Content-Type": "application/json"},
        )

    client = BrewForgeClient(
        "test-token",
        http=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert request_count == 3
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert {check["error_code"] for check in document["checks"]} == {"invalid_response"}
    assert "RecursionError" not in result.output


@pytest.mark.parametrize(
    ("error", "expected_code"),
    [
        (
            credentials.InvalidEnvironmentCredentialError("private environment value"),
            "invalid_environment_credential",
        ),
        (
            credentials.InvalidStoredCredentialError("private stored value"),
            "invalid_stored_credential",
        ),
        (
            credentials.CredentialStoreError("private backend detail"),
            "credential_store_error",
        ),
    ],
)
def test_doctor_json_classifies_credential_failures_without_details(
    monkeypatch: pytest.MonkeyPatch,
    error: credentials.CredentialStoreError,
    expected_code: str,
) -> None:
    def fail() -> credentials.ResolvedToken:
        raise error

    monkeypatch.setattr(credentials, "resolve_token", fail)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document == {
        "schema_version": "forge-companion-doctor-v2",
        "status": "error",
        "checks": [],
        "error": {"code": expected_code},
    }
    assert "private" not in result.output


@pytest.mark.parametrize(
    "error",
    [
        ValueError("private invalid HTTPS_PROXY value"),
        FileNotFoundError("private missing SSL_CERT_FILE path"),
        httpx.InvalidURL("private invalid HTTPS_PROXY port"),
    ],
)
def test_doctor_json_classifies_client_setup_failures_without_details(
    monkeypatch: pytest.MonkeyPatch,
    error: OSError | ValueError | httpx.InvalidURL,
) -> None:
    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: credentials.ResolvedToken(token="test-token", source="keyring"),
    )

    def fail_client_setup(token: str) -> BrewForgeClient:
        raise error

    monkeypatch.setattr(cli, "BrewForgeClient", fail_client_setup)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document == {
        "schema_version": "forge-companion-doctor-v2",
        "status": "error",
        "checks": [],
        "error": {"code": "client_setup_error"},
    }
    assert "private" not in result.output


@pytest.mark.parametrize(
    "environment",
    [
        {"HTTPS_PROXY": "://private-proxy"},
        {"HTTPS_PROXY": "http://private-host:invalid"},
        {"SSL_CERT_FILE": "Z:/private-missing-forge-companion-ca.pem"},
    ],
)
def test_doctor_json_classifies_real_client_environment_failures_without_requests(
    monkeypatch: pytest.MonkeyPatch,
    environment: dict[str, str],
) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "SSL_CERT_FILE"):
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: credentials.ResolvedToken(token="test-token", source="keyring"),
    )

    original_client = httpx.Client

    def guarded_client(*args: object, **kwargs: object) -> httpx.Client:
        client = original_client(*args, **kwargs)
        client.close()
        raise AssertionError("client setup unexpectedly succeeded before any request")

    monkeypatch.setattr(httpx, "Client", guarded_client)

    result = runner.invoke(app, ["doctor", "--json"])

    assert result.exit_code == 1
    assert result.output.count("\n") == 1
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document["error"] == {"code": "client_setup_error"}
    assert "private" not in result.output


def test_doctor_json_reports_missing_auth_without_human_prose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: credentials.ResolvedToken(token=None, source="missing"),
    )
    monkeypatch.setattr(
        cli,
        "BrewForgeClient",
        lambda token: pytest.fail("missing authentication must not construct the API client"),
    )

    result = runner.invoke(app, ["doctor", "--json"], env={"BREWFORGE_API_TOKEN": ""})

    assert result.exit_code == 2
    document = json.loads(result.output)
    _assert_doctor_v2_schema(document)
    assert document == {
        "schema_version": "forge-companion-doctor-v2",
        "status": "error",
        "checks": [],
        "error": {"code": "authentication_required"},
    }
    assert "forge-companion auth login" not in result.output


def test_doctor_keeps_human_output_as_default(monkeypatch: pytest.MonkeyPatch) -> None:
    client = StubClient()
    _use_client(monkeypatch, client)

    result = runner.invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert result.output == "".join(f"OK   {path:28} 200\n" for path in _ENDPOINTS)
    assert "schema_version" not in result.output


def test_doctor_help_lists_json_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        credentials,
        "resolve_token",
        lambda: pytest.fail("doctor help must not access credentials"),
    )

    doctor_command = get_command(app).commands["doctor"]
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert any("--json" in parameter.opts for parameter in doctor_command.params)
