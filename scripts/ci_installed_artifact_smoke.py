"""Build-CI smoke for an isolated Forge Companion wheel installation."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path


def _venv_python(root: Path, system: str) -> Path:
    if system == "Windows":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def _venv_cli(root: Path, system: str) -> Path:
    if system == "Windows":
        return root / "Scripts" / "forge-companion.exe"
    return root / "bin" / "forge-companion"


def _clean_environment(source: Mapping[str, str]) -> dict[str, str]:
    environment = dict(source)
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _run(command: list[str], *, cwd: Path, environment: Mapping[str, str]) -> None:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {' '.join(command)}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def _verification_program() -> str:
    return r"""
import importlib.metadata as metadata
import importlib.resources as resources
import json
import os
import platform
from pathlib import Path
from zoneinfo import ZoneInfo

import forge_companion
import keyring
from forge_companion import credentials, rapt_credentials, shelly_cloud_credentials

venv_root = Path(os.environ["FORGE_COMPANION_SMOKE_VENV"]).resolve()
module_path = Path(forge_companion.__file__).resolve()
if venv_root not in module_path.parents:
    raise AssertionError(f"package imported outside smoke venv: {module_path}")
if metadata.version("forge-companion") != forge_companion.__version__:
    raise AssertionError("installed metadata and runtime version differ")

contract_text = (
    resources.files("forge_companion.contracts")
    .joinpath("cli-v1-contract.json")
    .read_text(encoding="utf-8")
)
contract = json.loads(contract_text)
if contract.get("schema_version") != "forge-companion-cli-contract-v1":
    raise AssertionError("installed CLI contract is missing or incompatible")

for path in (
    "rapt auth login",
    "rapt telemetry",
    "rapt devices",
    "vessel telemetry",
    "vessel status",
    "vessel compare-progress",
    "vessel sg-diagnose",
    "vessel sg-diagnose-brewforge",
    "vessel sg-trend",
    "vessel overview",
    "vessel context start",
    "vessel context start-brewforge",
    "vessel context phase",
    "vessel context show",
    "vessel context close",
):
    if contract["commands"][path]["stability"] != "experimental":
        raise AssertionError("RAPT surface absent or not experimental")
from typer.testing import CliRunner
from forge_companion.cli import app
for args in (
    ["rapt", "--help"],
    ["rapt", "telemetry", "--help"],
    ["vessel", "--help"],
    ["vessel", "telemetry", "--help"],
    ["vessel", "status", "--help"],
    ["vessel", "compare-progress", "--help"],
    ["vessel", "sg-diagnose", "--help"],
    ["vessel", "sg-diagnose-brewforge", "--help"],
    ["vessel", "sg-trend", "--help"],
    ["vessel", "overview", "--help"],
    ["vessel", "context", "start", "--help"],
    ["vessel", "context", "start-brewforge", "--help"],
    ["vessel", "context", "phase", "--help"],
    ["vessel", "context", "show", "--help"],
    ["vessel", "context", "close", "--help"],
):
    result = CliRunner().invoke(app, args)
    if result.exit_code != 0:
        raise AssertionError("installed CLI help failed")

# Exercise the installed implementation with synthetic local state and a recording
# client. Neither credentials nor a real network or device is used by this check.
import tempfile
from unittest.mock import patch
import forge_companion.cli_vessel as vessel_cli

brew_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
calls = []
class DiagnosticClient:
    def __init__(self, *, token):
        calls.append(("client", token))
    def get(self, path):
        calls.append(("get", path))
        return {"data": [
            {"id": "a", "timestamp": "2026-09-03T00:30:00.0000001Z", "gravity": 1.019},
            {"id": "b", "timestamp": "2026-09-03T01:00:00.0000001Z", "gravity": 1.018},
        ]}

def diagnostic_token():
    calls.append("token")
    return "synthetic-token"

diagnostic_args = [
    "vessel", "sg-diagnose-brewforge", "tank",
    "--start", "2026-09-03T00:00:00.0000001Z",
    "--end", "2026-09-03T01:00:00.0000001Z", "--trigger-sg", "1.020",
    "--max-age-minutes", "30", "--max-gap-minutes", "30", "--confirmations", "2",
    "--gravity-unit", "sg", "--temperature-unit", "c",
]
with tempfile.TemporaryDirectory() as state:
    with patch.dict(os.environ, {"FORGE_COMPANION_CONFIG_DIR": state}), \
         patch.object(vessel_cli, "_token_for_api", diagnostic_token), \
         patch.object(vessel_cli, "BrewForgeClient", DiagnosticClient):
        result = CliRunner().invoke(app, diagnostic_args)
        if result.exit_code != 1 or result.stdout or calls:
            raise AssertionError("installed diagnostic accessed credentials before preflight")
        context_path = Path(state) / "fermentation-contexts.json"
        context_path.write_text(json.dumps({
            "schema_version": "forge-companion-fermentation-contexts-v1",
            "contexts": [{
                "context_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "vessel_id": "tank",
                "batch_display_name": "Synthetic", "original_gravity_sg": "1.050",
                "expected_final_gravity_sg": "1.010", "yeast": "Synthetic",
                "fermentation_start_date": "2026-09-01", "start_instant": None,
                "same_day_telemetry_attribution": "unavailable",
                "authoritative_temperature_role": "hydrometer",
                "source_devices": {
                    "hydrometer": "11111111-1111-4111-8111-111111111111",
                    "temperature_controller": "22222222-2222-4222-8222-222222222222",
                }, "status": "active", "brewforge_brew_id": brew_id,
            }],
        }), encoding="utf-8")
        before = context_path.read_bytes()
        result = CliRunner().invoke(app, diagnostic_args)
        if result.exit_code != 0 or result.stderr:
            raise AssertionError("installed BrewForge diagnostic failed")
        if calls != ["token", ("client", "synthetic-token"),
                     ("get", f"brews/{brew_id}/readings")]:
            raise AssertionError("installed diagnostic request contract failed")
        for expected in (
            "source=brewforge gravity_unit=sg temperature_unit=c",
            "evaluation_at_ns=1788397200000000100",
            "status=CONDITION_MET reason=AT_OR_BELOW_TRIGGER",
            "No BrewForge, RAPT, or Shelly write and no device command was sent.",
        ):
            if expected not in result.stdout:
                raise AssertionError("installed diagnostic output contract failed")
        if context_path.read_bytes() != before or list(Path(state).iterdir()) != [context_path]:
            raise AssertionError("installed diagnostic changed local state")

phase_timezone = ZoneInfo("Europe/Berlin")
if phase_timezone.key != "Europe/Berlin":
    raise AssertionError("installed timezone data lookup failed")

system = platform.system()
backend = keyring.get_keyring()
backend_module = type(backend).__module__
if system == "Windows":
    metadata.version("pywin32")
    allowed_prefix = "keyring.backends.Windows"
elif system == "Darwin":
    try:
        metadata.version("pywin32")
    except metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError("pywin32 must not be installed on macOS")
    allowed_prefix = "keyring.backends.macOS"
else:
    try:
        metadata.version("pywin32")
    except metadata.PackageNotFoundError:
        pass
    else:
        raise AssertionError("pywin32 must not be installed on non-Windows platforms")
    secret_service_prefix = "keyring.backends.SecretService"
    if backend_module == secret_service_prefix or backend_module.startswith(
        f"{secret_service_prefix}."
    ):
        allowed_prefix = secret_service_prefix
    else:
        allowed_prefix = None

if allowed_prefix is not None:
    if not (
        backend_module == allowed_prefix
        or backend_module.startswith(f"{allowed_prefix}.")
    ):
        raise AssertionError(f"unexpected native keyring backend: {backend_module}")
    credentials._require_native_backend()
    shelly_cloud_credentials._require_native_backend()
    rapt_credentials._require_native_backend()
else:
    checks = (
        (credentials._require_native_backend, credentials.CredentialStoreError),
        (rapt_credentials._require_native_backend, rapt_credentials.RaptCredentialError),
        (
            shelly_cloud_credentials._require_native_backend,
            shelly_cloud_credentials.ShellyCloudCredentialError,
        ),
    )
    for check, expected_error in checks:
        try:
            check()
        except expected_error:
            pass
        else:
            raise AssertionError(
                f"unsupported keyring backend was accepted: {backend_module}"
            )

print(
    f"installed artifact OK: {forge_companion.__version__}; "
    f"backend={backend_module}; module={module_path}"
)
"""


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    wheels = sorted((repository / "dist").glob("forge_companion-*.whl"))
    if len(wheels) != 1:
        raise RuntimeError(f"Expected exactly one wheel in dist, found {len(wheels)}")

    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required for the installed-artifact smoke")

    system = platform.system()
    environment = _clean_environment(os.environ)
    with tempfile.TemporaryDirectory(prefix="forge-companion-artifact-") as temporary:
        venv_root = Path(temporary) / "venv"
        _run(
            [uv, "venv", str(venv_root), "--python", sys.executable],
            cwd=repository,
            environment=environment,
        )
        python = _venv_python(venv_root, system)
        cli = _venv_cli(venv_root, system)
        _run(
            [uv, "pip", "install", "--python", str(python), "--no-cache", str(wheels[0])],
            cwd=repository,
            environment=environment,
        )

        smoke_environment = dict(environment)
        smoke_environment["FORGE_COMPANION_SMOKE_VENV"] = str(venv_root.resolve())
        _run(
            [str(python), "-I", "-c", _verification_program()],
            cwd=venv_root,
            environment=smoke_environment,
        )
        _run([str(cli), "--help"], cwd=venv_root, environment=smoke_environment)
        _run(
            [str(cli), "hopper", "cloud-auth", "--help"],
            cwd=venv_root,
            environment=smoke_environment,
        )

    print(f"isolated wheel smoke passed on {system}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
