"""Strict, atomic parsing of one BrewForge brew detail object.

BrewForge returns a *direct object* for ``GET /brews/{id}`` (not an envelope). This
module validates that object and extracts only the values a fermentation context may
copy from it:

* the batch display name, from the validated brew ``name``;
* the original gravity, **only** from ``measured.originalGravity`` -- never from the
  ``calculated.og`` estimate;
* the expected final gravity, from ``calculated.fg``. This is the brew's *calculated
  estimate*, not a measurement, and is documented as such wherever it is stored;
* the yeast, from validated nonempty ``recipe.yeasts[].name`` entries, joined in a
  deterministic bounded display;
* the start date, from an unambiguous ``brewDate`` calendar date -- never from
  ``plannedBrewDate``, and never from a timestamp whose instant-to-calendar-date
  mapping would require an unknown vessel timezone.

Any field that is absent or not unambiguously usable is returned as ``None`` so the
caller can require an explicit override; genuinely malformed structures raise
:class:`BrewForgeBrewDetailError` without echoing raw upstream values.
"""

import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from math import isfinite
from uuid import UUID

MAX_DISPLAY_LENGTH = 120
MAX_YEASTS = 64
_SG_MIN = Decimal("1.000")
_SG_MAX = Decimal("1.200")
_SG_QUANTUM = Decimal("0.001")
_MULTILINE_CHARACTERS = frozenset("\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029")


class BrewForgeBrewDetailError(ValueError):
    """Report a malformed BrewForge brew detail that cannot be trusted."""


@dataclass(frozen=True)
class BrewContextSource:
    """Source-derived fermentation-context fields; ``None`` means unavailable."""

    batch_display_name: str | None
    original_gravity_sg: str | None
    expected_final_gravity_sg: str | None
    yeast: str | None
    fermentation_start_date: str | None


def _canonical_brew_id(payload: dict[str, object], requested: str) -> None:
    raw_id = payload.get("id")
    if not isinstance(raw_id, str):
        raise BrewForgeBrewDetailError("BrewForge brew detail has no string ID.")
    try:
        canonical = str(UUID(raw_id))
    except ValueError:
        raise BrewForgeBrewDetailError("BrewForge brew detail ID is not a UUID.") from None
    if canonical != requested:
        raise BrewForgeBrewDetailError("BrewForge brew detail ID does not match the request.")


def _optional_mapping(payload: dict[str, object], key: str) -> dict[str, object] | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise BrewForgeBrewDetailError(f"BrewForge brew detail {key} is not an object.")
    return value


def _sg_text(value: object) -> str:
    """Quantize a finite measured/calculated gravity to the persisted 1.xxx precision."""
    if isinstance(value, bool):
        raise BrewForgeBrewDetailError("BrewForge gravity is not a plain number.")
    if isinstance(value, int):
        decimal_value = Decimal(value)
    elif isinstance(value, float):
        if not isfinite(value):
            raise BrewForgeBrewDetailError("BrewForge gravity is not finite.")
        decimal_value = Decimal(str(value))
    else:
        raise BrewForgeBrewDetailError("BrewForge gravity is not a plain number.")
    if not decimal_value.is_finite() or not _SG_MIN <= decimal_value <= _SG_MAX:
        raise BrewForgeBrewDetailError("BrewForge gravity is outside the supported range.")
    quantized = decimal_value.quantize(_SG_QUANTUM, rounding=ROUND_HALF_UP)
    if not _SG_MIN <= quantized <= _SG_MAX:
        raise BrewForgeBrewDetailError("BrewForge gravity is outside the supported range.")
    return format(quantized, "f")


def _gravity(payload: dict[str, object], section: str, key: str) -> str | None:
    mapping = _optional_mapping(payload, section)
    if mapping is None or key not in mapping:
        return None
    return _sg_text(mapping[key])


def _has_multiline(value: str) -> bool:
    return any(
        character in _MULTILINE_CHARACTERS or unicodedata.category(character).startswith("C")
        for character in value
    )


def _bounded_display(value: str, field: str) -> str:
    stripped = value.strip()
    if not stripped or _has_multiline(stripped):
        raise BrewForgeBrewDetailError(f"BrewForge brew detail {field} is not displayable.")
    if len(stripped) > MAX_DISPLAY_LENGTH:
        return stripped[: MAX_DISPLAY_LENGTH - 3] + "..."
    return stripped


def _name(payload: dict[str, object]) -> str | None:
    raw = payload.get("name")
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise BrewForgeBrewDetailError("BrewForge brew detail name is not text.")
    if not raw.strip():
        return None
    return _bounded_display(raw, "name")


def _yeast(payload: dict[str, object]) -> str | None:
    recipe = _optional_mapping(payload, "recipe")
    if recipe is None or "yeasts" not in recipe:
        return None
    yeasts = recipe["yeasts"]
    if not isinstance(yeasts, list):
        raise BrewForgeBrewDetailError("BrewForge brew detail recipe.yeasts is not a list.")
    if len(yeasts) > MAX_YEASTS:
        raise BrewForgeBrewDetailError("BrewForge brew detail recipe.yeasts is too large.")
    names: list[str] = []
    for entry in yeasts:
        if not isinstance(entry, dict):
            raise BrewForgeBrewDetailError("BrewForge brew detail yeast entry is not an object.")
        if "id" in entry and not isinstance(entry["id"], str):
            raise BrewForgeBrewDetailError("BrewForge brew detail yeast ID is not text.")
        raw_name = entry.get("name")
        if raw_name is None:
            continue
        if not isinstance(raw_name, str):
            raise BrewForgeBrewDetailError("BrewForge brew detail yeast name is not text.")
        stripped = raw_name.strip()
        if not stripped:
            continue
        if _has_multiline(stripped):
            raise BrewForgeBrewDetailError("BrewForge brew detail yeast name is not displayable.")
        names.append(stripped)
    if not names:
        return None
    combined = ", ".join(names)
    if len(combined) > MAX_DISPLAY_LENGTH:
        return combined[: MAX_DISPLAY_LENGTH - 3] + "..."
    return combined


def _start_date(payload: dict[str, object]) -> str | None:
    """Return a date-only ``brewDate``; timestamps are ambiguous without a vessel zone."""
    raw = payload.get("brewDate")
    if not isinstance(raw, str):
        return None
    if _has_multiline(raw):
        raise BrewForgeBrewDetailError("BrewForge brew detail brewDate is not displayable.")
    if len(raw) != 10 or raw[4] != "-" or raw[7] != "-":
        return None
    year, month, day = raw[:4], raw[5:7], raw[8:]
    if not (year.isdigit() and month.isdigit() and day.isdigit()):
        return None
    try:
        parsed = date(int(year), int(month), int(day))
    except ValueError:
        return None
    return parsed.isoformat() if parsed.isoformat() == raw else None


def context_source_from_brew_detail(payload: object, *, brew_id: str) -> BrewContextSource:
    """Validate one direct brew-detail object and return source-derived fields."""
    if not isinstance(payload, dict):
        raise BrewForgeBrewDetailError("BrewForge brew detail is not an object.")
    _canonical_brew_id(payload, brew_id)
    return BrewContextSource(
        batch_display_name=_name(payload),
        original_gravity_sg=_gravity(payload, "measured", "originalGravity"),
        expected_final_gravity_sg=_gravity(payload, "calculated", "fg"),
        yeast=_yeast(payload),
        fermentation_start_date=_start_date(payload),
    )
