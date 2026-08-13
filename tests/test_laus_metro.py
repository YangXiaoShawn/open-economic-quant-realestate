"""Metropolitan LAUS.

The failure mode guarded here is a malformed series identifier: BLS answers one with an
empty series rather than an error, which is indistinguishable from a metro that has no
labour force. A 19-character id (five trailing zeros instead of six) does exactly that,
and it is the bug this module's first draft shipped with.
"""

from __future__ import annotations

import pytest

from lockin.adapters.base import AdapterError
from lockin.adapters.bls_laus import (
    METRO_SEASONALITY_NOTE,
    metro_series_id,
    series_id,
)


def test_metro_id_matches_the_identifier_verified_against_the_api():
    assert metro_series_id("17", "16980") == "LAUMT171698000000003"
    assert metro_series_id("06", "31080") == "LAUMT063108000000003"


def test_every_laus_identifier_is_twenty_characters():
    """LA + seasonal(1) + area type(2) + area code(13) + measure(2)."""
    assert len(metro_series_id("17", "16980")) == 20
    assert len(series_id("CA")) == 20


def test_a_malformed_area_code_raises_instead_of_returning_nothing():
    with pytest.raises(AdapterError, match="13 characters"):
        metro_series_id("175", "16980")
    with pytest.raises(AdapterError, match="13 characters"):
        metro_series_id("17", "169800")


def test_metro_is_unadjusted_and_the_state_series_is_not():
    """Mixing them in one regression compares two different measurements."""
    assert metro_series_id("17", "16980").startswith("LAU")
    assert series_id("CA").startswith("LAS")


def test_the_seasonality_difference_is_documented_not_just_known():
    note = METRO_SEASONALITY_NOTE
    assert "UNADJUSTED" in note
    assert "never be pooled" in note
    assert "annual" in note
