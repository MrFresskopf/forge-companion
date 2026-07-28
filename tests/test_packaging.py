import tomllib
from pathlib import Path

from forge_companion import __version__


def _project_metadata() -> dict[str, object]:
    repository_root = Path(__file__).resolve().parents[1]
    with (repository_root / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]


def test_release_version_is_0_2_1_and_matches_runtime() -> None:
    project = _project_metadata()

    assert project["version"] == "0.2.1"
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
