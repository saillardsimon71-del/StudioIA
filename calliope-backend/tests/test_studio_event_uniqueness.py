"""Unicité des événements : détection déterministe des gestes répétés.

Le défaut documenté du job 681 : « la découverte du compteur par Marion est
décrite avant la réplique, puis répétée ensuite ». La conservation littérale de
chaque texte ne garantit pas l'unicité de l'événement. Ces tests verrouillent le
détecteur qui alimente la réparation (planner/Director) et les avertissements de
preflight, ainsi que la déduplication quasi-sémantique au rendu.
"""

from calliope.studio.ltx_realization import (
    content_words,
    find_repeated_events,
    repeated_pair,
)


def test_same_discovery_reworded_is_a_repeat():
    reason = repeated_pair(
        "Marion discovers the counter on her wrist.",
        "Marion looks down at her counter in shock.",
    )
    assert reason == "même personnage attentif au même objet"


def test_near_identical_description_is_a_repeat():
    assert repeated_pair(
        "Marion notices the counter on her wrist.",
        "Marion notices the counter at her wrist.",
    ) == "description presque identique"


def test_distinct_actions_are_not_repeats():
    assert repeated_pair(
        "Léa picks up the counter from the table.",
        "Léa sets the counter back on the table.",
    ) is None


def test_same_actor_different_object_is_not_a_repeat():
    assert repeated_pair(
        "Léa looks at the counter.",
        "Léa looks at the door.",
    ) is None


def test_counter_progression_is_never_a_repeat():
    # 000 -> 001 -> 002 est une progression diégétique, pas un doublon.
    assert repeated_pair(
        "The counter display reads 001.",
        "The counter display reads 002.",
    ) is None


def test_find_repeated_events_reports_the_documented_double_discovery():
    actions = [
        "Marion discovers the counter on her wrist.",
        "Marion says a line.",
        "Marion looks down at her counter again.",
    ]
    repeats = find_repeated_events(actions)
    assert (0, 2) in [(a, b) for a, b, _ in repeats]


def test_find_repeated_events_accepts_a_clean_sequence():
    actions = [
        "Léa picks up the counter from the table.",
        "Max steps back from the table.",
        "Léa sets the counter down.",
    ]
    assert find_repeated_events(actions) == []


def test_content_words_ignores_stopwords_and_attention_verbs():
    words = content_words("Marion looks at the counter on the table.")
    assert "counter" in words and "table" in words
    assert "look" not in words and "the" not in words
