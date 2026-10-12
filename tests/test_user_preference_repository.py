"""Tests for user_preference_repository.py."""

from __future__ import annotations

from trip_hunter.user_preference_repository import SETTINGS_MENU_AIRPORTS, UserPreferenceRepository


def test_a_brand_new_chat_id_has_no_preferences_and_is_not_a_dm_recipient(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    assert repo.has_preferences(111) is False
    assert repo.get_user_origins(111) is None  # the settings menu still shows everything checked by default
    assert repo.is_active(111) is False
    assert repo.get_users_for_origin("FRA") == []  # never assumed "all" for a stranger who never opted in


def test_select_all_subscribes_to_every_origin(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.select_all(111)

    assert repo.has_preferences(111) is True
    assert repo.get_user_origins(111) is None
    assert repo.is_active(111) is True
    for code in SETTINGS_MENU_AIRPORTS:
        assert 111 in repo.get_users_for_origin(code)


def test_set_user_origins_stores_an_explicit_subset(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["FRA", "MUC"])

    assert repo.get_user_origins(111) == ["FRA", "MUC"]
    assert repo.get_users_for_origin("FRA") == [111]
    assert repo.get_users_for_origin("MUC") == [111]
    assert repo.get_users_for_origin("HAM") == []


def test_set_user_origins_sorts_and_deduplicates(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["muc", "FRA", "muc", " fra "])
    assert repo.get_user_origins(111) == ["FRA", "MUC"]


def test_empty_list_and_star_both_mean_all_same_as_none(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    for value in (None, [], ["*"]):
        repo.set_user_origins(111, value)
        assert repo.get_user_origins(111) is None
        assert repo.is_active(111) is True


def test_get_users_for_origin_only_returns_active_subscribers(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["FRA"])
    repo.set_user_origins(222, ["FRA"])
    repo.set_active(222, False)

    assert repo.get_users_for_origin("FRA") == [111]


def test_deselect_all_pauses_without_resubscribing_to_everything(tmp_path):
    """The task's own two rules ("empty csv means all" + "a deselect-all
    button must exist") only reconcile if deselect_all does NOT store an
    empty csv - it pauses (is_active=0) instead. See the module's own
    "THE 'ALLE ABWÄHLEN' EDGE CASE" docstring section."""
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.select_all(111)
    repo.deselect_all(111)

    assert repo.is_active(111) is False
    assert repo.get_users_for_origin("FRA") == []
    assert repo.get_users_for_origin("MUC") == []


def test_deselect_all_on_a_brand_new_chat_id_just_creates_a_paused_row(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.deselect_all(111)
    assert repo.has_preferences(111) is True
    assert repo.is_active(111) is False


def test_toggle_origin_off_from_all_expands_to_every_other_airport(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.select_all(111)

    new_origins = repo.toggle_origin(111, "FRA")

    assert "FRA" not in new_origins
    assert set(new_origins) == set(SETTINGS_MENU_AIRPORTS) - {"FRA"}
    assert repo.get_users_for_origin("FRA") == []
    assert repo.get_users_for_origin("MUC") == [111]


def test_toggle_origin_on_a_brand_new_chat_id_also_starts_from_all(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    new_origins = repo.toggle_origin(111, "FRA")
    assert set(new_origins) == set(SETTINGS_MENU_AIRPORTS) - {"FRA"}


def test_toggle_origin_on_adds_it_back(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["MUC"])
    new_origins = repo.toggle_origin(111, "FRA")
    assert set(new_origins) == {"MUC", "FRA"}


def test_toggling_every_airport_back_on_collapses_to_the_canonical_all_state(tmp_path):
    """Two different paths to "every box checked" (select_all, or
    toggling the very last excluded box back on) must end up IDENTICALLY
    represented - never a literal csv of all 10 codes living alongside
    the "*" sentinel as a second, subtly different way to mean the same
    thing."""
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.toggle_origin(111, "FRA")  # -> all minus FRA
    result = repo.toggle_origin(111, "FRA")  # -> FRA back on -> every box checked again

    assert result == list(SETTINGS_MENU_AIRPORTS)
    assert repo.get_user_origins(111) is None  # the canonical "all" sentinel, not a literal 10-code csv


def test_toggling_off_the_last_remaining_airport_pauses_instead_of_reverting_to_all(tmp_path):
    """The reverse of "Alle abwählen" via individual taps - toggling the
    very last checked box off must behave the same way the dedicated
    button does (pause), never silently resubscribe to everything."""
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["FRA"])

    result = repo.toggle_origin(111, "FRA")

    assert result == []
    assert repo.is_active(111) is False
    assert repo.get_users_for_origin("FRA") == []


def test_reactivating_after_a_full_deselect_starts_clean_not_from_stale_data(tmp_path):
    """Toggling everything off and then reactivating via a fresh toggle
    must start from the neutral "all" default minus the toggled one -
    never resurrect a DIFFERENT, now-irrelevant stale subset (here:
    "only BSL") that happened to be stored right before the user paused.
    BSL itself is correctly back INCLUDED - "all minus HAM" naturally
    contains it, same as every other airport except HAM."""
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["BSL"])
    repo.toggle_origin(111, "BSL")  # -> empty -> pauses
    assert repo.is_active(111) is False

    result = repo.toggle_origin(111, "HAM")

    assert "HAM" not in result
    assert "BSL" in result  # "all minus HAM" - not a leftover from before pausing
    assert "FRA" in result
    assert repo.is_active(111) is True


def test_set_active_true_reactivates_without_touching_explicit_origins(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.set_user_origins(111, ["FRA", "MUC"])
    repo.set_active(111, False)
    repo.set_active(111, True)

    assert repo.is_active(111) is True
    assert repo.get_user_origins(111) == ["FRA", "MUC"]


def test_multiple_users_with_overlapping_and_distinct_origins(tmp_path):
    repo = UserPreferenceRepository(tmp_path / "t.db")
    repo.select_all(1)
    repo.set_user_origins(2, ["FRA", "HAM"])
    repo.set_user_origins(3, ["MUC"])
    repo.deselect_all(4)  # paused - never included anywhere

    assert set(repo.get_users_for_origin("FRA")) == {1, 2}
    assert set(repo.get_users_for_origin("HAM")) == {1, 2}
    assert set(repo.get_users_for_origin("MUC")) == {1, 3}
    assert set(repo.get_users_for_origin("BSL")) == {1}


def test_preferences_persist_across_repository_instances(tmp_path):
    db_path = tmp_path / "t.db"
    UserPreferenceRepository(db_path).set_user_origins(111, ["FRA"])

    reopened = UserPreferenceRepository(db_path)
    assert reopened.get_user_origins(111) == ["FRA"]
    assert reopened.get_users_for_origin("FRA") == [111]


def test_settings_menu_airports_has_exactly_the_tasks_own_ten_hubs():
    assert SETTINGS_MENU_AIRPORTS == ("FRA", "MUC", "BER", "HAM", "DUS", "CGN", "STR", "VIE", "ZRH", "BSL")
