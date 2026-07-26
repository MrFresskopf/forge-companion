import tomllib
from pathlib import Path

from forge_companion import __version__


def _project_metadata() -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[1]
    with (repository_root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_release_version_is_0_2_0_and_matches_runtime() -> None:
    project = _project_metadata()

    assert project["version"] == "0.2.0"
    assert __version__ == project["version"]


def test_windows_only_dependency_has_platform_marker() -> None:
    dependencies = _project_metadata()["dependencies"]

    assert isinstance(dependencies, list)
    assert "pywin32>=312; platform_system == 'Windows'" in dependencies
    assert "pywin32>=312" not in dependencies
