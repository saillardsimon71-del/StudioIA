import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import export as export_module
from core.config import V2Settings
from core.countdown import countdown_overlays
from core.ltx_prompt import compile_shot_prompt
from core.scenario import DisplayOverlay, Project, Shot
from llm import deepseek


def _draft(*, narration_mode="dialogue", dialogue=None):
    return {
        "title": "Le compte à rebours",
        "narration_mode": narration_mode,
        "characters": [{
            "id": "person", "name": "Person", "description": "A person in a dark room.",
        }],
        "shots": [{
            "id": "shot_1", "duration_s": 5,
            "initial_state": "Person stands by the timer.",
            "action": "The clock ticks; rain and low room ambience continue.",
            "final_state": "Person remains beside the timer.",
            "camera_setup": "Static medium shot",
            "dialogue": dialogue or [],
        }],
    }


def _respond(draft):
    return lambda request: {"choices": [{"message": {"content": json.dumps(draft)}}]}


def test_explicit_silence_overrides_director_mode_and_prevents_spoken_audio():
    draft = _draft(narration_mode="voice_over", dialogue=[{
        "speaker_id": "person", "text": "Il ne reste que quelques secondes.",
        "delivery": "voice_over", "start_sec": 0,
    }])
    project, _ = deepseek.generate_scenario_once(
        settings=V2Settings(), idea="Aucun dialogue ni voix off. Conserver la pluie et les sons d'ambiance.",
        target_duration_s=5, transport_fn=_respond(draft),
    )

    assert project.narration_mode == "dialogue"  # Existing enum value; silence is represented by no lines.
    assert all(not shot.dialogue for shot in project.shots)
    for shot in project.shots:
        prompt = compile_shot_prompt(project, shot)
        assert "Characters stay silent" in prompt
        assert "Il ne reste que quelques secondes" not in prompt
        assert "rain and low room ambience" in prompt


def test_dialogue_and_voice_over_scenarios_keep_their_existing_audio_behavior():
    spoken = [{"speaker_id": "person", "text": "Quelqu'un arrive.", "delivery": "on_screen", "start_sec": 0}]
    project, _ = deepseek.generate_scenario_once(
        settings=V2Settings(), idea="Une scène avec dialogue.", target_duration_s=5,
        transport_fn=_respond(_draft(narration_mode="dialogue", dialogue=spoken)),
    )
    assert project.narration_mode == "dialogue"
    assert project.shots[0].dialogue[0].text == "Quelqu'un arrive."
    assert 'Person says' in compile_shot_prompt(project, project.shots[0])

    voice_over = [{
        "speaker_id": "person", "text": "La nuit tombe.",
        "delivery": "voice_over", "start_sec": 0,
    }]
    narrated, _ = deepseek.generate_scenario_once(
        settings=V2Settings(), idea="Un récit avec narration en voix off.", target_duration_s=5,
        transport_fn=_respond(_draft(narration_mode="voice_over", dialogue=voice_over)),
    )
    assert narrated.narration_mode == "voice_over"
    assert "Voice-over" in compile_shot_prompt(narrated, narrated.shots[0])


def _countdown_project():
    durations = [8, 8, 8, 8, 8, 7, 11, 2]
    shots = []
    for index, duration in enumerate(durations):
        black_final = index == 7
        shots.append(Shot(
            id=f"shot_{index + 1}", duration_s=duration,
            initial_state=(
                "A pure black screen." if black_final
                else f"A timer reads {60 - sum(durations[:index])}."
            ),
            action="The timer counts down." if not black_final else "Hold on a pure black screen.",
            final_state="A pure black screen." if black_final else "The timer continues.",
            display_overlay=(DisplayOverlay(initial_text="60", x=0.5, y=0.2) if index == 0 else None),
        ))
    return Project(
        id="countdown", title="Compte à rebours",
        idea="Un compte à rebours de 60 secondes, puis un écran noir final.",
        target_duration_s=60, shots=shots,
    )


def test_countdown_is_global_and_continuous_across_shot_cuts_and_black_tail():
    project = _countdown_project()
    overlays = countdown_overlays(project)
    assert overlays is not None
    assert [overlay.initial_text if overlay else None for overlay in overlays] == [
        "60", "52", "44", "36", "28", "20", "13", None,
    ]
    # Plan 6 runs from 20 to the next global tick, and plan 7 resumes at 13,
    # then changes to 12 ... 3 without resetting at the edit.
    assert [(change.at_sec, change.text) for change in overlays[5].changes][-1] == (6.0, "14")
    assert [(change.at_sec, change.text) for change in overlays[6].changes][:2] == [(1.0, "12"), (2.0, "11")]
    assert overlays[6].changes[-1].text == "3"
    assert overlays[7] is None
    assert all(change.text != "0" for overlay in overlays if overlay for change in overlay.changes)


def test_brief_countdown_value_wins_over_inconsistent_shot_overlay():
    project = _countdown_project()
    project.shots[0].display_overlay = DisplayOverlay(initial_text="20")
    overlays = countdown_overlays(project)
    assert overlays[0].initial_text == "60"


def test_countdown_uses_elapsed_fractional_clip_time_at_a_cut():
    project = Project(
        id="fractional", idea="A countdown from 4 seconds; end on a black screen.",
        target_duration_s=4, shots=[
            Shot(id="a", duration_s=1.5, action="Timer ticks."),
            Shot(id="b", duration_s=1.75, action="Timer continues."),
            Shot(id="black", duration_s=0.75, action="Hold on black.", initial_state="Black screen."),
        ],
    )
    overlays = countdown_overlays(project, durations_s=[1.5, 1.75, 0.75])
    assert overlays is not None
    assert overlays[1].initial_text == "3"  # ceil(4 - 1.5), not a reset to 4.
    assert [(change.at_sec, change.text) for change in overlays[1].changes] == [
        (0.5, "2"), (1.5, "1"),
    ]
    assert overlays[2] is None


def test_countdown_preserves_opening_hook_and_non_countdown_projects_are_untouched():
    project = _countdown_project()
    project.shots[0].display_overlay = DisplayOverlay(initial_text="NE RESTE QUE 60 SECONDES", x=0.45)
    overlays = countdown_overlays(project)
    assert overlays[0].initial_text == "NE RESTE QUE 60 SECONDES"
    assert overlays[0].changes[0].at_sec == 3.0
    assert overlays[0].changes[0].text == "57"

    ordinary = project.model_copy(update={"idea": "Une scène nocturne sans compteur."})
    assert countdown_overlays(ordinary) is None
    assert not any("compte" in (shot.action or "") for shot in ordinary.shots)


def test_export_applies_timeline_generated_counter_through_existing_ass_filter(tmp_path, monkeypatch):
    project = Project(
        id="render-countdown", idea="Un compte à rebours de 4 secondes puis écran noir final.",
        target_duration_s=4, shots=[
            Shot(
                id="first", duration_s=2, action="Timer ticks.",
                rendered_video_ref=str(tmp_path / "first.mp4"),
            ),
            Shot(id="black", duration_s=2, action="Hold on black.", initial_state="Pure black screen.",
                 rendered_video_ref=str(tmp_path / "black.mp4")),
        ],
    )
    for name in ("first.mp4", "black.mp4"):
        (tmp_path / name).write_bytes(b"input")

    monkeypatch.setattr(export_module, "ffmpeg_exe", lambda: "ffmpeg")
    monkeypatch.setattr(export_module, "inspect_media", lambda path: {
        "duration_s": 2.0, "has_audio": True,
    })
    commands = []
    ass_contents = []

    def fake_run(command, cwd):
        commands.append(command)
        vf_index = command.index("-vf") if "-vf" in command else None
        if vf_index is not None:
            ass_filter = command[vf_index + 1]
            if "ass=filename=" in ass_filter:
                ass_contents.append((ass_filter, (cwd / "overlay_0.ass").read_text(encoding="utf-8")))
        if str(command[-1]).endswith("assembled.mp4"):
            Path(command[-1]).write_bytes(b"assembled")

    qc_shots = []
    monkeypatch.setattr(export_module, "_run_cmd", fake_run)
    monkeypatch.setattr(export_module, "run_export_qc", lambda path, shots, settings, expected_size: (
        qc_shots.extend(shots) or {"ok": True}
    ))
    settings = V2Settings(data_dir=tmp_path / "data")
    output, report = export_module.export_project_mp4(project, settings, output_path=tmp_path / "out.mp4")

    assert output.read_bytes() == b"assembled" and report["ok"]
    assert len(ass_contents) == 1
    ass_filter, ass_text = ass_contents[0]
    assert "ass=filename=overlay_0.ass" in ass_filter
    assert "Dialogue: 0,0:00:00.00,0:00:01.00" in ass_text
    assert "}4" in ass_text and "}3" in ass_text
    # The generated countdown is used by export QC as the accepted overlay, while
    # the persisted Project object and its references remain untouched.
    assert qc_shots[0].display_overlay.initial_text == "4"
    assert qc_shots[1].display_overlay is None
    assert project.shots[0].display_overlay is None
    assert project.shots[0].rendered_video_ref == str(tmp_path / "first.mp4")
    assert all("ass=filename=" not in command[command.index("-vf") + 1]
               for command in commands if "-vf" in command and "segment_0001" in command[-1])
