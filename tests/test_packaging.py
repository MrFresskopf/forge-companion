import importlib.util
import tomllib
from hashlib import sha256
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


def test_release_version_is_0_5_0_across_active_metadata_and_readme() -> None:
    project = _project_metadata()
    lock = (_REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8")
    readme = (_REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")

    assert project["version"] == "0.5.0"
    assert __version__ == project["version"] == "0.5.0"
    assert 'name = "forge-companion"\nversion = "0.5.0"' in lock
    assert readme.count("@v0.5.0") == 4
    assert "@v0.4.0" not in readme


def test_frozen_snapshot_fixture_remains_historical_and_lf_byte_stable() -> None:
    fixture = _REPOSITORY_ROOT / "tests" / "fixtures" / "collection-snapshot-v3.json"
    fixture_bytes = fixture.read_bytes().replace(bytes((13, 10)), bytes((10,)))

    assert sha256(fixture_bytes).hexdigest() == (
        "e363a679dc962b0e139ac78e20b10ac41aae2a11f40212e4bb70ad7b0d456f3e"
    )
    attributes = (_REPOSITORY_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "tests/fixtures/collection-snapshot-v3.json text eol=lf" in attributes
    assert '"version": "0.2.1"' in fixture.read_text(encoding="utf-8")


def test_release_docs_ship_0_5_0_experimental_surfaces_and_keep_v0_4_history() -> None:
    readme = (_REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    commands = (_REPOSITORY_ROOT / "docs" / "COMMANDS.md").read_text(encoding="utf-8")
    compatibility = (_REPOSITORY_ROOT / "docs" / "COMPATIBILITY.md").read_text(encoding="utf-8")
    changelog = (_REPOSITORY_ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

    assert "[0.5.0] — 2026-09-18" in changelog
    assert (
        "[0.5.0]: https://github.com/MrFresskopf/forge-companion/compare/v0.4.0...v0.5.0"
        in changelog
    )
    assert "## [0.4.0] — 2026-08-16" in changelog
    assert "An executable 1.0 CLI freeze candidate" in changelog
    assert "unreleased" not in readme.lower()
    assert "unreleased" not in commands.lower()
    assert "unreleased" not in compatibility.lower()
    assert "## `rapt` (experimental)" in commands
    assert "## `vessel` (experimental)" in commands
    assert "#rapt-experimental" in readme
    assert "#vessel-experimental" in readme
    assert "#vessel-sg-diagnose-brewforge-experimental" in readme


def test_packaging_ignores_all_local_virtualenv_directories() -> None:
    gitignore = (_REPOSITORY_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".venv*/" in gitignore


def test_bug_report_template_uses_release_version() -> None:
    repository_root = Path(__file__).resolve().parents[1]
    template = (repository_root / ".github" / "ISSUE_TEMPLATE" / "bug.yml").read_text()

    assert f"placeholder: Forge Companion {__version__}" in template


def test_windows_only_dependency_has_platform_marker() -> None:
    dependencies = _project_metadata()["dependencies"]

    assert isinstance(dependencies, list)
    assert "pywin32>=312; platform_system == 'Windows'" in dependencies
    assert "pywin32>=312" not in dependencies
    assert "tzdata>=2026.1; platform_system == 'Windows'" in dependencies
    assert "tzdata>=2026.1" not in dependencies


def test_ci_matrix_includes_macos_and_installed_artifact_smoke() -> None:
    workflow = (_REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

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


def test_installed_smoke_checks_packaged_cli_contract() -> None:
    program = _installed_smoke_module()._verification_program()

    assert "forge_companion.contracts" in program
    assert "cli-v1-contract.json" in program
    assert "forge-companion-cli-contract-v1" in program


def test_installed_smoke_checks_rapt_surface_and_native_boundary() -> None:
    program = _installed_smoke_module()._verification_program()
    assert "rapt_credentials._require_native_backend()" in program
    assert "rapt_credentials.RaptCredentialError" in program
    assert '"rapt telemetry"' in program
    assert '"rapt auth login"' in program
    assert '"rapt", "telemetry", "--help"' in program
    assert '"vessel telemetry"' in program
    assert '"vessel", "telemetry", "--help"' in program
    assert '"vessel status"' in program
    assert '"vessel", "status", "--help"' in program
    assert '"vessel compare-progress"' in program
    assert '"vessel", "compare-progress", "--help"' in program
    assert '"vessel sg-diagnose"' in program
    assert '"vessel", "sg-diagnose", "--help"' in program
    assert '"vessel sg-diagnose-brewforge"' in program
    assert '"vessel", "sg-diagnose-brewforge", "--help"' in program
    assert '"vessel sg-trend"' in program
    assert '"vessel", "sg-trend", "--help"' in program
    assert '"vessel overview"' in program
    assert '"vessel", "overview", "--help"' in program
    assert '"vessel context start"' in program
    assert '"vessel", "context", "start", "--help"' in program
    assert '"vessel context start-brewforge"' in program
    assert '"vessel", "context", "start-brewforge", "--help"' in program
    assert '"vessel context phase"' in program
    assert '"vessel", "context", "phase", "--help"' in program


def test_installed_smoke_checks_named_phase_timezone_lookup() -> None:
    program = _installed_smoke_module()._verification_program()

    assert 'ZoneInfo("Europe/Berlin")' in program


def test_installed_smoke_exercises_brewforge_diagnostic_offline() -> None:
    program = _installed_smoke_module()._verification_program()
    assert "brews/{brew_id}/readings" in program
    assert "source=brewforge gravity_unit=sg temperature_unit=c" in program
    assert "installed diagnostic accessed credentials before preflight" in program
