# Studio IA — suite de la reprise Arena AI / pilote « Bodycount » (20 octobre 2026)

> **Fait suite à** `docs/studio/ARENA-REPRISE-BODYCOUNT-2026-10-20.md`.
> **Branche de travail** : `arena/fb4de4c0-studioia`.
> **Aucun calcul GPU payant, aucune dépense Modal/DeepSeek, aucune publication** n'ont été
> déclenchés par cette suite. DeepSeek (texte) reste autorisé pour la préparation créative,
> mais rien n'a été consommé ici : les contrats du pilote sont écrits et validés hors ligne.

## 1. Récupération du dernier code fonctionnel (sans perte)

- **Topologie.** La branche `arena/fb4de4c0-studioia` est issue d'un **merge** (`1920fb6`) des deux
  parents :
  - `07e92c3` — `arena/bodycount-recovery-20261020` : dossier de reprise + garde-fous QC (documentation) ;
  - `676f02d` — `arena/bodycount-local-20261020` : **dernier code fonctionnel** (WIP local du 19 octobre :
    QC OCR, cache de transcription, détection de gestes répétés, contrôle de synchro).
  Le merge a été fait avec `--allow-unrelated-histories -X theirs` après **examen des différences**
  (ordre 3.1/3.2 du dossier). Aucune branche ancienne n'a remplacé du code récent.
- **Garde-fous respectés** : pas de `git reset --hard`, pas de `git clean -fd`, pas de force-push.
  `feat/generalist-studio` (`3269424`) et `fix/studio-tts-voice-reference-20261018` (`f879cc3`,
  divergente/non testée) **n'ont pas été fusionnés**. La série « En prison », ses médias et ses
  références voix canoniques **n'ont pas été touchés**.
- **`work/`, `publish/`, `data/`** : absents de la racine, non importés. L'environnement de test
  `.venv-test/` est ajouté à `.gitignore` (jamais commité).

## 2. Les cinq correctifs demandés

Chaque défaut documenté dans le dossier de reprise est corrigé **à la source**, avec des tests qui
verrouillent le comportement et le principe « préférer un verdict *à revoir* à un faux succès ou un
faux rejet ».

### 2.1 Sous-titres parasites vs chiffres diégétiques (`generated_text.py`)
- Le scan OCR combine **deux références** : la **ligne de base** (texte de la première image = décor
  persistant, toléré) et la **liste blanche** des valeurs diégétiques déclarées (`allowed_forms`, avec
  variantes : zéros de leading, confusions d'afficheur à segments `0/O`, `1/l/I`).
- Un texte **nouveau** (absent de la ligne de base et non déclaré) vu dans **≥ 2 images** est refusé
  (`unexpected_text`) ; vu dans **1 image**, il est « à revoir » (`unverified`) ; OCR indisponible =
  `unverified`, **jamais** un succès.
- `allowed_display_tokens(context)` construit la liste blanche : valeurs/libellés des `screen_text` du
  plan + **seuls les chiffres** des états d'objets (contrat Director et plan). La prose des états ne
  blanchit aucun mot.
- **Revue supervisée** d'un faux positif : `override_valid(metadata, take_path)` exige un motif écrit
  (≥ 12 caractères) **et** le hash du fichier revu ; un override déjà valable n'exige pas de re-motif à
  la re-revue. `is_blocked(metadata, take_path)` pilote `guard_approval`, `verify_take` et `render_shot`.
- **Limite assumée** : un sous-titre incrusté **dès la première image** reste hors de portée de la ligne
  de base OCR → revue visuelle humaine obligatoire (signalée dans `limits`).

### 2.2 Transcription Whisper (`transcription.py`, `production.py`)
- `transcribe_dialogue` renvoie désormais un flag **`environment_failure`** sur tous les chemins :
  `True` pour un échec d'environnement (checkpoint absent/non configuré, langue manquante, Whisper
  indisponible), `False` pour un résultat honnête (`ok`, ou `unknown` d'analyse « parole incertaine ou
  absente »).
- Le **cache** de `production.assess_take` ne réutilise un résultat que si la signature du fichier est
  identique **et** (`status == 'ok'` **ou** `environment_failure is False`). Un échec d'environnement
  n'est donc **jamais mis en cache** : il est retenté après réparation du lanceur/checkpoint ; un
  résultat honnête reste en cache pour ne pas repayer Whisper à chaque contrôle.

### 2.3 Cohérence visuelle (`director.py`)
- `consistency_notes(value)` détecte une **contradiction** entre la composition t=0 (`first_frame`) et
  l'état d'un objet : si `first_frame` décrit l'état **final** d'un objet dont l'état initial déclaré
  diffère (la bouilloire « allumée, vapeur visible » alors que l'état initial est « éteinte » — défaut du
  job 681), une note est émise. Les chiffres sont comparés explicitement (un compteur qui **commence** à
  sa valeur finale est signalé). Une simple **omission** n'est pas signalée : le paquet d'images ajoute
  déjà la ligne autoritaire « État initial : … ».
- Branchée comme **garde réparable** en préparation (`prepare`) et dans la boucle de révision LLM
  (`revise`), et comme **avertissement** de `preflight` (revue humaine). **Jamais** de blocage
  rétroactif d'un contrat déjà approuvé (pas de rejet dur dans `validate_live`/`initialize`/édition
  experte), conformément au principe « à revoir plutôt que faux rejet ».
- `validate_render_contract` reçoit le `director_contract` dans son contexte de compilation.

### 2.4 Gestes répétés (`ltx_realization.py`, `ltx_prompt_v3.py`, `series_planner.py`)
- `repeated_pair(a, b)` / `find_repeated_events(actions)` : détection déterministe d'un même événement
  décrit deux fois. Règles : (A) similarité de contenu (Jaccard ≥ 0,6 sur les mots porteurs, à chiffres
  égaux) ; (B) même personnage attentif au même objet (acteurs + verbes d'attention bilatéraux + objet
  partagé, noms d'acteurs exclus des objets). **Garde chiffres** : des chiffres différents ne sont jamais
  un doublon (la progression `000 → 001 → 002` du compteur est légitime). `_actors` gère les noms
  accentués (Léa, Max).
- **Avant GPU** : `series_planner` appelle `find_repeated_events` sur la séquence d'actions **avant**
  `compile_video_prompt` ; un doublon lève une `_NativeValidationError` avec les **chemins** des champs
  en cause → réparation LLM **ciblée** (`_native_repair_fields`), paroles et locuteurs **verrouillés**.
- **Au rendu** : `_Seen` (déduplication exacte **et** quasi-sémantique) dans `ltx_prompt_v3` écarte une
  seconde formulation quasi identique d'un même geste.

### 2.5 Synchronisation des dialogues (`ltx_prompt_v3.py`, `production.py`)
- **Prompt** : le bloc de profil `brisk`/`brief` d'origine est conservé intact. Un **cue de synchro
  relatif et contractuel** est ajouté pour tout dialogue : si le début de la première réplique du contrat
  est ≤ 1,0 s → « The spoken exchange begins promptly as the shot opens, without a long silent lead-in. » ;
  sinon → « The spoken exchange begins only after a brief silent beat, then stays continuous. » Aucun
  **timing absolu** n'est envoyé au modèle (interdit par le linter LTX). Le cue est `optional=False` :
  il n'est jamais rogné par `_limit_words`.
- **Contrôle** : `production.apply_speech_onset_check(verdict, transcript, turns, timing_check)` mesure le
  début réel de parole et le compare au début déclaré par le contrat ; une **dérive > 1,0 s** rend la
  prise « à recaler » (`timing_mismatch`), sauf verdict déjà plus grave. Le contrôle ne s'applique que
  pour les profils sans timing absolu (LTX v3), avec un transcript fiable et un onset de contrat connu —
  sinon il est ignoré (jamais de faux rejet).

### 2.6 Continuité du compteur entre prises (`director.py`)
- `check_counter_continuity(plan_number, labels, previous_labels)` : pour un plan raccordé
  (`continue_from_previous`), la valeur initiale d'un compteur en surimpression doit égaler la valeur
  finale du plan précédent (dernier changement, sinon valeur affichée). Comparaison **exacte**, sans
  heuristique ; clés absentes ou sans valeur ignorées. Lève une 422 explicite en cas de rupture.

## 3. Distinction chiffres autorisés / sous-titres interdits

Le compteur de la fiction est un **accessoire physique diégétique** (rendu dans l'image), pas une
surimpression logicielle. Ses chiffres sont déclarés dans les états d'objets des contrats, ce qui
alimente **à la fois** :
- la **liste blanche OCR** (`allowed_display_tokens` → `000`, `001`, `002`) : le QC ne les confond pas
  avec un sous-titre ;
- la **clause de texte du prompt LTX** : « No subtitles, captions, speech bubbles, watermarks, logos or
  invented words anywhere in frame; the only visible characters are the numerals 000, 001 … on the small
  physical counter display described above. »

Ainsi l'OCR **et** le générateur partagent la même source de vérité : seuls les trois chiffres du
compteur sont autorisés à l'image.

## 4. Interface de revue supervisée (frontend)

- `director-api.ts` : `review(...)` accepte un `text_override_reason` optionnel (inclus dans le payload
  seulement s'il est renseigné — rétro-compatible) ; le type `verify` expose `vision`, `text_qc`,
  `text_qc_override`.
- `DirectorWorkflow.svelte` : quand la vérification signale un texte parasite (`text_qc.status ===
  'unexpected_text'`), un **textarea « Revue supervisée (motif du faux positif) »** apparaît ; le motif
  est transmis à la validation. Un override déjà enregistré est pré-rempli.
- `routers/studio_director.py` : `TakeReview.text_override_reason: str = Field(default="", max_length=600)`.

## 5. Outil de revue humaine d'une prise (`scripts/qc_take.py`)

Script **sans GPU ni dépense** qui contrôle UN MP4 et répond au point 3.5 du dossier :
```bash
cd calliope-backend
python scripts/qc_take.py <prise.mp4> --allowed 000 --allowed 001 --allowed 002 \
    --contract-onset 0.5 --language fr          # --json pour une sortie brute
```
Il croise l'OCR (sous-titres vs chiffres diégétiques) et Whisper (dérive du début de parole), puis rend
un verdict qui **privilégie « À REVOIR »** : `REJETÉE` (sous-titre parasite ou dérive > 1 s),
`À REVOIR` (OCR/Whisper indisponible, onset non mesurable), `CONTRÔLES AUTO OK` (validation humaine
requise). Code de sortie : `0` seulement si contrôles auto OK, sinon `1` — jamais un faux succès.

## 6. Tests — commandes et résultats exacts

Environnement : `.venv-test` (Python 3.11), `ffmpeg`/`ffprobe` locaux via `~/.local/bin`
(imageio-ffmpeg + @ffprobe-installer). Tesseract absent du bac à sable → l'OCR réel rend `unverified` ;
la logique OCR est testée par doubles (mocks).

**Suite backend complète** :
```bash
cd calliope-backend
PATH=~/.local/bin:$PATH ../.venv-test/bin/python -m pytest tests/ -q -p no:cacheprovider
```
> **Résultat : 1732 passed, 6 failed, 2 skipped** (503 s).

Les **6 échecs sont environnementaux**, sans lien avec les correctifs (aucun des modules touchés n'est
en cause) :
- **4 tests Windows-only** (chemins `E:\assets\…` que `os.path` Linux ne segmente pas) :
  `test_continuity.py::test_h3_messages_bind_neighbor_shots_and_keep_file_slots`,
  `test_h3_profile.py::test_resolve_h3_references_prefers_form_files_over_story`,
  `test_scene_settings.py::test_preview_prompt_uses_form_references_not_story_cast`,
  `test_workspace_files.py::test_path_containment_denials[read_file-args1]`.
- **2 tests version ffmpeg** (le binaire embarqué diffère de celui attendu) :
  `test_studio_pacing.py::test_a_good_take_is_approved_without_rerender_and_trimmed_usefully`
  (durée exportée 6,52 s vs 6,583 s attendue), `test_studio_export.py::test_scene_fade_preserves_duration_and_voice_start[False]`
  (filtre `xfade` : « inputs needs to be a constant frame rate »).

Baseline avant correctifs : 1662 passed / 10 failed / 2 skipped. Les 4 échecs restants corrigés étaient
les conflits de conception du cache de transcription (désormais alignés sur `environment_failure`).
L'augmentation à 1732 vient des ~66 nouveaux tests et des 4 tests de cache réparés.

**Tests ciblés** (tous verts) : `test_studio_generated_text.py` (13), `test_studio_ltx_realization.py` +
`test_studio_event_uniqueness.py` + `test_studio_authored_script.py` (25), `test_studio_ltx_prompt.py`
(14), `test_studio_transcription.py` (76), `test_studio_qc_take.py` (13),
`test_studio_compteur_pilote.py` (14), `test_studio_production_regressions.py` (23),
`test_studio_director.py` (71), `test_studio_integration.py` + `test_studio_director_speech_timing.py`.

**Frontend** :
```bash
cd calliope-web && npm ci && npm test          # Director client checks passed (payload review + text_override_reason)
npx svelte-kit sync && npx svelte-check         # 0 erreur (12 warnings préexistants, hors fichiers modifiés)
```

## 7. Pilote 15 s « COMPTEUR — Le compteur ne ment jamais »

Paquet prêt dans `docs/studio/compteur-pilote/` :
- `reference-contracts.json` — les **trois contrats Director** (prises A/B/C, 5 s chacune), entités
  (Léa, Max adultes ; studio ; compteur physique) et accessoires.
- `COMPTEUR-PILOTE-15S.md` — intentions, découpage, protocole de production, limites.

Validé **hors ligne** contre le moteur réel par `test_studio_compteur_pilote.py` (14 tests) :
- chaque contrat passe `validate_contract`, `validate_speech_timing`, et `consistency_notes == []`
  (aucune image de départ montrant l'état final, aucun geste répété) ;
- **continuité contractuelle** du compteur : `000 → 001 → 001 → 002`, chaque prise commençant à la
  valeur où la précédente finit ;
- les chiffres déclarés alimentent la liste blanche OCR (A `{000,001}`, B `{001}`, C `{001,002}`) ;
- le prompt LTX restreint le texte visible aux chiffres déclarés et cale le début des paroles sur le
  contrat : A/B (onset ≤ 1 s) « begins promptly », C (onset 2,8 s, après les trois coups) « brief silent
  beat » — le tout en ≤ 200 mots, sans timing absolu.

**Protocole de production** (rappel, sans gaspiller de GPU) : produire **uniquement la prise A (5 s)**,
la lire avec `scripts/qc_take.py`, **revue humaine**, puis — seulement si A est validée — produire B et
C, assembler ~15 s, préparer l'export 9:16. Budget ≤ 4 € (estimation ~3,1 € pour ~15,5 s). Aucune mise
en file des prises restantes par défaut.

## 8. Comportements modifiés à connaître

- **Les prompts des plans avec dialogue changent** (ajout du cue de synchro contractuel). Comme le
  `generation_id` inclut le prompt compilé, des prises **en attente** non approuvées peuvent être
  re-fileées. Les prises **déjà approuvées** (série « En prison ») ne sont **pas** re-validées ni
  invalidées : `reusable_video` ne revérifie le prompt que pour les prises non approuvées.
- Le contrôle OCR réel rend `unverified` quand Tesseract est absent : c'est un « à revoir » explicite,
  jamais un faux succès. Brancher Tesseract dans l'environnement du Studio lève cette limite.

## 9. Problèmes restant ouverts

1. **Tesseract non installé** dans ce bac à sable : l'OCR réel des prises rend `unverified`. La logique
   (liste blanche, variantes, refus ≥ 2 images, override supervisé) est testée par mocks. À installer
   dans l'environnement de production pour un contrôle OCR effectif.
2. **Limite OCR de première image** : un sous-titre incrusté dès la frame 0 n'est pas détecté par la
   ligne de base → revue visuelle humaine obligatoire (documentée dans `limits` du scan).
3. **Prise bodycount réelle non rejouée ici** : les MP4 produits sur le PC (série 33 / projet 47, jobs
   680/681, prises 429/430/431) sont inaccessibles dans ce bac à sable et le serveur local n'a pas été
   redémarré. Le premier vrai test (point 3.5) — lire le MP4 de la première prise conservée, inspecter
   sous-titres/onset/identité/compteur, contrôle humain — reste à faire sur le PC, avec `scripts/qc_take.py`.
4. **6 échecs environnementaux** (4 Windows-only, 2 version ffmpeg) : non bloquants, à exécuter sur
   l'environnement cible (Windows + ffmpeg attendu) pour une suite entièrement verte.
5. **Étape GPU non franchie** : conformément aux garde-fous, aucune vidéo du pilote n'a été générée. Le
   pilote est prêt à produire (contrats validés), mais la preuve technique (prise A rendue + revue)
   reste à faire avec validation explicite du coût.

## 10. Garde-fous — état

| Garde-fou | État |
|-----------|------|
| Pas de GPU / dépense Modal / DeepSeek | Respecté (rien consommé) |
| « En prison » préservé (médias, voix canoniques) | Non touché |
| Pas de `reset --hard` / `clean -fd` / force-push | Respecté |
| Pas de fusion dans `feat/generalist-studio` | Respecté |
| `work/`, `publish/`, `data/` non importés | Absents / non committés |
| `.venv-test/` non committé | Ajouté à `.gitignore` |
| Récupération sans perte du dernier code fonctionnel | Merge `07e92c3 × 676f02d` examiné |
