import csv
import io
import os
import shutil
import subprocess

import cv2


def words(tsv):
    found = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        text = (row.get('text') or '').strip()
        if (row.get('level') == '5' and float(row.get('conf') or -1) >= 80
                and sum(char.isalnum() for char in text) >= 3):
            found.append(text)
    return found


def sample_text(path, binary):
    video = cv2.VideoCapture(str(path))
    try:
        frames = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
        if frames < 2:
            raise ValueError('Images vidéo illisibles.')
        result = []
        count = min(frames, 8)
        for index in range(count):
            video.set(cv2.CAP_PROP_POS_FRAMES, round(index * (frames - 1) / (count - 1)))
            valid, frame = video.read()
            if not valid:
                raise ValueError('Image vidéo illisible.')
            height, width = frame.shape[:2]
            if height > 960:
                frame = cv2.resize(frame, (round(width * 960 / height), 960))
            valid, png = cv2.imencode('.png', frame)
            if not valid:
                raise ValueError('Image OCR inexploitable.')
            process = subprocess.run(
                [binary, 'stdin', 'stdout', '--psm', '11', 'tsv'],
                input=png.tobytes(), capture_output=True, check=True, timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0,
            )
            result.append(words(process.stdout.decode('utf-8', errors='replace')))
        return result
    finally:
        video.release()


def scan(path):
    binary = shutil.which('tesseract')
    if not binary:
        return {'status': 'unverified', 'reason': 'Tesseract local indisponible.'}
    try:
        samples = sample_text(path, binary)
        if len(samples) < 2:
            raise ValueError('Échantillonnage OCR incomplet.')
    except (OSError, subprocess.SubprocessError, ValueError, TimeoutError) as exc:
        return {'status': 'unverified', 'reason': f'OCR indisponible ({type(exc).__name__}).'}
    initial = {word.casefold() for word in samples[0]}
    hits = [{'sample': index, 'words': [word for word in sample if word.casefold() not in initial]}
            for index, sample in enumerate(samples[1:], 1)]
    hits = [hit for hit in hits if hit['words']]
    status = 'unexpected_text' if len(hits) >= 2 else 'unverified' if hits else 'not_detected'
    return {
        'status': status, 'sampled_frames': len(samples), 'hits': hits,
        'reason': ('Nouveau texte détecté dans plusieurs images de la prise native.' if len(hits) >= 2
                   else 'Texte suspect dans une image : revue requise.' if hits
                   else 'Aucun nouveau texte détecté dans les images échantillonnées.'),
        'limits': 'OCR partiel : ne vérifie ni identité, ni lip-sync, ni tous les photogrammes.',
    }
