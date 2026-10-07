# Validation du 7 octobre 2026

Parcours réel effectué dans l'interface du studio, sur le compte Modal existant.
Trois rendus GPU seulement: deux plans depuis du texte, puis une nouvelle prise
du second plan depuis une image importée. Aucun nouveau rendu après la reprise
de la session interrompue.

| Prise | Entrée | Résultat mesuré | Temps du worker GPU |
| --- | --- | --- | --- |
| 1, lac | Texte, graine 324711 | 124 frames, 832x480, 24 fps, audio natif | 105,950 s |
| 2, cascade | Texte, graine 324712 | 124 frames, 832x480, 24 fps, audio natif | 90,068 s |
| 4, cascade | Image importée 3, graine 324713 | 124 frames, 832x480, 24 fps, audio natif | 106,411 s |

Chaque vidéo dure réellement 5,1667 s. La piste audio de la première prise a
été mesurée: volume moyen -25,8 dB, maximum -10,8 dB; ce n'est pas une piste vide.

Le studio a été arrêté pendant le second rendu puis redémarré. Il a récupéré
le même appel Modal, sans nouvelle soumission. Le choix et l'approbation du
premier plan sont restés enregistrés. L'image importée a été choisie dans
l'interface. La comparaison a affiché les deux vidéos du second plan. La nouvelle
prise n'a remplacé aucune sélection automatiquement.

Les prises 1 et 4 ont été choisies et approuvées dans l'interface. Le bouton
Exporter le film MP4 a produit un fichier final réellement assemblé:

- H.264, 1280x720, 24 fps, 248 frames;
- AAC, 48000 Hz, stéréo;
- 10,333333 secondes, 6205718 octets;
- téléchargement depuis le studio identique au fichier source par SHA-256;
- lecture complète dans le navigateur jusqu'à ended=true, aucune erreur;
- décodage intégral FFmpeg sans erreur.

Le projet local "La lumière du matin" reste prêt à ouvrir et à réexporter. Le
film téléchargé est livré séparément dans outputs/film-demo.mp4. Le dépôt Git
contient les sources, sans clés, base locale, poids ni médias générés.

SHA-256 du film: 19e0a82127bbd0095cb0ab857e840eb906ddedf518c0adf0065a431766a6fc68.

## Tests finaux

- Suite backend complète: 786 réussis, 2 ignorés, 2 avertissements, 321,99 s.
- npm run check: 0 erreur, 11 avertissements dans les écrans hérités.
- npm test et npm run build: réussis.
- Ruff sur tous les nouveaux modules Python et leurs tests: aucune erreur.
- git diff --check: aucune erreur.

Commandes reproductibles à la racine du dépôt:

```powershell
.venv\Scripts\python.exe -m pytest calliope-backend/tests -q
cd calliope-web
npm run check
npm test
npm run build
```

## Vérifications de robustesse

Les tests ciblés ont vérifié les prises conservées, les demandes dédupliquées,
la référence qui invalide l'approbation, l'export complet et son snapshot figé,
la reprise qui ne soumet pas une seconde tâche GPU, la soumission incertaine qui
ne dépense pas de nouveau, l'annulation pendant la préparation et après reprise,
l'arrêt réel de FFmpeg lors d'une annulation et le respect de la révision du
projet à la fin d'un export. Un changement de service images bloque les anciens
jobs avant tout envoi de la nouvelle clé.

L'import du SDK Modal préserve la politique asyncio de Windows. Le test démarre
un vrai sous-processus après cet import, afin de conserver l'assemblage FFmpeg.
Les formats paysage/portrait/carré et les vidéos importées avec/sans audio sont
vérifiés par des fichiers FFmpeg réels. La continuité par dernière image est
testée sur un clip rouge puis bleu: la référence extraite est bien la fin bleue.

La commande launch.cmd a installé les dépendances figées, reconnu un studio
déjà ouvert, puis démarré le studio après arrêt. Aucun service ComfyUI local
n'est requis; le worker le démarre sur Modal.

## Limites constatées

Les services d'écriture et d'images distants n'étaient pas configurés, donc aucun
rendu distant de leur part n'est revendiqué. Le découpage local, les imports et
les vidéos H3 fonctionnent sans eux. Les scènes avec parole/personnage n'ont pas
été validées par un rendu de cette livraison. Ref2VA, identité stable, clonage
vocal et continuité des latents audio/mouvement restent hors de cette V1.
La référence image avec FastH3 est expérimentale. La licence séparée des poids
et ses restrictions sont rappelées dans README et THIRD_PARTY_NOTICES.
