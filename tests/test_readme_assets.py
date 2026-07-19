from pathlib import Path
from xml.etree import ElementTree

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_readme_static_badges_are_local_assets() -> None:
    readme = (REPOSITORY_ROOT / "README.md").read_text(encoding="utf-8")
    badge_paths = (
        "docs/assets/badges/license.svg",
        "docs/assets/badges/brewforge-read-only.svg",
    )
    replaced_remote_urls = (
        "https://img.shields.io/badge/license-MIT-788C5D",
        "https://img.shields.io/badge/BrewForge-read--only-D97757",
    )

    for badge_path in badge_paths:
        assert f'src="{badge_path}"' in readme
        ElementTree.parse(REPOSITORY_ROOT / badge_path)

    for remote_url in replaced_remote_urls:
        assert remote_url not in readme
