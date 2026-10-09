import asyncio
import copy

import pytest

from calliope.studio import series_planner


def manifest(dialogue, speaker="mango"):
    return {
        "entities": [
            {"key": "mango", "name": "Mango", "kind": "character", "description": "Gold jacket."},
            {
                "key": "blueberry",
                "name": "Blueberry",
                "kind": "character",
                "description": "Blue coat.",
            },
        ],
        "scenes": [
            {
                "shots": [
                    {
                        "prompt": "Mango speaks while Blueberry listens.",
                        "image_prompt": "Two students in a hallway.",
                        "speaker": speaker,
                        "character_keys": ["mango", "blueberry"],
                        "dialogue": dialogue,
                        "duration_sec": 6.0,
                    }
                ]
            }
        ],
    }


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Mango: You're kidding.", "You're kidding."),
        ("MANGO: You're kidding.", "You're kidding."),
        ('"You\'re kidding."', "You're kidding."),
        ("'You're kidding.'", "You're kidding."),
        ("\u201cYou're kidding.\u201d", "You're kidding."),
        ("<d>[English] You're kidding.</d>", "You're kidding."),
        ('Mango: "<d>[English] You\'re kidding.</d>"', "You're kidding."),
        ("<d>[English] Mango: You're kidding.</d>", "You're kidding."),
        ("You're\nkidding.", "You're kidding."),
        ("Mango: You're kidding.\nMango: No way.", "You're kidding. No way."),
        ("Remember this: you're kidding.", "Remember this: you're kidding."),
    ],
)
def test_planner_normalizes_single_voice_formatting_without_changing_words(raw, expected):
    result = series_planner.normalize_plan(manifest(raw), {"entities": []})
    shot = result["scenes"][0]["shots"][0]
    assert shot["speaker"] == "mango"
    assert shot["dialogue"] == expected
    assert shot["duration_sec"] == 6.0
    assert shot["audio_mode"] == "native"


def test_planner_resolves_only_an_identified_single_speaker_prefix():
    result = series_planner.normalize_plan(manifest("Mango: You're kidding.", ""), {"entities": []})
    shot = result["scenes"][0]["shots"][0]
    assert shot["speaker"] == "mango"
    assert shot["dialogue"] == "You're kidding."


@pytest.mark.parametrize(
    "raw",
    [
        "Mango: You're kidding.\nBlueberry: No.",
        "Mango: You're kidding. Blueberry: No.",
        "Mango: You're kidding.\nStranger: No.",
        "Stranger: You're kidding.\nOther: No.",
        "Stranger: You're kidding. Other: No.",
        "Stranger: You're kidding Other: No.",
        "\u00d3scar: You're kidding Ana: No.",
        "Blueberry: No.",
        "<d>[English] You're kidding.</d><d>[English] No.</d>",
        "(whispering) You're kidding.",
        "You're kidding. [laughs]",
        "Say exactly: You're kidding.",
        "(S1) You're kidding.",
    ],
)
def test_planner_does_not_hide_multiple_voices_or_stage_instructions(raw):
    with pytest.raises(ValueError):
        series_planner.normalize_plan(manifest(raw), {"entities": []})


def test_planner_accepts_benign_formatting_without_regenerating_the_episode(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        return manifest('Mango: "<d>[English] You\'re\nkidding.</d>"')

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = asyncio.run(
        series_planner.plan_episode({"entities": [], "target_seconds": 6}, "A reversal.", "")
    )
    assert result["scenes"][0]["shots"][0]["dialogue"] == "You're kidding."
    assert len(requests) == 1


def test_planner_repairs_real_two_voice_shot_with_a_localized_instruction(monkeypatch):
    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        plan = manifest("Mango: You're kidding.\nBlueberry: No.")
        if len(requests) == 2:
            shot = plan["scenes"][0]["shots"][0]
            shot.update(dialogue="You're kidding.", duration_sec=3.4)
            reply = copy.deepcopy(shot)
            reply.update(speaker="blueberry", dialogue="No.", duration_sec=2.6)
            plan["scenes"][0]["shots"].append(reply)
        return plan

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    result = asyncio.run(
        series_planner.plan_episode({"entities": [], "target_seconds": 6}, "A reversal.", "")
    )
    assert [
        (s["speaker"], s["dialogue"], s["duration_sec"]) for s in result["scenes"][0]["shots"]
    ] == [
        ("mango", "You're kidding.", 3.4),
        ("blueberry", "No.", 2.6),
    ]
    assert "scenes[0].shots[0].dialogue" in requests[1][-1]["content"]
    assert "one speaker" in requests[1][-1]["content"]


def test_planner_keeps_repair_bounded_and_logs_the_rejected_field(monkeypatch, caplog):
    async def generate(messages, **options):
        return manifest("Mango: You're kidding.\nBlueberry: No.")

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    with pytest.raises(ValueError, match=r"scenes\[0\]\.shots\[0\]\.dialogue"):
        asyncio.run(
            series_planner.plan_episode({"entities": [], "target_seconds": 6}, "A reversal.", "")
        )
    assert "attempt=3" in caplog.text
    assert "Blueberry" in caplog.text


@pytest.mark.parametrize("speaker", ["Mango", "MANGO", " mango "])
def test_planner_resolves_character_names_to_canonical_keys(speaker):
    plan = manifest("You're kidding.", speaker)
    plan["scenes"][0]["shots"][0]["character_keys"] = ["Mango", "BLUEBERRY"]
    shot = series_planner.normalize_plan(plan, {"entities": []})["scenes"][0]["shots"][0]
    assert shot["speaker"] == "mango"
    assert shot["character_keys"] == ["mango", "blueberry"]
    assert shot["dialogue"] == "You're kidding."


def test_unknown_speaker_reports_the_field_and_valid_character_keys():
    with pytest.raises(ValueError) as caught:
        series_planner.normalize_plan(manifest("Hello.", "stranger"), {"entities": []})
    assert "scenes[0].shots[0].speaker" in str(caught.value)
    assert "stranger" in str(caught.value)
    assert "mango" in str(caught.value)
    assert "blueberry" in str(caught.value)


def test_duration_near_target_is_accepted_without_padding():
    plan = manifest("Hello.")
    shot = plan["scenes"][0]["shots"][0]
    plan["scenes"][0]["shots"] = [copy.deepcopy(shot) for _ in range(10)]
    plan["scenes"][0]["shots"][-1]["duration_sec"] = 5.8
    result = series_planner.normalize_plan(plan, {"entities": [], "target_seconds": 65})
    durations = [s["duration_sec"] for s in result["scenes"][0]["shots"]]
    assert durations == [6.0] * 9 + [5.8]
    assert sum(durations) == pytest.approx(59.8)


@pytest.mark.parametrize("language,label,dialogue", [
    ("en", "English",
     "Want to make videos too? Copy this video's link, like, subscribe, and comment GO!"),
    ("fr", "French", "Toi aussi, tu veux faire des vidéos ? "
     "Copie le lien de la vidéo, like, abonne-toi et commente GO !"),
    ("fr-FR", "French", "Toi aussi, tu veux faire des vidéos ? "
     "Copie le lien de la vidéo, like, abonne-toi et commente GO !"),
    ("ja", "Japanese", "あなたも動画を作りたい？この動画のリンクをコピーして、"
     "いいねと登録をして、GOとコメントしてね！"),
])
def test_each_episode_gets_a_fruit_outro_in_its_language(monkeypatch, language, label, dialogue):
    from calliope.studio.h3_prompt import compile_h3_prompt

    requests = []

    async def generate(messages, **options):
        requests.append(copy.deepcopy(messages))
        plan = manifest("Hello.", "Mango")
        shot = plan["scenes"][0]["shots"][0]
        plan["scenes"][0]["shots"] = [copy.deepcopy(shot) for _ in range(10)]
        plan["scenes"][0]["shots"][-1]["duration_sec"] = 5.8
        return plan

    monkeypatch.setattr(series_planner, "generate_structured", generate)
    context = {"entities": [], "target_seconds": 65, "language": language}
    result = asyncio.run(series_planner.plan_episode(context, "An unexpected discovery.", ""))
    assert len(requests) == 1
    assert len(result["scenes"]) == 2
    assert sum(s["duration_sec"] for s in result["scenes"][0]["shots"]) == pytest.approx(59.8)
    ending = result["scenes"][-1]["shots"]
    assert len(ending) == 1
    shot = ending[0]
    assert shot["dialogue"] == dialogue
    assert shot["speaker"] == "studio_cta_host"
    assert shot["character_keys"] == ["studio_cta_host"]
    assert shot["language"] == language.split("-")[0]
    host = next(e for e in result["entities"] if e["key"] == "studio_cta_host")
    assert "apple" in host["description"].lower()
    compiled = compile_h3_prompt(shot, context | {"entities": result["entities"]})
    assert f"<d>[{label}] {dialogue}</d>" in compiled
    assert "<d>" not in shot["dialogue"]
