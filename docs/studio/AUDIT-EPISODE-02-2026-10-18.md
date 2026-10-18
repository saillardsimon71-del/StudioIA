# Audit de l'épisode 2 V2 — 18 octobre 2026

## Résumé

La V2 refusée (`publish/episode-02-V2/EPISODE-02-V2.mp4`) n'est pas une version « presque bonne » : elle change la méthode de voix de l'épisode 1, remplace des prestations natives correctes par du TTS, perd le CTA, présente plusieurs ruptures de raccord visibles et sort dans un format audio non conforme. Le pipeline Studio IA ne pouvait pas l'attraper : il approuvait automatiquement les prises dont le QC était absent ou non concluant, puis exportait et marquait la production `done`.

La référence retenue est la V3 publiée de l'épisode 1, choisie par l'utilisateur. L'ancienne comparaison avec une V2 de l'épisode 1 n'est pas utilisée.

Aucune image n'a été générée, aucun GPU n'a été appelé, aucun TTS payant n'a été exécuté et aucune version de remplacement n'a été produite. L'analyse des médias repose sur `ffprobe`/`ffmpeg`, Whisper local (CPU), des mesures F0 indicatives, des extractions d'images et la lecture du code. **Je n'ai pas écouté les films.** Une écoute humaine reste nécessaire pour les voix, le naturel et le lip-sync ; ce rapport ne les déclare pas validés.

## 1. Référence et médias contrôlés

### 1.1 Référence publiée retenue

| Élément | Mesure |
| --- | --- |
| Fichier | `publish/episode-01-FINAL-V3/EPISODE-01-V3-social.mp4` |
| SHA-256 | `7ab17711fc0a4aa49842108c6f2bbfe5aa81b791077085f615371234760efda3` |
| Durée | 43,136 s (vidéo 43,083 s, 1 034 images à 24 fps) |
| Image | 1080 × 1920, H.264, 24 fps |
| Audio | AAC 48 000 Hz, stéréo, 43,136 s |
| Loudness | -14,0 LUFS intégré, crête vraie -4,1 dBFS, LRA 6,1 LU |
| Découpage | 5 plans, 4 coupes ; CTA final « Commente GO… » |
| Statut | Choisie et publiée par l'utilisateur ; copie dans `calliope-backend/data/assets/43/studio/exports/episode-01-v3-external.mp4` |

La V3 sert de référence de voix, de géographie et de continuité, pas de master parfait. Ses propres documents signalent un lip-sync approximatif sur le plan C et une ASR instable sur le mix. L'ASR locale de la V3 n'a d'ailleurs pas transcrit la ligne CTA finale (elle s'arrête à 36,06 s) : la présence du CTA dans la V3 n'est donc pas prouvée par cette ASR, même si le fichier et le manifeste la déclarent.

### 1.2 Film refusé

| Élément | Mesure |
| --- | --- |
| Fichier | `publish/episode-02-V2/EPISODE-02-V2.mp4` |
| SHA-256 | `bac11f5c28a110d080d0dff0bc141876d45d9b42e0790d20b4b148342e76c2d6` |
| Durée | 59,458 s (1 427 images à 24 fps) |
| Image | 1080 × 1920, H.264, 24 fps |
| Audio | AAC **96 000 Hz**, stéréo, **59,100 s** |
| Loudness | -13,9 LUFS intégré, crête vraie **-0,7 dBFS**, LRA 5,3 LU |
| Structure | 6 plans, 5 fondus de 4/24 s ; aucune CTA |
| Sources | `work/publish-ep44-v2/` (scripts, prompts, rendus, TTS, assemblage) |

Les transitions visibles ont été relevées par extraction d'images et détection de scènes à 14,917 s, 19,792 s, 24,667 s et 44,417 s ; la coupe D→E à 38,5 s est visible sur les images mais n'a pas dépassé le seuil automatique. Les fondus de 166 ms sont assez courts pour masquer une rupture d'état plutôt que pour la motiver.

## 2. Défauts observés, preuves et causes

| # | Passage | Observation | Preuve | Cause démontrée ou hypothèse |
| --- | --- | --- | --- | --- |
| D1 | 0,0–59,4 s ; fin à 58,35 s | **Aucune CTA**. Le film s'arrête 1,1 s après la dernière réplique. | `lines.json` ne contient aucune ligne CTA ; ASR du mix s'arrête à 58,08 s ; V3 a une CTA de 39,0 à 42,45 s. | Assemblage manuel hors du garde-fou `enqueue_export` (qui exige une CTA). `editorial.validate_plan` n'était appelé par personne. |
| D2 | Plans A, B, C, F (0–24,7 et 44,4–59,4) | **14 des 22 répliques du script sont jouées en TTS** (toutes celles de A, B, C et F), alors que les prises natives contiennent déjà ces répliques, correctement transcrites. Les 8 répliques de D restent natives ; 8 synthèses d1–d8 n'ont jamais été utilisées. | `assemble_v2.py` marque A/B/C/F en `tts` ; ASR des rendus natifs A, F et D : statut `ok`, texte conforme. V3 n'utilise le TTS que pour 2 lignes sur 12. | `tts.apply_to_shot` remplaçait toutes les répliques ; la V2 a été fabriquée avec la même logique. La prestation native a été jetée. |
| D3 | 1,88–4,28 s ; 4,36 s ; 11,94–14,93 s | **Dérive de lip-sync** : la ligne a2 du TTS dure 2,40 s contre 1,42 s pour la prise native (+0,98 s). a3 démarre 0,34 s après la bouche ; a6 démarre 0,334 s après et finit 0,05 s **après** le début du fondu de coupe. | `work/audit-ep02-2026-10-18/timing-deltas.json`, comparaison des segments ASR natifs et des départs calculés par `assemble_v2.py`. | Le TTS est placé au départ demandé, puis repoussé par `max(start, prev_end + 0.08)` ; aucune vérification de durée dans la fenêtre, aucun recalage sur les mots natifs. |
| D4 | f4 et f6 ; a6 | **Textes divergents entre le prompt vidéo et le TTS** : f4 ajoute « Explique-moi. » ; f6 dit « fais attention à qui tu tapes » au lieu de « regarde bien qui tu tapes » ; a6 dit « Je serai là » au lieu de « J'y serai ». | `renders/f-yard-reveal/prompt.txt` et `renders/a-cantine/prompt.txt` comparés à `lines.json`. | Deux sources de vérité (prompt de rendu et manifeste TTS), sans contrôle croisé. La bouche native ne prononce pas les mêmes mots que la piste TTS. |
| D5 | 38,5–45,0 s ; 23–24 s ; 1–7 s / 40–58 s | **Ruptures d'état visibles** : lunettes de Momo présentes à 43–44 s, absentes à 45 s avec la photo déjà en main ; les deux hommes sont immobiles à 43–44 s puis en garde de combat à 50 s ; chaussures blanches dans E/F contre bottes noires dans A–D ; cellule de Momo décorée d'affiches et d'outils contre cellule nue de Durio. | Images `work/audit-ep02-2026-10-18/frames2/b-0430.jpg`, `b-0440.jpg`, `b-0450.jpg`, `b-0500.jpg` ; `frames/v2-01-001000.jpg`, `v2-17-055000.jpg` ; V3 `frames/v3-01-001000.jpg` (bottes noires). | Aucun contrôle d'état initial/final entre plans ; le QC visuel n'est pas obligatoire en mode automatique ; le fondu court masque le changement. |
| D6 | 41,24–41,60 s | **Cri parasite « NOOOOO ! »** dans le plan E (cour de prison, 38,5–44,4 s), plan déclaré sans dialogue. | ASR du mix final ; ASR du rendu natif E : « DAAAAAUH !!! » (statut `unknown`). | Les plans natifs sans dialogue n'étaient jamais transcrits en production : `assess_take` retournait `None` et la prise était approuvée. |
| D7 | toute la piste | **Audio 96 kHz**, audio plus court que l'image de 0,358 s, crête -0,7 dBFS pour une cible de -1,5. | `ffprobe` + `ebur128` ; V2 59,100 s audio / 59,458 s vidéo. | `assemble_v2.py` applique `loudnorm` sans `aresample=48000` après, puis encode en AAC. Aucun contrôle de livraison dans l'app. |
| D8 | 14,875–15,042 s | **Fin de réplique dans le fondu** : a6 TTS finit à 14,925 s, le crossfade commence à 14,875 s. | `timing-deltas.json`, dernier champ. | `assemble_v2.py` ne réserve pas la marge de transition ; la vérification `prev_end > duration + 0.02` ignore le fondu. |
| D9 | a1 et a3 | **Rupture de canon démontrable** : Durio reprend mot pour mot la question que Momo pose à la fin de la V3 (« Les gardiens t'ont pas expliqué pourquoi j'étais ici ? »), avec les rôles inversés. Le « Hier » de a1 peut aussi contredire la continuité immédiate, mais le délai n'est pas explicité : hypothèse à confirmer par le scénariste. | V3 `qc.json` ligne c3 (33,5–35,9 s) ; `lines.json` a1/a3. | Le scénario V2 n'a pas été contraint par la dernière ligne/état de la V3 ; aucun contrôle de canon ou de répétition inter-épisodes. |
| D10 | D (24,7–38,5 s) | **Deux méthodes de voix dans le même film** : D garde l'audio natif, A/B/C/F utilisent le TTS. Les 8 lignes TTS d1–d8 ont été synthétisées puis **jamais utilisées**. | `assemble_v2.py` (`d-corridor`, `native`) ; fichiers `tts/d1.wav`…`d8.wav`. | Le choix natif/TTS était manuel et non tracé ; aucune politique « natif d'abord, doublage ciblé » dans le pipeline. |


## 3. Voix : ce qui est prouvé, ce qui ne l'est pas

### 3.1 Fait structurel

La V3 fait porter l'essentiel du dialogue par les voix natives de LTX : 10 lignes sur 12 dans les plans A, B et D, plus une partie de C. Le TTS n'y intervient que pour la réplique Momo inversée (ligne 9) et la CTA finale (ligne 12). La V2 fait l'inverse : elle remplace 14 lignes sur 22 par du TTS, y compris toutes les répliques de Durio et Momo dans A, B, C et F ; les 8 répliques de D restent natives, et 8 synthèses d1–d8 n'ont jamais été utilisées.

Ce n'est donc pas une hypothèse d'écoute : la méthode de production a changé entre les deux films. Une voix TTS, même avec une référence correcte, n'est pas la voix native entendue dans la V3. C'est la cause structurelle la plus probable du retour « voix différentes ».

### 3.2 Mesures F0 indicatives

Mesures locales par autocorrélation, sans valeur probante d'identité :

| Source | Médiane F0 observée |
| --- | --- |
| TTS Durio (V2, a1/a3/a5/b1) | 99,4 à 111,5 Hz |
| TTS Momo (V2, a2/a4/a6/c1) | 120,3 à 148,1 Hz |
| TTS Poire (d1–d8) | 210 à 308 Hz |
| TTS Pêche (d2/d4/d6) | 206 à 267 Hz |
| Références `durio` / `momo` | 124,0 / 137,3 Hz |
| Rendu natif A, Durio (ASR) | 155 à 232 Hz |
| Rendu natif A, Momo (ASR) | 238 à 271 Hz |
| V3 native, Durio (SRT) | 176 à 291 Hz |
| V3 native, Momo ligne 11 | 207,8 Hz |

Les valeurs natives sont probablement affectées par des erreurs d'octave (musique, ambiance, réverbération) : elles ne prouvent ni une différence ni une identité. En revanche, la source TTS est propre et reproductible, et la V2 l'utilise pour des personnages dont la V3 a fait entendre la voix native. **Ne pas conclure « voix cohérentes » à partir de ces chiffres.**

### 3.3 Ce que le QC du Studio sait réellement vérifier

`transcription.assess_turns_v2` renvoie toujours :

```
speaker_identity_status = "unverified"
speaker_identity_reason = "Diarisation non installée : le locuteur est recopié de l'attendu."
```

Le manifeste de la V3 affirme pourtant `speaker_identity_status: "vérifié par affectation de voix (TTS) et transcription native (A/C)"`. Ce n'est pas une vérification acoustique : c'est une affirmation humaine fondée sur l'affectation des voix et le texte. Elle ne prouve ni le timbre, ni la prononciation, ni le lip-sync. Le présent audit ne la reprend pas.

Aucun outil du dépôt ne mesure l'alignement bouche/audio. Les contrôles disponibles sont : texte transcrit, timings de mots, niveaux, extraction d'images. Le lip-sync et l'identité vocale restent **non vérifiés** et doivent bloquer une affirmation de conformité.

## 4. Pourquoi la supervision et le QC ont laissé passer la V2

1. **Approbation automatique.** `production.render_shot` appelait `service.approve_shot(..., True)` après le QC. En mode non-Director, `director.guard_approval` ne s'applique pas : une prise terminée était approuvée même avec un QC `unknown`, `missing`, `extra` ou `inaudible`. Les tests encodaient ce comportement (8 cas paramétrés dans `test_studio_production_regressions.py`, 3 autres dans `test_studio_integration.py` et `test_studio_retention.py`) ; ils ont été corrigés, pas contournés.
2. **Export automatique.** `run_production` enchaînait `render_videos` → `enqueue_export` → `status='done'` sans exiger que les plans soient approuvés. Un MP4 assemblé valait « Épisode prêt ».
3. **Plans sans dialogue non contrôlés.** `assess_take` retournait `None` pour un plan sans dialogue et la prise était approuvée. C'est exactement le cas du cri « NOOOOO ! » dans le plan E.
4. **Post-doublage global.** `tts.apply_to_shot` synthétisait toutes les répliques et remplaçait toute la piste native, sans sélection ciblée, sans contrôle de durée dans la fenêtre, sans comparaison à une référence vocale approuvée. Le texte ASR pouvait être juste, donc le QC disait `ok`, alors que la voix et le lip-sync avaient changé.
5. **Linter éditorial mort.** `editorial.validate_plan` existait mais n'était appelé par aucun chemin de production. Il ne pouvait donc pas signaler les coupes, la CTA ou les états incohérents.
6. **Aucun contrôle de livraison.** Rien ne vérifiait le 48 kHz, le stéréo, la durée de l'audio par rapport à l'image ou la crête après `loudnorm`.
7. **Aucun contrôle de canon inter-épisodes.** Le contexte de la V3 s'arrête à la question de Momo ; la V2 réutilise cette question dans la bouche de Durio et invente « hier ». Le planificateur n'a pas de garde-fou sur la dernière réplique/état publié.


## 5. Corrections générales appliquées

### 5.1 `calliope-backend/src/calliope/studio/tts.py`

- **Le jeu natif n'est plus remplacé par défaut.** `apply_to_shot` ne fait rien si aucune réplique n'est explicitement sélectionnée. `post_dub_turns` (index 0-based) cible les répliques à doubler ; `post_dub=True` reste accepté pour compatibilité, mais il est signalé comme remplacement complet.
- **Post-doublage ciblé.** Seules les répliques sélectionnées sont synthétisées. `mux_video` coupe la piste native uniquement dans leurs fenêtres et conserve l'ambiance/les autres voix hors fenêtres.
- **Fenêtre de contrat respectée.** Chaque TTS est placé à `start_sec` et comparé à `end_sec`. Un débordement de plus de 80 ms lève une erreur explicite au lieu de repousser silencieusement la réplique suivante. Les silences de bord sont retirés avant mesure.
- **Références vocales traçables.** `_reference_for` est indexé par série, personnage et empreinte de la description de voix. Un sidecar JSON conserve la description, le SHA-256 du fichier, le modèle et `approved: false`. Un fichier modifié ou une description changée invalide le cache au lieu de réutiliser une ancienne voix.
- **Métadonnées honnêtes.** `tts.voice_identity_status = "unverified"`, `tts.lip_sync_status = "unverified"`, `replaced_turns`, `preserved_turns`, `windows` et `voice_references` sont enregistrés avec la prise.
- **Sortie audio normalisée.** Le mux TTS sort en 48 000 Hz, stéréo, AAC 192 kbit/s.

### 5.2 `calliope-backend/src/calliope/studio/production.py`

- `apply_tts_status` ajoute les statuts non vérifiés au QC des prises doublées.
- **Plans sans dialogue** : une ASR locale est exécutée sur les prises natives. Une parole détectée donne `unknown` (« Parole détectée dans un plan sans dialogue »), jamais une approbation. Sans checkpoint ASR, le comportement historique est conservé pour ne pas bloquer les installations sans ASR.
- **Approbation** : `render_shot` n'approuve automatiquement que si le QC est `ok` (ou absent pour un plan sans dialogue) et si aucune voix n'a été remplacée. Un QC `unknown/missing/extra/inaudible` conserve la prise mais la laisse non approuvée, sans rerender GPU.
- **Post-doublage** : dès qu'une réplique a été remplacée, la production se met en pause et demande une écoute humaine ; elle ne peut plus approuver ni exporter automatiquement.
- **Assemblage** : `run_production` vérifie que tous les plans sont approuvés avant `enqueue_export` ; sinon la production passe en `paused` avec le nombre de plans et leur statut QC. Le statut `done` n'est plus synonyme de « MP4 assemblé ».
- **Linter éditorial** : `editorial_review(project_id)` est appelé avant les rendus (hors mode Director, où le preflight et la revue du storyboard font déjà office de garde). Les erreurs bloquent ; les avertissements sont conservés dans `plan_json.editorial_review`.
- `post_dub_turns` est exposé dans le contexte de plan.

### 5.3 `calliope-backend/src/calliope/studio/editorial.py`

- Validation des `post_dub_turns` : liste d'entiers, index dans le plan, pas de doublon.
- Avertissement si `post_dub` est global sans répliques ciblées.
- Avertissement sur les états (`end_state` du plan N vs `start_state` du plan N+1) non raccordés et non déclarés.
- La limite CTA de 4 s s'applique au plan `outro` (la vraie CTA), plus au midroll publicitaire.
- Les plans courts mais silencieux ne sont plus rejetés ; un plan court avec dialogue produit un avertissement, pas un blocage arbitraire.
- Le nombre minimal de coupes tolérées passe à 2 pour les formats courts ; le seuil « une coupe par 8 s » reste actif sur les films longs.

### 5.4 `calliope-backend/src/calliope/studio/export.py`

- `inspect_audio` et `verify_delivery` vérifient la fréquence (48 kHz), les canaux (stéréo) et l'écart audio/image (≤ 0,25 s). `assemble` appelle `verify_delivery` avant de remplacer la destination.
- Le contrôle vise explicitement le défaut 96 kHz / audio tronqué de la V2.

### 5.5 `service.py`, `routers/studio.py`, tests

- `public_shot` expose `post_dub_turns` ; `ShotUpdate` et `update_shot` l'acceptent et l'écrivent dans le contexte du plan.
- Nouveau fichier `tests/test_studio_episode02_audit.py` (11 tests).
- Plusieurs tests existants ont été corrigés pour la nouvelle politique : un QC non concluant ne vaut plus approbation (production_regressions, integration, retention), et le diagnostic audio calme ne s'auto-approuve plus (pacing).
- `test_studio_tts.py` sélectionne explicitement la réplique à doubler et vérifie les nouveaux statuts.

### 5.6 Ce qui n'est pas un correctif général

Aucun média de la V2 n'a été retouché, aucun plan n'a été réassemblé et aucune V3 n'a été produite. Les scripts `work/publish-ep44-v2/*.py` restent des archives ; ils n'ont pas été exécutés. La V2 reste refusée.


## 6. Tests réels et résultats

| Commande / périmètre | Résultat |
| --- | --- |
| `pytest tests/test_studio_episode02_audit.py` | **11 passed** |
| Lot ciblé TTS, éditorial, export, série, Director, production, pacing | **224 passed** |
| Tous les `tests/test_studio*.py` sauf `test_studio_repair_values.py`, plus `tests/test_export.py` | **770 passed en 407,83 s** |
| `tests/test_studio_repair_values.py` | 46 passed, **1 failed** |
| `npm run check/test/build` | Non lancés : aucun fichier web n'a été modifié. |

Le seul échec restant est **antérieur à cet audit** : `test_unlocated_error_stops_without_arbitrary_whole_episode_repair` attend 2 appels texte et le planificateur en fait 4. Il est déjà documenté comme non résolu dans `docs/studio/VERIFICATION-PUBLICATION-2026-10-18.md`. Il n'a pas été masqué par une modification d'assertion.

Aucun test n'a appelé Modal, VoxCPM, un fournisseur d'images ou un LLM payant. Le plugin de test et la configuration isolée existants ont été conservés. L'ASR utilisée est le checkpoint Whisper local déjà installé (CPU). Les mesures F0/FFmpeg sont locales.


## 7. Limites et points restant à valider

1. **Écoute humaine.** Je n'ai pas écouté la V2 ni la V3. Les timbres, le naturel, les accents, les mots parasites et le lip-sync ne sont pas validés. Une écoute comparée A/B des mêmes répliques (V3 native vs V2 TTS) est indispensable.
2. **Identité vocale.** F0 et MFCC ne remplacent pas une comparaison d'empreinte vocale ni une diarisation. Le QC du Studio déclare lui-même `speaker_identity_status = unverified`. Aucune base de référence vocale approuvée par série n'existe encore ; les sidecars `approved: false` le rendent visible mais ne le corrigent pas.
3. **Lip-sync.** Aucun outil du dépôt ne mesure l'alignement lèvres/audio. La dérive de timings est démontrée, mais l'effet visuel exact (à quelle image la bouche cesse de bouger) reste à vérifier à l'œil.
4. **Continuité visuelle.** Les ruptures de chaussures, lunettes et décor sont observées sur des images extraites. Leur perception exacte au mouvement (et l'effet du fondu) demande un visionnage.
5. **CTA de la V3.** L'ASR locale ne transcrit pas la ligne CTA de la V3. Le fichier et le manifeste la déclarent, mais ce point reste non vérifié par l'outil.
6. **Échec préexistant.** Le test de réparation ciblée reste rouge (2 appels attendus, 4 effectués). Il doit être repris séparément ; il ne relève pas de cet audit.
7. **UI.** `post_dub_turns` est disponible par l'API/contexte, mais aucun contrôle Svelte ne permet encore de sélectionner les répliques à doubler. Aujourd'hui, la V2 ne peut pas être « réparée » par l'interface ; la politique corrigée empêche surtout le remplacement automatique. Une petite UI de sélection reste à ajouter avant de proposer le post-doublage ciblé à l'utilisateur.
8. **Coût réel.** Les tarifs Modal utilisés ci-dessous sont ceux documentés en octobre 2026 pour L40S ; le déploiement courant utilise un RTX PRO 6000 dont le tarif exact n'est pas vérifié dans ce dépôt. Le coût doit être confirmé avant paiement.

## 8. Scène de validation proposée (aucune dépense engagée)

### 8.1 Validation à coût nul

Réutiliser la prise native existante `work/publish-ep44-v2/renders/a-cantine/video.mp4` (15,04 s) dans un projet de contrôle, sans TTS et sans GPU, et l'exporter avec le pipeline corrigé. Cela vérifie :

- conservation de la voix native et de l'ambiance ;
- absence de remplacement TTS ;
- master 48 kHz/stéréo et crête conforme ;
- refus d'un dialogue qui chevauche une coupe.

C'est un test de plomberie, pas une validation de la qualité de la V2.

### 8.2 Scène courte payante, si l'utilisateur l'autorise

**Scène** : un plan unique de 6 s, two-shot cantine Durio/Momo, image de départ existante et déjà validée (aucune génération d'image), audio **natif uniquement**, aucune réplique TTS, coupé et exporté avec les nouveaux garde-fous. Objectif : vérifier que le pipeline conserve la voix native, ne déclenche pas de doublage et bloque l'export tant que l'écoute humaine n'a pas validé.

**Coût estimé** (tarifs L40S documentés : 0,000542 USD/s GPU, 0,0000131 USD/core/s, 0,00000222 USD/GiB/s ; 8 cores, 96 GiB ; 197 s de génération mesurées pour 5,7 s) :

| Poste | Estimation |
| --- | --- |
| Génération d'un plan de 6 s | ≈ 0,17 USD |
| Démarrage + maintien à chaud 300 s | ≈ 0,26 USD |
| Total indicatif premier appel | **≈ 0,40 à 0,45 USD** (≈ 0,37 à 0,42 €) |
| Appel suivant à chaud | ≈ 0,17 à 0,20 USD |

Aucun coût image, LLM ou TTS. Le tarif RTX PRO 6000 n'étant pas vérifié ici, plafonner à **0,60 USD (≈ 0,55 €)** avant accord. Je ne lance rien sans confirmation explicite.


## 9. Fichiers modifiés

| Fichier | Nature |
| --- | --- |
| `calliope-backend/src/calliope/studio/tts.py` | post-doublage ciblé, préservation native, fenêtres, références traçables, métadonnées |
| `calliope-backend/src/calliope/studio/production.py` | statuts TTS, ASR des plans sans dialogue, approbation conditionnelle, garde d'export, linter éditorial |
| `calliope-backend/src/calliope/studio/editorial.py` | cibles de post-doublage, états entre plans, CTA outro, seuils |
| `calliope-backend/src/calliope/studio/export.py` | `inspect_audio`, `verify_delivery`, contrôle avant destination |
| `calliope-backend/src/calliope/studio/service.py` | `post_dub_turns` exposé |
| `calliope-backend/src/calliope/routers/studio.py` | `post_dub_turns` accepté par `ShotUpdate` |
| `calliope-backend/tests/test_studio_episode02_audit.py` | **nouveau**, 11 tests |
| `calliope-backend/tests/test_studio_tts.py` | sélection ciblée, statuts non vérifiés |
| `calliope-backend/tests/test_studio_production_regressions.py` | un QC non concluant n'approuve plus |
| `calliope-backend/tests/test_studio_integration.py` | idem |
| `calliope-backend/tests/test_studio_retention.py` | idem |
| `calliope-backend/tests/test_studio_pacing.py` | l'audio calme ne s'auto-approuve plus |
| `docs/studio/AUDIT-EPISODE-02-2026-10-18.md` | ce rapport |

Les preuves locales (images, JSON, transcriptions, mesures) sont dans `work/audit-ep02-2026-10-18/`, ignoré par Git. Elles ne sont pas publiées avec le dépôt.

## 10. Conclusion

La V2 cumule des causes indépendantes : voix TTS substituées à des prestations natives valides, timing non contraint, textes divergents, raccords visuels non vérifiés, cri parasite non contrôlé, CTA absente, master 96 kHz et canon contredit. Le QC ne pouvait pas les voir parce qu'il ne vérifiait que le texte, approuvait automatiquement le reste et exportait sans revue.

Les correctifs généraux empêchent désormais : le remplacement TTS silencieux, l'approbation d'un QC non concluant, l'export sans validation humaine, l'oubli du linter éditorial et la sortie d'un master audio non conforme. Ils ne prouvent pas que la prochaine prise sera bonne : la voix, le lip-sync et les raccords restent à écouter et à regarder. C'est précisément la limite que ce rapport laisse explicite.

