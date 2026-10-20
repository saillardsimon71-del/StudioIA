"""Paquet pilote « COMPTEUR — Le compteur ne ment jamais » (15 s, 9:16).

Valide HORS LIGNE les contrats de référence du pilote contre le moteur réel,
sans aucun calcul GPU ni dépense :

- chaque contrat passe ``validate_contract``, ``validate_speech_timing`` et ne
  déclenche aucune note de cohérence (pas d'image de départ montrant l'état
  final, pas de geste répété) ;
- la continuité contractuelle du compteur physique : 000 -> 001 -> 001 -> 002,
  chaque prise commençant à la valeur où la précédente finit ;
- les chiffres diégétiques déclarés alimentent la liste blanche OCR
  (``allowed_display_tokens``) : l'OCR ne les confond pas avec un sous-titre ;
- le prompt LTX restreint le texte visible aux chiffres déclarés et cale le
  début des paroles sur le contrat (amorce franche ou bref silence).

Le fichier lu est ``docs/studio/compteur-pilote/reference-contracts.json``.
"""
import json
import re
from pathlib import Path

import pytest

from calliope.studio import director, generated_text
from calliope.studio.director_contracts import validate_speech_timing
from calliope.studio.ltx_prompt import compile_ltx_prompt

PILOT = (Path(__file__).resolve().parents[2]
         / "docs" / "studio" / "compteur-pilote" / "reference-contracts.json")


@pytest.fixture(scope="module")
def pilot():
    assert PILOT.is_file(), f"Paquet pilote introuvable : {PILOT}"
    return json.loads(PILOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def canon(pilot):
    entities = pilot["entities"]
    props = [prop["key"] for prop in pilot["props"]]
    characters = {e["key"]: {"name": e["name"], "appearance": e["description"],
                             "voice": "voix naturelle"}
                  for e in entities if e["kind"] == "character"}
    return entities, props, characters


def _digits(state):
    return re.findall(r"\d+", state)


def test_pilot_declares_the_expected_editorial_frame(pilot):
    assert pilot["series"] == "COMPTEUR"
    assert pilot["aspect_ratio"] == "9:16"
    assert pilot["target_seconds"] == 15
    assert pilot["counter_sequence"] == ["000", "001", "001", "002"]
    assert pilot["estimated_eur"] <= pilot["budget_eur_ceiling"]
    assert [shot["id"] for shot in pilot["shots"]] == ["A", "B", "C"]
    assert sum(shot["contract"]["duration_sec"] for shot in pilot["shots"]) == 15.0


@pytest.mark.parametrize("shot_id", ["A", "B", "C"])
def test_each_reference_contract_is_valid_and_coherent(pilot, canon, shot_id):
    entities, props, _ = canon
    contract = next(s["contract"] for s in pilot["shots"] if s["id"] == shot_id)
    # Schéma, casting, lieu et références connus du canon.
    director.validate_contract(contract, entities, props)
    # Débit de parole réaliste, répliques dans la durée, sans chevauchement.
    validate_speech_timing(contract)
    # Aucune contradiction état/image ni geste répété.
    assert director.consistency_notes(contract) == []


def test_counter_continuity_chains_across_the_three_takes(pilot):
    shots = {s["id"]: s["contract"] for s in pilot["shots"]}
    a, b, c = shots["A"], shots["B"], shots["C"]
    # A : 000 au départ, 001 à la fin (une seule impulsion).
    assert _digits(a["initial_state"]["compteur"]) == ["000"]
    assert _digits(a["final_state"]["compteur"]) == ["001"]
    # B : conserve 001 du début à la fin.
    assert _digits(b["initial_state"]["compteur"]) == ["001"]
    assert _digits(b["final_state"]["compteur"]) == ["001"]
    # C : 001 au départ, 002 à la fin (une seule fois).
    assert _digits(c["initial_state"]["compteur"]) == ["001"]
    assert _digits(c["final_state"]["compteur"]) == ["002"]
    # Chaque prise commence là où la précédente finit.
    assert _digits(a["final_state"]["compteur"]) == _digits(b["initial_state"]["compteur"])
    assert _digits(b["final_state"]["compteur"]) == _digits(c["initial_state"]["compteur"])
    # Séquence contractuelle globale.
    sequence = [
        _digits(a["initial_state"]["compteur"])[-1],
        _digits(a["final_state"]["compteur"])[-1],
        _digits(c["initial_state"]["compteur"])[-1],
        _digits(c["final_state"]["compteur"])[-1],
    ]
    assert sequence == pilot["counter_sequence"]


@pytest.mark.parametrize("shot_id,expected", [
    ("A", {"000", "001"}), ("B", {"001"}), ("C", {"001", "002"}),
])
def test_declared_counter_digits_feed_the_ocr_allowlist(pilot, shot_id, expected):
    contract = next(s["contract"] for s in pilot["shots"] if s["id"] == shot_id)
    tokens = generated_text.allowed_display_tokens(
        {"shot": {}, "director_contract": contract})
    assert expected <= set(tokens)


def _ltx_prompt(pilot, canon, contract):
    entities, _, characters = canon
    shot = {
        "dialogue": " ".join(line["text"] for line in contract["dialogue"]),
        "dialogue_turns": [{"speaker": line["speaker"], "text": line["text"]}
                           for line in contract["dialogue"]],
        "action": contract["action"], "prompt": contract["action"],
        "audio_mode": "native", "duration_sec": contract["duration_sec"],
        "framing": contract["framing"], "camera": contract["camera"],
        "soundscape": contract["sound"],
    }
    context = {"language": pilot["language"], "entities": entities,
               "render_canon": {"characters": characters},
               "director_contract": contract}
    return compile_ltx_prompt(shot, context, first_frame=True)


@pytest.mark.parametrize("shot_id", ["A", "B", "C"])
def test_ltx_prompt_restricts_visible_text_to_the_declared_counter(pilot, canon, shot_id):
    contract = next(s["contract"] for s in pilot["shots"] if s["id"] == shot_id)
    prompt = _ltx_prompt(pilot, canon, contract)
    assert "the only visible characters are the numerals" in prompt
    assert "No subtitles, captions, speech bubbles, watermarks, logos" in prompt
    for number in _digits(contract["initial_state"]["compteur"]) + \
            _digits(contract["final_state"]["compteur"]):
        assert number in prompt


@pytest.mark.parametrize("shot_id,expected_cue", [
    ("A", "begins promptly as the shot opens"),
    ("B", "begins promptly as the shot opens"),
    ("C", "begins only after a brief silent beat"),
])
def test_ltx_prompt_cues_dialogue_onset_from_the_contract(pilot, canon, shot_id, expected_cue):
    contract = next(s["contract"] for s in pilot["shots"] if s["id"] == shot_id)
    prompt = _ltx_prompt(pilot, canon, contract)
    assert expected_cue in prompt
    # Jamais de timing absolu dans un prompt LTX (interdit par le linter).
    assert "At 2 seconds" not in prompt
    assert len(prompt.split()) <= 200
