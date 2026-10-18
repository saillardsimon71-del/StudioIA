from calliope.studio import editorial, ltx_realization


def plan(shots, post_dub=True):
    return {
        "post_dub": post_dub,
        "scenes": [{"shots": shots}],
    }


def shot(duration, turns=None, purpose=""):
    return {
        "duration_sec": duration,
        "dialogue_turns": turns or [],
        "purpose": purpose,
    }


def test_five_long_takes_pass():
    turns = [{"speaker": "durio", "text": "A"}, {"speaker": "momo", "text": "B"}]
    result = editorial.validate_plan(plan([
        shot(14, turns), shot(11, turns), shot(11, turns), shot(2.7), shot(3.4, purpose="outro"),
    ]))
    assert result["ok"] is True
    assert result["cuts"] == 4
    assert result["errors"] == []


def test_cut_per_line_fails():
    result = editorial.validate_plan(plan([shot(3, purpose="outro" if i == 14 else "")
                                           for i in range(15)]))
    assert result["ok"] is False
    assert any("Trop de coupes" in error for error in result["errors"])


def test_long_cta_fails():
    result = editorial.validate_plan(plan([
        shot(14), shot(11), shot(11), shot(2.7), shot(6.0, purpose="outro"),
    ]))
    assert result["ok"] is False
    assert any("CTA trop long" in error for error in result["errors"])


def test_multi_speaker_without_post_dub_warns():
    turns = [{"speaker": "durio", "text": "A"}, {"speaker": "momo", "text": "B"}]
    result = editorial.validate_plan(plan([shot(14, turns), shot(3, purpose="outro")], post_dub=False))
    assert any("post-doublage" in warning for warning in result["warnings"])


def test_post_dub_keeps_multi_speaker_shot_whole():
    turns = [
        {"speaker": "durio", "text": "A", "action": "Durio parle."},
        {"speaker": "momo", "text": "B", "reaction": "Momo répond."},
        {"speaker": "durio", "text": "C", "action": "Durio insiste."},
    ]
    source = plan([shot(14, turns)])
    prepared = ltx_realization.prepare_ltx_plan(source)
    assert len(prepared["scenes"][0]["shots"]) == 1
    assert prepared["scenes"][0]["shots"][0]["dialogue_turns"] == turns
