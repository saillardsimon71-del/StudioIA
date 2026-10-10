# Produire une série dans Studio IA

Ouvrir http://127.0.0.1:8247/studio. Si le studio est fermé, double-cliquer sur launch.cmd.

1. Cliquer sur "Créer une nouvelle série", saisir son titre et son concept. Le format vertical, l'anglais et environ 65 secondes sont proposés par défaut.
2. Dans la série, cliquer sur "Créer un nouvel épisode". Donner une idée directrice. "Préparer et relire le scénario avant les rendus" est coché par défaut : cliquer sur "Préparer le scénario", relire l'accroche, l'enjeu, la récompense et la suite, puis corriger les plans si nécessaire. La préparation utilise seulement le service texte. La revue est une proposition de l'IA, pas une prévision de succès ; elle signale quand le scénario a changé.
3. Cliquer sur "Lancer les rendus", puis attendre "Épisode prêt" et lire ou télécharger le MP4. Le studio prépare les références et les images, génère les clips FastH3 sur Modal et assemble le son et la vidéo. Décocher la préparation permet de produire directement jusqu'au MP4.
4. Créer l'épisode suivant dans la même série. Son scénario utilise les épisodes terminés et les références de cette série.

Le scénario remplit réellement la durée cible : les plans ont des durées libres
(une réaction d'une seconde et demie, une réplique de trois secondes, un
mouvement de cinq secondes), puis H3 rend des clips plus longs (~5,17 s minimum)
et le montage ne conserve que la partie utile de chaque prise, sur la grille
24 fps — jamais l'inverse. Un épisode de 65 secondes vient de la quantité de
contenu narratif, pas de plans étirés. La coupe retenue est visible dans la
fiche de chaque plan ; une réplique qui manquerait manifestement ne déclenche
qu'une seule nouvelle prise, jamais de boucle de rendu.

Le mix ajoute musique très légère, ambiance de lieu, quelques effets, une
montée de tension avant la révélation et, si le scénario le demande, un court
silence volontaire ; la musique baisse automatiquement sous les voix.

Chaque épisode contient une seule incitation d'environ 4 secondes, à la coupe
la plus proche du milieu du temps narratif. Un personnage de la série, dans le
lieu actuel, pointe vers un téléphone ajouté au montage et dit, en français :
"Abonne-toi, copie le lien et commente GO !". Le texte affiché reprend ces trois
actions. Les autres langues utilisent une traduction. Le plan demande un zoom
rapide, sans musique ni ambiance native ; le mix coupe les fonds et les
bruitages ajoutés sur toute la durée réellement conservée, en gardant la voix.
Le récit reprend immédiatement. Sa fin apporte une récompense, puis une
conséquence ou une révélation donnant envie de voir l'épisode suivant.

Une reprise remplace l'ancienne outro promotionnelle par cette incitation,
conserve ses anciennes prises et demande une nouvelle prise pour ce plan.
Les vidéos déjà exportées restent disponibles.
Un épisode terminé avec une ancienne outro propose "Intégrer l'incitation au
milieu" : ce bouton prépare le plan sans lancer de rendu. Générer et valider
sa nouvelle prise depuis l'éditeur du plan, puis réexporter.
L'incitation ne fait pas partie de l'historique narratif et ne sert pas de référence de continuation aux plans
suivants. Le scénario doit avoir au moins deux plans narratifs utiles.

Pour intervenir, mettre la production en pause. Modifier le scénario ou ouvrir un plan pour changer son image, son dialogue ou ses prises. Choisir et approuver la prise souhaitée. "Musique et bruitages" permet d'ajuster les volumes, les ambiances, les effets ou d'importer ses propres pistes. Réexporter le MP4 après les changements.

Une reprise conserve les prises terminées et reprend les appels Modal déjà enregistrés. Une série garde sa propre histoire, ses personnages et ses lieux. Le service texte est Agnes 2.5 Flash et les images utilisent Agnes Image 2.5 Flash dans la configuration validée.
