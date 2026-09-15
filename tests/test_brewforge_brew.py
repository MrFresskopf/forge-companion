"""Unit tests for strict BrewForge brew-detail parsing."""

import pytest

from forge_companion.brewforge_brew import (
    BrewContextSource,
    BrewForgeBrewDetailError,
    context_source_from_brew_detail,
)

BREW_ID = "54d34560-f1af-49f0-9a26-6caca3397f75"


def _payload(**overrides):
    values = {
        "id": BREW_ID,
        "name": "Example pale ale",
        "brewDate": "2026-08-29",
        "plannedBrewDate": "2026-09-30",
        "recipe": {"yeasts": [{"id": "yeast-1", "name": "US-05"}]},
        "measured": {"originalGravity": 1.077, "finalGravity": 1.012},
        "calculated": {"og": 1.080, "fg": 1.015},
    }
    values.update(overrides)
    return values


def test_extracts_validated_fields_from_direct_brew_detail():
    source = context_source_from_brew_detail(_payload(), brew_id=BREW_ID)

    assert source.batch_display_name == "Example pale ale"
    assert source.original_gravity_sg == "1.077"
    assert source.expected_final_gravity_sg == "1.015"
    assert source.yeast == "US-05"
    assert source.fermentation_start_date == "2026-08-29"


def test_rejects_response_id_that_does_not_match_request():
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(_payload(), brew_id="11111111-1111-4111-8111-111111111111")


def test_accepts_response_id_that_canonicalizes_to_request():
    source = context_source_from_brew_detail(_payload(id=BREW_ID.upper()), brew_id=BREW_ID)

    assert source.batch_display_name == "Example pale ale"


def test_missing_fields_are_unavailable_not_invented():
    source = context_source_from_brew_detail({"id": BREW_ID}, brew_id=BREW_ID)

    assert source == BrewContextSource(None, None, None, None, None)


def test_original_gravity_never_comes_from_calculated_og():
    source = context_source_from_brew_detail(
        _payload(measured={}, calculated={"og": 1.080, "fg": 1.015}),
        brew_id=BREW_ID,
    )

    assert source.original_gravity_sg is None
    assert source.expected_final_gravity_sg == "1.015"


def test_start_date_never_comes_from_planned_brew_date():
    source = context_source_from_brew_detail(
        _payload(brewDate=None, plannedBrewDate="2026-08-29"),
        brew_id=BREW_ID,
    )

    assert source.fermentation_start_date is None


@pytest.mark.parametrize(
    "value",
    [True, False, "1.077", [1.077], {"sg": 1.077}, float("nan"), float("inf")],
    ids=["true", "false", "str", "list", "dict", "nan", "inf"],
)
def test_rejects_invalid_original_gravity_types(value):
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(
            _payload(measured={"originalGravity": value}), brew_id=BREW_ID
        )


@pytest.mark.parametrize(
    "value",
    [0.999, 1.201, 10**400, -1.0],
    ids=["low", "high", "oversized", "negative"],
)
def test_rejects_out_of_range_original_gravity(value):
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(
            _payload(measured={"originalGravity": value}), brew_id=BREW_ID
        )


@pytest.mark.parametrize(
    "value",
    [True, float("nan"), 0.5, 10**400],
    ids=["bool", "nan", "low", "oversized"],
)
def test_rejects_invalid_expected_final_gravity(value):
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(
            _payload(calculated={"fg": value}), brew_id=BREW_ID
        )


def test_rejects_non_object_gravity_sections():
    for section in ("measured", "calculated", "recipe"):
        with pytest.raises(BrewForgeBrewDetailError):
            context_source_from_brew_detail(_payload(**{section: []}), brew_id=BREW_ID)


def test_rejects_non_text_brew_name():
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(_payload(name=12345), brew_id=BREW_ID)


@pytest.mark.parametrize(
    "name",
    ["bad\nname", "bad\u2028name", "bad\x1bname"],
    ids=["newline", "line-separator", "escape"],
)
def test_rejects_multiline_or_control_brew_name(name):
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(_payload(name=name), brew_id=BREW_ID)


def test_blank_brew_name_is_unavailable():
    source = context_source_from_brew_detail(_payload(name="   "), brew_id=BREW_ID)

    assert source.batch_display_name is None


def test_long_brew_name_is_bounded_deterministically():
    source = context_source_from_brew_detail(_payload(name="a" * 200), brew_id=BREW_ID)

    assert source.batch_display_name == "a" * 117 + "..."


def test_joins_multiple_yeast_names_deterministically():
    source = context_source_from_brew_detail(
        _payload(recipe={"yeasts": [{"name": "US-05"}, {"name": "S-04"}]}),
        brew_id=BREW_ID,
    )

    assert source.yeast == "US-05, S-04"


def test_long_yeast_join_is_bounded_deterministically():
    yeasts = [{"name": "y" * 40} for _ in range(40)]
    source = context_source_from_brew_detail(_payload(recipe={"yeasts": yeasts}), brew_id=BREW_ID)

    assert source.yeast is not None
    assert len(source.yeast) == 120
    assert source.yeast.endswith("...")


def test_rejects_oversized_yeast_list():
    yeasts = [{"name": "x"} for _ in range(65)]
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(_payload(recipe={"yeasts": yeasts}), brew_id=BREW_ID)


def test_rejects_non_list_yeasts():
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(
            _payload(recipe={"yeasts": "US-05"}), brew_id=BREW_ID
        )


@pytest.mark.parametrize(
    "entry",
    ["US-05", {"name": 5}, {"id": 7, "name": "US-05"}],
    ids=["not-object", "name-int", "id-int"],
)
def test_rejects_malformed_yeast_entries(entry):
    with pytest.raises(BrewForgeBrewDetailError):
        context_source_from_brew_detail(_payload(recipe={"yeasts": [entry]}), brew_id=BREW_ID)


def test_empty_yeast_names_are_unavailable():
    source = context_source_from_brew_detail(
        _payload(recipe={"yeasts": [{"name": "  "}]}), brew_id=BREW_ID
    )

    assert source.yeast is None


@pytest.mark.parametrize(
    "value",
    ["2026-08-29T18:00:00Z", "2026-08-29T18:00:00+02:00", "2026-02-30", 20260829, None],
    ids=["utc-timestamp", "offset-timestamp", "impossible", "int", "null"],
)
def test_ambiguous_or_non_calendar_start_dates_are_unavailable(value):
    source = context_source_from_brew_detail(_payload(brewDate=value), brew_id=BREW_ID)

    assert source.fermentation_start_date is None
