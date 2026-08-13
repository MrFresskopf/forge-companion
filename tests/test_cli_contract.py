import json
from pathlib import Path
from typing import Any

from click import unstyle
from typer.main import get_command

from forge_companion.cli import app

CONTRACT_PATH = Path("src/forge_companion/contracts/cli-v1-contract.json")
_IGNORED_FRAMEWORK_PARAMETERS = {"help", "install_completion", "show_completion"}


def _normalized_default(value: object) -> object:
    if isinstance(value, Path):
        return value.as_posix()
    return value


def _parameter_type(parameter: object) -> dict[str, object]:
    parameter_type = parameter.type
    name = getattr(parameter_type, "name", "")
    if name == "boolean":
        return {"kind": "boolean"}
    if name == "str":
        return {"kind": "string"}
    if name == "path":
        return {"kind": "path"}
    if name == "float":
        return {"kind": "number"}
    if name in {"int", "int range"}:
        result: dict[str, object] = {"kind": "integer"}
        minimum = getattr(parameter_type, "min", None)
        maximum = getattr(parameter_type, "max", None)
        if minimum is not None:
            result["minimum"] = minimum
        if maximum is not None:
            result["maximum"] = maximum
        return result
    raise AssertionError(f"unsupported CLI parameter type: {name}")


def _surface() -> dict[str, dict[str, Any]]:
    surface: dict[str, dict[str, Any]] = {}

    def visit(command: object, path: tuple[str, ...]) -> None:
        parameters = []
        for parameter in getattr(command, "params", []):
            if parameter.name in _IGNORED_FRAMEWORK_PARAMETERS:
                continue
            option_names = [
                *getattr(parameter, "opts", []),
                *getattr(parameter, "secondary_opts", []),
            ]
            parameters.append(
                {
                    "name": parameter.name,
                    "kind": parameter.param_type_name,
                    "spellings": option_names,
                    "required": parameter.required,
                    "default": _normalized_default(parameter.default),
                    "type": _parameter_type(parameter),
                }
            )

        command_path = " ".join(path) or "<root>"
        surface[command_path] = {
            "kind": "group" if isinstance(getattr(command, "commands", None), dict) else "command",
            "hidden": bool(getattr(command, "hidden", False)),
            "parameters": parameters,
        }
        for name, child in sorted(getattr(command, "commands", {}).items()):
            visit(child, (*path, name))

    visit(get_command(app), ())
    return surface


def test_cli_v1_contract_matches_registered_surface() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))

    assert contract["schema_version"] == "forge-companion-cli-contract-v1"
    assert contract["framework_parameters_excluded"] == [
        "--help",
        "--install-completion",
        "--show-completion",
    ]
    assert contract["exit_codes"] == {
        "0": "success",
        "1": "operational_failure",
        "2": "parser_or_setup_precondition",
    }
    assert set(contract["commands"]) == set(_surface())
    assert {entry["stability"] for entry in contract["commands"].values()} == {
        "stable",
        "mixed",
        "experimental",
    }
    assert contract["commands"]["hopper plan"]["stability"] == "mixed"
    assert contract["commands"]["hopper fire"]["stability"] == "experimental"
    assert contract["commands"]["doctor"]["stability"] == "stable"

    actual = _surface()
    for path, expected in contract["commands"].items():
        assert actual[path] == {
            "kind": expected["kind"],
            "hidden": expected["hidden"],
            "parameters": expected["parameters"],
        }


def test_cli_v1_contract_records_parameter_types_and_ranges() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    commands = contract["commands"]

    assert commands["doctor"]["parameters"][0]["type"] == {"kind": "boolean"}
    assert commands["brews"]["parameters"] == [
        {
            "name": "page",
            "kind": "option",
            "spellings": ["--page"],
            "required": False,
            "default": 1,
            "type": {"kind": "integer", "minimum": 1},
        },
        {
            "name": "limit",
            "kind": "option",
            "spellings": ["--limit"],
            "required": False,
            "default": 100,
            "type": {"kind": "integer", "minimum": 1, "maximum": 100},
        },
    ]
    assert commands["hopper cloud-status"]["parameters"][0]["type"] == {
        "kind": "integer",
        "minimum": 0,
        "maximum": 255,
    }


def test_mixed_commands_define_stable_and_experimental_modes() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    mixed = {
        path: entry["modes"]
        for path, entry in contract["commands"].items()
        if entry["stability"] == "mixed"
    }

    assert mixed == {
        "hopper": {
            "stable": "simulated-pulse workflow and read-only diagnostics",
            "experimental": "Cloud-pulse lifecycle, qualification, readiness, and fire",
        },
        "hopper plan": {
            "stable": "simulated-pulse without --cloud",
            "experimental": "cloud-pulse with --cloud",
        },
        "hopper arm": {
            "stable": "simulated-pulse plan",
            "experimental": "cloud-pulse plan",
        },
        "hopper status": {
            "stable": "simulated-pulse plan",
            "experimental": "cloud-pulse plan",
        },
    }


def test_stable_and_experimental_command_classification_is_explicit() -> None:
    contract = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    classified = {
        stability: {
            path for path, entry in contract["commands"].items() if entry["stability"] == stability
        }
        for stability in ("stable", "mixed", "experimental")
    }

    assert classified["mixed"] == {"hopper", "hopper arm", "hopper plan", "hopper status"}
    assert classified["experimental"] == {
        "hopper check",
        "hopper fire",
        "hopper qualification",
        "hopper qualification attest",
        "hopper qualification revoke",
        "hopper qualification status",
    }
    assert {
        "doctor",
        "hopper cloud-auth",
        "hopper cloud-auth login",
        "hopper cloud-auth logout",
        "hopper cloud-auth status",
        "hopper cloud-status",
        "hopper shelly-status",
        "hopper simulate",
        "snapshot",
        "snapshot validate",
    } <= classified["stable"]


def test_exit_code_classes_match_representative_registered_behavior(
    monkeypatch: Any,
) -> None:
    from typer.testing import CliRunner

    import forge_companion.cli_brewforge as brewforge_cli
    import forge_companion.cli_hopper as hopper_cli

    runner = CliRunner()

    success = runner.invoke(app, ["--version"])
    parser_error = runner.invoke(app, ["brews", "--limit", "0"])
    validated_domain_error = runner.invoke(
        app,
        [
            "hopper",
            "plan",
            "--trigger-at",
            "invalid-trigger",
            "--pulse-ms",
            "1000",
        ],
    )
    monkeypatch.setattr(
        brewforge_cli.credentials,
        "resolve_token",
        lambda: brewforge_cli.credentials.ResolvedToken(token=None, source="missing"),
    )
    missing_setup = runner.invoke(app, ["doctor", "--json"])
    monkeypatch.setattr(
        hopper_cli.shelly_cloud_credentials,
        "resolve_profile",
        lambda: hopper_cli.shelly_cloud_credentials.ResolvedCloudProfile(
            profile=None,
            source="missing",
        ),
    )
    missing_cloud_status_setup = runner.invoke(app, ["hopper", "cloud-status"])

    assert success.exit_code == 0
    assert success.stdout.startswith("Forge Companion ")
    assert success.stderr == ""

    assert parser_error.exit_code == 2
    assert parser_error.stdout == ""
    assert "Invalid value for '--limit'" in unstyle(parser_error.stderr)

    assert validated_domain_error.exit_code == 1
    assert validated_domain_error.stdout == ""
    assert validated_domain_error.stderr == (
        "Hopper plan failed: trigger, pulse, or brew UUID is invalid.\n"
    )

    assert missing_setup.exit_code == 2
    assert missing_setup.stderr == ""
    assert json.loads(missing_setup.stdout) == {
        "schema_version": "forge-companion-doctor-v2",
        "status": "error",
        "checks": [],
        "error": {"code": "authentication_required"},
    }

    assert missing_cloud_status_setup.exit_code == 2
    assert missing_cloud_status_setup.stdout == ""
    assert "run `forge-companion hopper cloud-auth login`" in missing_cloud_status_setup.stderr
