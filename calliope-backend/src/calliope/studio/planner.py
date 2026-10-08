import re

from calliope.agent.llm import generate_structured

HEADING = re.compile(r"^(?:INT\.?\s|EXT\.?\s|INT/EXT\.?\s|SC[ÈE]NE\s+\d|#{1,3}\s)", re.I)
SPEECH = re.compile(r"^([\wÀ-ÿ][\wÀ-ÿ .-]{0,40}):\s*(.+)$")
WORDS_PER_SEC = 2.2


def beat_duration(dialogue="", fallback=4.0):
    """Durée utile d'un plan : le temps honnête de sa réplique, à 0,1 s près.

    Le découpage n'étire jamais un plan pour remplir ; sans réplique, un beat
    d'action sobre vaut environ quatre secondes.
    """
    words = len(str(dialogue or "").split())
    if not words:
        return fallback
    return max(1.0, min(15.0, round(words / WORDS_PER_SEC + 0.3, 1)))


def _chunks(text, words=30):
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    result, current = [], ""
    for sentence in sentences:
        if not sentence.strip():
            continue
        pieces = sentence.split()
        while len(pieces) > words:
            if current:
                result.append(current)
                current = ""
            result.append(" ".join(pieces[:words]))
            pieces = pieces[words:]
        sentence = " ".join(pieces)
        if current and len((current + " " + sentence).split()) > words:
            result.append(current)
            current = sentence
        else:
            current = (current + " " + sentence).strip()
    if current:
        result.append(current)
    return result


def parse_script(text):
    groups = []
    heading, lines = "Scène 1", []
    for line in text.strip().splitlines():
        if HEADING.match(line.strip()):
            if lines:
                groups.append((heading, lines))
            heading, lines = line.strip().lstrip("#").strip(), []
        else:
            lines.append(line.strip())
    if lines:
        groups.append((heading, lines))
    scenes = []
    for heading, lines in groups:
        action, dialog = [], []
        for line in lines:
            if SPEECH.match(line):
                dialog.append(line)
            elif line:
                action.append(line)
        action_text, dialog_text = "\n".join(action), "\n".join(dialog)
        actions = _chunks(action_text) or [heading]
        speeches = []
        for line in dialog:
            speaker, speech = SPEECH.match(line).groups()
            speeches.extend(f"{speaker}: {part}" for part in _chunks(speech, words=11))
        count = max(len(actions), len(speeches), 1)
        shots = []
        for index in range(count):
            description = actions[min(index, len(actions) - 1)]
            dialogue = speeches[index] if index < len(speeches) else ""
            shots.append(
                {
                    "description": description,
                    "prompt": description,
                    "image_prompt": description,
                    "dialogue": dialogue,
                    "duration_sec": beat_duration(dialogue),
                    "audio_mode": "native",
                }
            )
        scenes.append(
            {"heading": heading, "action": action_text, "dialog": dialog_text, "shots": shots}
        )
    if not scenes:
        raise ValueError("Saisissez un script ou une description de plan.")
    return scenes


async def plan_with_llm(text, mode):
    prompt = """Tu prépares un découpage vidéo généraliste: fiction, animation,
publicité, documentaire, sketch, paysage ou autre. Respecte exclusivement la
demande, sa langue, ses personnages, son style et son dialogue. Ne force aucun
genre. Pour un script fourni, conserve les faits et les paroles exactes. Pour
une idée, développe une courte proposition filmable. Découpe les scènes complexes
en plans autonomes dont la durée utile varie librement entre 1 et 15 secondes
selon la réplique ou l'action: le plan se termine dès que son beat est fini,
jamais de temps mort. Une réplique par plan, au maximum 2 mots par seconde de
plan. Pas de paroles si elles ne sont pas demandées. Prompt visuel
et image_prompt en anglais pour H3, paroles dans leur langue originale.
Retourne uniquement un objet JSON: {"scenes":[{"heading":"titre de scène",
"action":"action", "dialog":"dialogue complet", "shots":[{"description":
"description française", "prompt":"visible action, camera, light, audio",
"image_prompt":"still reference prompt", "dialogue":"locuteur: réplique",
"duration_sec":3.4, "audio_mode":"native"}]}]}. Ne retourne aucun Markdown."""
    result = await generate_structured(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"Mode: {mode}\n{text}"},
        ],
        temperature=0.4,
    )
    scenes = result.get("scenes")
    if not isinstance(scenes, list) or not 1 <= len(scenes) <= 100:
        raise ValueError("L'IA n'a pas renvoyé une liste de scènes valide.")
    for scene in scenes:
        if not isinstance(scene, dict) or not isinstance(scene.get("shots"), list):
            raise ValueError("L'IA a renvoyé un découpage incomplet.")
        if not 1 <= len(scene["shots"]) <= 100:
            raise ValueError("Une scène doit contenir entre 1 et 100 plans.")
        for shot in scene["shots"]:
            if not isinstance(shot, dict) or not str(shot.get("prompt") or "").strip():
                raise ValueError("L'IA a renvoyé un plan sans prompt.")
            try:
                duration = float(shot.get("duration_sec") or 0)
            except (TypeError, ValueError):
                duration = 0
            if not 1 <= duration <= 15:
                raise ValueError("Chaque plan doit durer entre 1 et 15 secondes utiles.")
            shot["duration_sec"] = round(duration, 1)
            shot["audio_mode"] = "silent" if shot.get("audio_mode") == "silent" else "native"
    return scenes
