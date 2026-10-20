# Pilote « COMPTEUR — Le compteur ne ment jamais » (15 s, vertical 9:16)

> **Statut : proposition éditoriale + contrats de référence prêts pour le moteur.**
> Aucun calcul GPU, aucune dépense Modal/DeepSeek n'est déclenchée par ce dossier.
> Source créative : `docs/studio/ARENA-REPRISE-BODYCOUNT-2026-10-20.md` §4.
> Série **nouvelle**, strictement séparée de « En prison » (aucun fruit, aucune prison,
> aucune référence voix canonique de cette série).

## 1. Intentions

- **Série** : COMPTEUR. Genre : thriller comique absurde. Personnages **adultes**, univers contemporain.
- **Format** : vertical 9:16, épisode pilote de **15 secondes** en trois prises de 5 s.
- **Dispositif** : un petit compteur physique autonome, posé sur une table, prétend afficher
  en temps réel le « bodycount » des personnages, puis révèle qu'il ne compte pas ce qu'ils croient.
- **Lieu unique** : petit studio d'appartement, le soir. Deux personnages : **Léa** et **Max**.
- Un épisode = un mystère vérifiable, un indice visuel (le compteur) et une fin suspendue.

## 2. Découpage des trois prises

| Prise | Fenêtre | Accroche | Compteur (début → fin) | Paroles |
|-------|---------|----------|------------------------|---------|
| **A** | 0–5 s | Léa découvre le compteur à zéro | `000 → 001` (une seule impulsion) | Léa « Attends… ton bodycount, il est à zéro ? » (0,5–2,8 s) ; Max hors champ « Je te jure. » (3,0–3,8 s) |
| **B** | 5–10 s | Révélation impossible | `001 → 001` (aucun changement) | Max « Mais… j'ai rien fait ! » (0,8–2,3 s) ; Léa « Alors pourquoi ça vient de monter ? » (2,6–4,5 s) |
| **C** | 10–15 s | Suspense | `001 → 002` (une seule fois, après trois coups) | Léa murmure « Max… on est combien dans l'appart ? » (2,8–4,6 s) ; coupe noire avant la réponse |

**Continuité contractuelle** : `000 → 001 → 001 → 002`. Chaque prise commence à la valeur où la
précédente finit ; mêmes costumes, même décor, même accessoire et même position du compteur sur la
table ; une seule montée par événement ; pas de geste répétitif ; **l'image de départ de chaque prise
montre son état initial, jamais sa fin** (A montre `000`, B montre `001`, C montre `001`).

## 3. Comment le pilote est représenté dans le moteur

- Le compteur est un **accessoire physique diégétique** (entité `compteur`, `kind: prop`), rendu dans
  l'image par le modèle vidéo — pas une surimpression logicielle. Ses chiffres vivent donc dans les
  `initial_state` / `final_state` de chaque contrat Director (`« … écran affichant 000 »`, etc.).
- Ces états alimentent **deux garde-fous** automatiques :
  - la **liste blanche OCR** (`generated_text.allowed_display_tokens`) : les chiffres déclarés `000`,
    `001`, `002` ne sont jamais pris pour un sous-titre parasite ;
  - la **clause de texte du prompt LTX** : « the only visible characters are the numerals 000, 001 … »,
    qui interdit à l'image tout autre texte (sous-titres, bulles, logos, mots inventés).
- Le **début des paroles** est calé sur le contrat : prise A/B (onset ≤ 1,0 s) → consigne « démarre
  franchement » ; prise C (onset 2,8 s, après les trois coups) → consigne « bref silence d'amorce ».
  Aucun timing absolu n'est envoyé au modèle (interdit par le linter LTX).

Les trois contrats sont dans [`reference-contracts.json`](./reference-contracts.json). Ils sont
validés **hors ligne** contre le moteur réel par
`calliope-backend/tests/test_studio_compteur_pilote.py` (schéma, débit de parole, cohérence
état/image, continuité du compteur, liste blanche OCR, clause de texte et calage des paroles du
prompt LTX) — sans GPU ni dépense.

## 4. Correctifs du moteur dont dépend ce pilote

Ce pilote est le banc d'épreuve des cinq défauts documentés dans le dossier de reprise :

1. **Sous-titres parasites** — QC OCR (`generated_text`) avec liste blanche des chiffres diégétiques ;
   un texte nouveau vu dans ≥ 2 images est refusé, vu dans 1 image est « à revoir » ; OCR indisponible
   = « à revoir », jamais un faux succès. Un faux positif OCR peut être écarté par une **revue
   supervisée motivée** (`text_override_reason`, liée au hash du fichier revu).
2. **Transcription Whisper** — le cache distingue un échec d'environnement (`environment_failure`,
   toujours retenté) d'un résultat honnête (`ok`/`unknown` d'analyse, mis en cache) : on ne repaie pas
   Whisper à chaque contrôle, et on ne garde pas en cache un lanceur cassé.
3. **Cohérence visuelle** — `consistency_notes` détecte une composition t=0 qui décrirait l'état
   **final** d'un objet (la bouilloire « allumée » alors que l'état initial est « éteinte »), en
   préparation (garde réparable) et en avertissement de preflight (revue humaine).
4. **Gestes répétés** — `find_repeated_events` / `_Seen` écartent un même événement décrit deux fois
   (la découverte du compteur avant puis après la réplique) ; détection **avant** GPU dans le planner
   (réparation ciblée, paroles verrouillées) et déduplication quasi-sémantique au rendu.
5. **Synchronisation des dialogues** — contrôle de la dérive du début de parole
   (`production.apply_speech_onset_check`) : une dérive > 1,0 s entre parole mesurée et contrat rend la
   prise « à recaler » (`timing_mismatch`).

## 5. Protocole de production (sans gaspiller de GPU)

1. **Ne produire QUE la prise A (5 s)** — `EpisodeCreate` : `target_seconds=15`,
   `studio_director=true`, `request_id` alphanumérique ; budget ≤ 4 € (estimation ~3,1 € pour ~15,5 s).
2. Lire le MP4 de A avec l'outil de revue humaine :
   ```bash
   cd calliope-backend
   python scripts/qc_take.py <prise_A.mp4> --allowed 000 --allowed 001 \
       --contract-onset 0.5 --language fr
   ```
   Le script croise OCR (sous-titres vs chiffres du compteur) et Whisper (dérive du début de parole)
   et rend un verdict qui **privilégie « à revoir »** à un faux succès ou un faux rejet. La décision
   finale (identité, raccord, lip-sync, texte dès la première image) reste humaine.
3. **Revue humaine** de A : si refusée, réparer et relancer **une seule** prise ciblée avec plafond de
   coût explicite. **Ne pas mettre B et C en file par défaut.**
4. Seulement après validation de A : produire B puis C, assembler les ~15 s, inspecter
   transitions / voix / compteur / sous-titres, préparer l'export 9:16.
5. Ne jamais toucher à « En prison », à ses médias ni à ses références voix canoniques.

## 6. Limites assumées

- L'OCR ne vérifie ni l'identité, ni le lip-sync, ni un sous-titre incrusté **dès la première image**
  (la ligne de base du scan est la première image) : ce cas exige une revue visuelle humaine.
- Ce dossier est une proposition éditoriale et un banc d'épreuve technique, **pas** un storyboard
  validé ni une vidéo produite. La possibilité de modifier l'idée après la preuve technique est conservée.
