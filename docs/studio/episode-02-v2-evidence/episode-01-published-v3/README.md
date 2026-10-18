# T'as touché au mauvais plateau — Épisode 1 V3

**Version corrigée : 5 plans, 4 coupes, 42,2 s.** Le CTA minimal est conservé.

## Fichiers

- `EPISODE-01-V3-social.mp4` — 1080×1920, 43,1 s, -14,0 LUFS, TP -4,1 dB.
- `EPISODE-01-V3-teaser.mp4` — 1080×1920, 10,7 s (hook + Momo + CTA), -14,1 LUFS.
- `EPISODE-01-V3-native.mp4` — 768×1344, 43,1 s.
- `EPISODE-01-V3.srt` — 12 cues exacts.
- `EPISODE-01-V3-poster.jpg` — vignette.
- `manifest.json` — stratégie audio, coupes, risques.
- `qc.json` — QC réplique par réplique.

## Ce qui a changé

- **Aucune coupe par réplique** : A = 14 s, B = 11 s, C = 11 s, D = 2,7 s, E = 3,4 s.
- **Bloc B corrigé** : Durio s’adresse à la salle, puis rejoint Momo **dans le même plan**, sans coupe.
- **Faux raccord B→C supprimé** : B est large, C est un medium plus serré ; la coupe est motivée par le retournement.
- **Inversion Momo corrigée** : la réplique « À moi… » est en voix Momo TTS ; Durio et Momo natifs restent sur les autres répliques.
- **B** est entièrement en voix native continue : « Hé, le bloc B ! » et « Toi, le binoclard… » ne sont plus séparés par un TTS.
- **CTA minimal allongé** : « Commente GO pour recevoir le guide et abonne-toi pour la partie deux. » — 3,4 s.
- **Ambiance continue** et fondus audio courts aux coupes.
- **Audio réassemblé depuis les WAV de scène** : plus de trim/concat AAC, qui figeait un vowel au point de coupe. Sortie 48 kHz stéréo.

## À écouter avant publication

1. **B** : la prise native continue porte « …il est à qui ton plateau… ». Plus de changement de voix ni de phrase coupée.
2. **C** : la vidéo medium a été refaite, l’audio Durio/Momo vient de la prise précédente ; le lip-sync est approximatif.
3. **E** : la source TTS est exacte, mais l’ASR du mix est instable.
4. Le timbre TTS Momo de la réplique 9 peut différer légèrement du Momo natif.

## Pipeline

- `editorial.validate_plan` : coupes max, durée mini, CTA ≤ 4 s.
- `post_dub=True` : le plan long n’est plus découpé par locuteur.
- `work/audit/tools/render_ep02.py` : 5 prises longues.
- `work/audit/tools/post_ep02.py` : post-doublage ciblé et assemblage.
- `work/audit/tools/tts_ep02.py` : références et synthèse.

## Statut

**Prêt à publier après écoute humaine**, en particulier le point B ci-dessus. La v1 est marquée rejetée.
