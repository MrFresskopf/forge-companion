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

import forge_companion
import keyring
from forge_companion import credentials, shelly_cloud_credentials

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
else:
    checks = (
        (credentials._require_native_backend, credentials.CredentialStoreError),
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
