import importlib.util
import tomllib
from pathlib import Path
from types import ModuleType

from forge_companion import __version__

_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _project_metadata() -> dict[str, object]:
    with (_REPOSITORY_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def _installed_smoke_module() -> ModuleType:
    script = _REPOSITORY_ROOT / "scripts" / "ci_installed_artifact_smoke.py"
    spec = importlib.util.spec_from_file_location("ci_installed_artifact_smoke", script)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_release_version_is_0_3_0_and_matches_runtime() -> None:
    project = _project_metadata()

    assert project["version"] == "0.3.0"
    assert __version__ == project["version"]


def test_bug_report_template_uses_release_version() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    template = (repository_root / ".github" / "ISSUE_TEMPLATE" / "bug.yml").read_text()

    assert f"placeholder: Forge Companion {__version__}" in template


def test_windows_only_dependency_has_platform_marker() -> None:
    dependencies = _project_metadata()["dependencies"]

    assert isinstance(dependencies, list)
    assert "pywin32>=312; platform_system == 'Windows'" in dependencies
    assert "pywin32>=312" not in dependencies


def test_ci_matrix_includes_macos_and_installed_artifact_smoke() -> None:
    workflow = (_REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "macos-latest" in workflow
    assert "uv build --wheel --out-dir dist" in workflow
    assert "uv run python scripts/ci_installed_artifact_smoke.py" in workflow


def test_installed_smoke_uses_platform_specific_virtualenv_paths() -> None:
    module = _installed_smoke_module()
    root = Path("artifact-venv")

    assert module._venv_python(root, "Windows") == root / "Scripts" / "python.exe"
    assert module._venv_python(root, "Linux") == root / "bin" / "python"
    assert module._venv_cli(root, "Windows") == root / "Scripts" / "forge-companion.exe"
    assert module._venv_cli(root, "Darwin") == root / "bin" / "forge-companion"


def test_installed_smoke_removes_import_path_overrides() -> None:
    module = _installed_smoke_module()

    cleaned = module._clean_environment(
        {
            "PYTHONPATH": "unsafe-source-path",
            "PYTHONHOME": "unsafe-runtime",
            "OTHER": "preserved",
        }
    )

    assert cleaned["OTHER"] == "preserved"
    assert cleaned["PYTHONNOUSERSITE"] == "1"
    assert "PYTHONPATH" not in cleaned
    assert "PYTHONHOME" not in cleaned


def test_installed_smoke_checks_linux_native_or_fail_closed_keyring_boundary() -> None:
    program = _installed_smoke_module()._verification_program()

    assert "keyring.backends.SecretService" in program
    assert "credentials.CredentialStoreError" in program
    assert "shelly_cloud_credentials.ShellyCloudCredentialError" in program
