import pytest

from calliope.studio import generated_text


def tsv(text='Dourze', confidence=91):
    return 'level\tconf\ttext\n5\t' + str(confidence) + '\t' + text + '\n'


def test_ocr_requires_confident_words_not_scattered_symbols():
    assert generated_text.words(tsv()) == ['Dourze']
    for text, confidence in [('P|', 95), ('it', 95), ('Dourze', 30), ('?', 98)]:
        assert generated_text.words(tsv(text, confidence)) == []


@pytest.mark.parametrize('detections,expected', [
    ([[], [], ['Dourze'], ['Dourze']], 'unexpected_text'),
    ([[], [], ['Dourze'], []], 'unverified'),
    ([[], [], [], []], 'not_detected'),
    ([['Kitchen'], ['Kitchen'], ['Kitchen'], ['Kitchen']], 'not_detected'),
])
def test_scan_requires_new_text_in_multiple_frames(monkeypatch, detections, expected):
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    monkeypatch.setattr(generated_text, 'sample_text', lambda *args: detections)
    result = generated_text.scan('take.mp4')
    assert result['status'] == expected
    assert result['sampled_frames'] == 4


def test_declared_counter_digits_are_not_subtitles(monkeypatch):
    # Le compteur de la fiction passe de 000 à 001 en cours de prise :
    # les chiffres déclarés ne doivent jamais être pris pour un sous-titre.
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    monkeypatch.setattr(
        generated_text, 'sample_text',
        lambda *args: [['000'], ['000'], ['001'], ['001']])
    result = generated_text.scan('take.mp4', allowed=['000', '001', '002'])
    assert result['status'] == 'not_detected'
    assert result['allowed_count'] > 0


def test_undeclared_counter_change_is_flagged(monkeypatch):
    # Sans déclaration, un chiffre qui change est un texte nouveau suspect.
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    monkeypatch.setattr(
        generated_text, 'sample_text',
        lambda *args: [['000'], ['000'], ['001'], ['001']])
    assert generated_text.scan('take.mp4')['status'] == 'unexpected_text'


def test_subtitle_words_are_flagged_next_to_allowed_digits(monkeypatch):
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    monkeypatch.setattr(
        generated_text, 'sample_text',
        lambda *args: [['000'], ['000'], ['Bonjour', '001'], ['Bonjour', '001']])
    result = generated_text.scan('take.mp4', allowed=['001'])
    assert result['status'] == 'unexpected_text'
    assert all(hit['words'] == ['Bonjour'] for hit in result['hits'])


def test_digit_variants_are_tolerated(monkeypatch):
    # Zéros de tête et confusions OCR (0/O, 1/l/I) d'un afficheur à segments.
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    monkeypatch.setattr(
        generated_text, 'sample_text',
        lambda *args: [['OOO'], ['000'], ['0O1'], ['00l']])
    assert generated_text.scan('take.mp4', allowed=['0', '1'])['status'] == 'not_detected'


def test_allowed_display_tokens_mix_overlay_labels_and_state_digits():
    context = {
        'shot': {
            'screen_text': [{'key': 'bodycount', 'text': '', 'value': 1,
                             'changes': [{'at_sec': 4.5, 'value': 2}]}],
            'start_state': {'compteur': 'affichant 000'},
            'end_state': {'compteur': 'affichant 001'},
        },
        'director_contract': {
            'initial_state': {'compteur': 'display reads 000', 'lea': 'assise, calme'},
            'final_state': {'compteur': 'display reads 001'},
        },
    }
    tokens = generated_text.allowed_display_tokens(context)
    assert {'1', '2', '000', '001'} <= set(tokens)
    # La prose des états ne blanchit aucun mot : seuls les chiffres.
    assert 'assise' not in tokens and 'display' not in tokens
    forms = generated_text.allowed_forms(tokens)
    assert {'000', '001', '0', '1', '2'} <= forms


def test_text_override_requires_a_motivated_review(tmp_path):
    from calliope.studio.visual_identity import file_hash

    take = tmp_path / 'take.bin'
    take.write_bytes(b'native take')
    blocked = {'text_qc': {'status': 'unexpected_text'}}
    assert generated_text.is_blocked(blocked, str(take))
    short = blocked | {'text_qc_override': {'reason': 'ok', 'file_hash': file_hash(str(take))}}
    assert generated_text.is_blocked(short, str(take))
    reviewed = blocked | {'text_qc_override': {
        'reason': 'Faux positif OCR vérifié image par image : motif de la couverture.',
        'file_hash': file_hash(str(take))}}
    assert not generated_text.is_blocked(reviewed, str(take))
    # Une prise remplacée invalide la revue supervisée précédente.
    assert generated_text.is_blocked(reviewed, None) is False
    other = tmp_path / 'other.bin'
    other.write_bytes(b'another take')
    assert generated_text.is_blocked(reviewed, str(other))
    assert not generated_text.is_blocked({'text_qc': {'status': 'not_detected'}}, str(take))


def test_missing_ocr_is_explicitly_unverified(monkeypatch):
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: None)
    result = generated_text.scan('take.mp4')
    assert result['status'] == 'unverified'
    assert 'Tesseract' in result['reason']


def test_partial_ocr_failure_cannot_be_reported_clear(monkeypatch):
    monkeypatch.setattr(generated_text.shutil, 'which', lambda _: 'tesseract')
    def unavailable(*args):
        raise TimeoutError('OCR timeout')
    monkeypatch.setattr(generated_text, 'sample_text', unavailable)
    assert generated_text.scan('take.mp4')['status'] == 'unverified'
