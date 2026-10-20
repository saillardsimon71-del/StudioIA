"""QC OCR des prises natives : sous-titres parasites contre texte diégétique.

Politique (dossier de reprise du 20 octobre 2026) :
- aucun texte n'est autorisé dans une prise native, SAUF les valeurs
  diégétiques déclarées (chiffres du compteur de la fiction, libellés
  d'overlays annoncés par le plan) ;
- les chiffres du compteur ne doivent jamais être confondus avec un
  sous-titre parasite : les valeurs déclarées forment une liste blanche,
  avec variantes OCR (zéros de tête, confusions 0/O, 1/l/I) ;
- un texte non autorisé vu dans une seule image est « à revoir »
  (``unverified``) ; vu dans au moins deux images, il est refusé
  (``unexpected_text``) ; l'OCR indisponible est explicitement
  ``unverified``, jamais un succès.

Une revue humaine supervisée peut écarter un faux positif OCR en
enregistrant ``text_qc_override`` avec une raison écrite et le hash du
fichier revu (voir ``director.review_take``).
"""

import csv
import io
import os
import re
import shutil
import subprocess
import unicodedata

import cv2


def words(tsv):
    found = []
    for row in csv.DictReader(io.StringIO(tsv), delimiter='\t'):
        text = (row.get('text') or '').strip()
        if (row.get('level') == '5' and float(row.get('conf') or -1) >= 80
                and sum(char.isalnum() for char in text) >= 3):
            found.append(text)
    return found


def _fold(word):
    """Normalise un mot : sans accents, minuscule, sans ponctuation."""
    folded = unicodedata.normalize('NFKD', str(word or ''))
    folded = ''.join(char for char in folded if not unicodedata.combining(char))
    return re.sub(r'[^a-z0-9]', '', folded.casefold())


# Confusions classiques d'un afficheur à segments lu par OCR : 0/O, 1/l/I.
_SEGMENT_CONFUSIONS = str.maketrans({'o': '0', 'l': '1', 'i': '1'})


def _variants(token):
    """Variantes acceptables d'un jeton OCR ou déclaré.

    Un chiffre diégétique peut être lu avec des zéros de tête retirés ou
    ajoutés (``001`` / ``1``) et avec des lettres confondues (``0O1``).
    """
    token = _fold(token)
    if not token:
        return set()
    forms = {token}
    if re.fullmatch(r'[0-9]{1,8}', token):
        forms.add(token.lstrip('0') or '0')
    if re.fullmatch(r'[0-9oli]{1,8}', token):
        mapped = token.translate(_SEGMENT_CONFUSIONS)
        forms.add(mapped)
        forms.add(mapped.lstrip('0') or '0')
    return forms


def allowed_forms(allowed):
    """Liste blanche normalisée à partir des valeurs diégétiques déclarées."""
    forms = set()
    for item in allowed or ():
        for token in re.split(r'[^A-Za-z0-9]+', str(item or '')):
            forms |= _variants(token)
        for number in re.findall(r'\d+', str(item or '')):
            forms |= _variants(number)
    return {form for form in forms if form}


def allowed_display_tokens(context):
    """Valeurs diégétiques déclarées pour une prise : overlays et états.

    - texte et valeurs des surimpressions ``screen_text`` du plan (libellés
      annoncés, valeur initiale et changements) ;
    - seuls les CHIFFRES des états d'objets (contrat Director et plan) :
      le compteur physique de la fiction déclare ses valeurs dans ses états,
      la prose des états ne doit pas blanchir des mots parasites.
    """
    context = context or {}
    shot = context.get('shot') or {}
    tokens = []
    for label in shot.get('screen_text') or []:
        if label.get('text'):
            tokens.append(str(label['text']))
        if label.get('value') is not None:
            tokens.append(str(label['value']))
        for change in label.get('changes') or []:
            if change.get('value') is not None:
                tokens.append(str(change['value']))
    contract = context.get('director_contract') or {}
    for source in (
        contract.get('initial_state'), contract.get('final_state'),
        shot.get('start_state'), shot.get('end_state'),
    ):
        for value in (source or {}).values():
            tokens.extend(re.findall(r'\d+', str(value)))
    return tokens


def override_valid(metadata, take_path=None):
    """Une revue supervisée n'est valable que motivée et à fichier inchangé."""
    override = (metadata or {}).get('text_qc_override') or {}
    reason = str(override.get('reason') or '').strip()
    if len(reason) < 12:
        return False
    if take_path and override.get('file_hash'):
        from calliope.studio.visual_identity import file_hash
        try:
            return override['file_hash'] == file_hash(take_path)
        except OSError:
            return False
    return True


def is_blocked(metadata, take_path=None):
    """Texte parasite non résolu : refus automatique, sauf revue supervisée."""
    text_qc = (metadata or {}).get('text_qc') or {}
    if text_qc.get('status') != 'unexpected_text':
        return False
    return not override_valid(metadata, take_path)


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


def scan(path, allowed=()):
    """Détecte du texte parasite dans les images d'une prise native.

    Deux références combinées, pour éviter à la fois faux succès et faux
    rejets :
    - la première image sert de ligne de base : le texte de décor persistant
      (enseigne, etc.) n'est pas un sous-titre parasite ;
    - la liste blanche des valeurs diégétiques déclarées (chiffres du
      compteur) : un compteur qui passe de 001 à 002 en cours de prise est
      une progression autorisée, pas un sous-titre.

    Tout texte NOUVEAU (absent de la première image et non déclaré) vu dans
    au moins deux images est refusé (``unexpected_text``) ; vu dans une seule
    image, il est « à revoir » (``unverified``). Un sous-titre incrusté dès la
    première image reste une limite de l'OCR, à trancher par la revue visuelle
    humaine obligatoire — signalée dans ``limits``.
    """
    binary = shutil.which('tesseract')
    if not binary:
        return {'status': 'unverified', 'reason': 'Tesseract local indisponible.'}
    try:
        samples = sample_text(path, binary)
        if len(samples) < 2:
            raise ValueError('Échantillonnage OCR incomplet.')
    except (OSError, subprocess.SubprocessError, ValueError, TimeoutError) as exc:
        return {'status': 'unverified', 'reason': f'OCR indisponible ({type(exc).__name__}).'}
    permitted = allowed_forms(allowed)
    baseline = set()
    for word in samples[0]:
        baseline |= _variants(word)

    def tolerated(word):
        variants = _variants(word)
        return bool(variants & permitted) or bool(variants & baseline)

    hits = [{'sample': index, 'words': [word for word in sample if not tolerated(word)]}
            for index, sample in enumerate(samples[1:], 1)]
    hits = [hit for hit in hits if hit['words']]
    status = 'unexpected_text' if len(hits) >= 2 else 'unverified' if hits else 'not_detected'
    return {
        'status': status, 'sampled_frames': len(samples), 'hits': hits,
        'allowed_count': len(permitted),
        'reason': ('Texte parasite détecté dans plusieurs images de la prise native.'
                   if len(hits) >= 2
                   else 'Texte suspect dans une image : revue requise.' if hits
                   else 'Aucun texte parasite détecté dans les images échantillonnées.'),
        'limits': 'OCR partiel : ne vérifie ni identité, ni lip-sync, ni un sous-titre '
                  'incrusté dès la première image, ni tous les photogrammes.',
    }
