# Qualification sonore PEEL CITY, 10 octobre 2026

Export observé : projet 17, épisode 2, export 20 du 10/10/2026 12:29:04.
Le MP4 original dure 44,166667 s. Il n'a pas été modifié ou réexporté.

## Faits retrouvés

Le mix est activé : musique `tension`, tempo 95, gain musical -18 dB,
voix 0 dB, ducking actif, ambiance `city` à -34 dB. Aucun fichier musical
ou bruitage externe n'est importé. Quatre accents sont enregistrés :
impact et clic au plan 141, whoosh au plan 145, beep au plan 139.
Une retenue sonore de 0,4 s est prévue dans le plan 145.

La reconstitution CPU du stem musical avec les réglages figés mesure
-14 dBFS RMS avant gain, donc -32 dBFS avant ducking. Le synthétiseur
précédent produisait principalement une nappe et une pulsation lente.
Le compresseur pouvait ensuite fortement réduire cette musique sous les
voix : le cas de régression à voix forte mesure -23,5 dB supplémentaires.
Ce dernier chiffre décrit le test contrôlé, pas une mesure du run réel.
Le MP4 original complet mesure -17 dB de volume moyen et -0,5 dB de crête
avec FFmpeg `volumedetect` ; ce sont les niveaux du mix complet, pas de
la voix isolée.

Le profil musical de série était également ignoré lorsque le planner
choisissait une autre musique que `none`. Choisir une identité ludique
ne garantissait donc pas son utilisation dans le nouvel épisode.

## Correction du studio

- Ducking parallèle : 35 % du lit reste présent, soit une atténuation
  supplémentaire limitée à environ 9,1 dB. Les retenues narratives peuvent
  toujours volontairement couper le fond ; la voix n'est pas coupée.
- Niveau musical initial -12 dB pour les nouveaux réglages. Les niveaux
  explicitement enregistrés restent respectés.
- Styles ludique, tension et énergique avec arpège, basse et percussion.
  Crêtes musicales adoucies pour conserver un niveau de référence audible.
- Sons locaux `pop`, `boing`, `scratch`. Sources SFX à crête commune
  -1 dBFS avant le gain du cue, sans normaliser l'ambiance avec les effets.
- Consignes de placement sur les gags, apparitions, notifications,
  retournements et révélations. Aucun ajout automatique à intervalle fixe.
- Le style musical du profil est appliqué aux nouveaux plans ; une musique
  importée conserve sa sélection. Le volume du profil devient éditable.

PEEL CITY est réglée sur une identité `playful` à -12 dB pour les futurs
épisodes. Le profil figé du projet 17 et son export 20 restent inchangés.
Un redémarrage du backend est nécessaire pour charger le nouveau moteur.

## Preuves et limites

`work/sound-design-qualification` conserve les métadonnées d'export avant
correction, le hash du MP4, les stems précédents, le profil de série précédent,
les journaux de tests et un exemple instrumental de dix secondes avec trois
accents comiques. Cet exemple ne reprend aucune vidéo ou voix de l'épisode.

Les tests contrôlent le niveau et la récupération du fond, la conservation
de la voix, les onsets, les crêtes SFX, la présence de percussion et la
propagation de l'identité musicale. Ils ne prouvent pas la qualité artistique
à l'écoute ni une hausse de rétention. Le choix précis des événements reste
une décision du planner. Les sons locaux sont synthétiques ; une bibliothèque
enregistrée peut toujours être importée depuis "Musique et bruitages".

Aucun GPU ni épisode supplémentaire n'est lancé pour cette qualification.

## Validation finale

- Neuf tests ciblés réussis après réglage des niveaux et des tolérances d'onset.
- Suite complète : 1164 passed, 2 skipped, 2 warnings Windows asyncio connus,
  0 failed, 561,03 s, avec `python -m pytest --asyncio-mode=auto -q`.
- Interface : tests réussis, contrôle Svelte sans erreur (11 warnings existants),
  build statique réussi. Ruff et contrôle du diff réussis.
- Hash SHA-256 du MP4 original inchangé :
  `c0c6c4bb2b2cb79cb89f088d521a22a50e93aeee8745b46ef2dd29e95b6493cb`.
