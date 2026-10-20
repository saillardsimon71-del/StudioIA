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
