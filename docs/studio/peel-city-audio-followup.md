# PEEL CITY : son du plan 1.2 et composition du plan 1.3

Observations du 10 octobre 2026 sur le projet 16, épisode déjà préparé avant
le correctif de découpage `318d201`.

## Plan 1.2, interne 124, prise 242

Dialogue attendu : "Homie, c'est des chips à la truffe. Respecte mon business, cuh."
Le prompt compilé contient une seule balise de parole, pour Kiki (S2), en français.
Aucune voix off introductive n'est demandée. Les seuls sons de lieu déclarés
sont les chips, le vent urbain faible et des sirènes lointaines. La musique
native est désactivée.

La vidéo native dure 5,166667 s pour une durée utile demandée de 3,5 s.
CallID : `fc-01M4JHSMEEGMZK67GKWD3DEFG2`.
Le défaut sonore signalé par l'utilisateur est dans la vidéo brute ; il ne
provient pas du mixage de l'export final.

La reconnaissance conservée dans le cache est `unknown`. Elle détecte un
premier segment de 0 à 1,62 s, puis reconnaît une partie de la réplique attendue.
Son score global `avg_logprob` est -0,723469, sous le seuil -0,7 du transcripteur.
Les mots du premier segment sont très incertains. Deux essais CPU sur cette
introduction, en français et en anglais, donnent des textes différents avec des
scores encore plus faibles (-2,4776 et -2,3628). Ces textes ne constituent pas
une transcription fiable de ce qui a été prononcé.

Ces données ne prouvent pas une récitation du prompt. Elles ne permettent pas
non plus de garantir une coupe automatique qui préserve toute la réplique.
Le signalement audible de l'utilisateur et le résultat incertain doivent rester
distincts d'un diagnostic lexical prouvé.

## Défaut logiciel confirmé et correction

Le QC incertain était conservé, mais `render_shot` pouvait ensuite approuver la
prise automatiquement. Il n'y avait aucune relance car seuls `missing`, `extra`
et `inaudible` entraînaient un rejet. Avec `voice=None`, aucun trim de parole
n'était enregistré et toute l'introduction restait disponible pour le montage.

Une prise non approuvée dont le QC reste `unknown` demande maintenant une écoute
explicite et met la production en pause avec un message clair. Elle ne déclenche
aucun rendu automatique. Une approbation manuelle permet de la conserver ; une
prise au QC fiable poursuit la production automatiquement. Les rejets déjà
confirmés et leur plafond de relance restent inchangés.

Cette correction empêche une acceptation automatique sans preuve. Elle ne
répare pas la prise 242 et ne garantit pas qu'H3 ne produira plus de sons indésirables.

## Plan 1.3, interne 125

Le manifest enregistré ne contient que `character_keys=["benny"]` et un tour
de parole de Benny. L'image choisie est la prise 234. Le prompt utilise le terme
"Two-shot", mais décrit Benny et un bus de passagers ; Kiki n'est pas déclaré
dans la composition. Au moment de l'observation, aucune prise vidéo n'était
encore choisie pour ce plan.

L'inspection des images confirme cette séparation : la prise 233 montre Kiki
seul sur le canapé ; la prise 234 montre Benny près du lampadaire et le canapé
vide. L'absence de l'interlocuteur précède donc le rendu vidéo.

La reprise conserve ce manifest et cette référence. Les nouvelles consignes du
planner ne réécrivent pas les images ou les plans d'un épisode existant. Une
conversation commune demande un nouveau découpage et une image réunissant les
interlocuteurs avant le rendu. Aucun de ces médias n'a été régénéré ici.

## Preuves conservées

Le dossier de travail de la mission contient `outputs/peel-city-16-audio-12` :
snapshot du board, audio diagnostique extrait, introduction isolée et sorties
des deux reconnaissances CPU. La vidéo originale n'a pas été modifiée ; son
SHA-256 reste `4a3c5d99774189c5a2ff1a54d074a5178fe2baf66a6d7448f270c2101bf0e02b`.

Le fichier `audio-qc-replay.json` contient le replay du contrôle et de
`render_shot` sur le cache réel de la prise 242. Seuls les accès en écriture
ont été redirigés vers une base de diagnostic. Le résultat est une pause
d'écoute, zéro approbation automatique, zéro appel GPU et une vidéo inchangée.

Les nouveaux tests couvrent l'audio incertain sans approbation, l'acceptation
manuelle et la poursuite automatique après QC fiable. Les tests de montage,
reprise et historique simulent désormais explicitement leur parole valide,
au lieu de dépendre de l'acceptation implicite d'un son artificiel non transcrit.

Résultats : 122 tests ciblés audio/intégration/pacing, puis 33 tests de
reprise/historique/régression réussis. Suite complète finale avec
`python -m pytest --asyncio-mode=auto -q` : 1129 réussis, 2 ignorés,
2 warnings Windows asyncio connus, aucune erreur (455,45 s).

Le serveur déjà ouvert doit être redémarré pour charger ce contrôle. Aucun clic
de reprise, approbation réelle ou rendu supplémentaire n'a été effectué.
