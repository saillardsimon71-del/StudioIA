# Produire une série dans Studio IA

Ouvrir http://127.0.0.1:8247/studio. Si le studio est fermé, double-cliquer sur launch.cmd.

1. Cliquer sur "Créer une nouvelle série", saisir son titre et son concept. Le format vertical, l'anglais et environ 65 secondes sont proposés par défaut.
2. Dans la série, cliquer sur "Créer un nouvel épisode". Donner une idée directrice. "Préparer et relire le scénario avant les rendus" est coché par défaut : cliquer sur "Préparer le scénario", relire l'accroche, l'enjeu, la récompense et la suite, puis corriger les plans si nécessaire. La préparation utilise seulement le service texte. La revue est une proposition de l'IA, pas une prévision de succès ; elle signale quand le scénario a changé.
3. Cliquer sur "Lancer les rendus", puis attendre "Épisode prêt" et lire ou télécharger le MP4. Le studio prépare les références et les images, génère les clips FastH3 sur Modal et assemble le son et la vidéo. Décocher la préparation permet de produire directement jusqu'au MP4.
4. Créer l'épisode suivant dans la même série. Son scénario utilise les épisodes terminés et les références de cette série.

Le rédacteur invente la situation et les échanges à partir de l'intention de
l'utilisateur, du genre et des personnages. Les exemples illustratifs ne sont
pas des intrigues imposées. Seuls les événements et les mots explicitement
exigés restent obligatoires ; aucun gag ni modèle d'ouverture n'est ajouté.
La demande de l'épisode courant prime sur les pistes d'intrigue du concept.
Ces pistes restent distinctes des événements antérieurs et des identités du canon.

La préparation sépare l'histoire, le découpage et les raccords. Les répliques,
leurs locuteurs et leur ordre sont contrôlés. Pour chaque nouveau scénario natif,
les états des objets sont préparés plan par plan : le logiciel transmet l'état
validé au suivant et calcule les états avant/après chaque changement. Un objet
important oublié peut être déclaré sans modifier les identités existantes.
Pour une prise indépendante, l'image initiale est aussi fournie au calcul des
états. Une prise continue conserve les états transmis depuis la précédente.
Une réparation ne réécrit pas librement les autres plans. Une réponse incorrecte
est conservée dans les diagnostics ; la reprise reste en préparation si les
rendus n'ont pas encore été autorisés. La relecture permet de juger la clarté,
le rythme et l'intérêt de l'histoire avant la génération des vidéos.

La durée cible guide l'écriture. Un nouveau scénario natif plus court reste
relisible : l'écart est signalé dans les points à corriger, sans imposer de
remplissage. Les répliques dont la durée estimée dépasse déjà le temps disponible
sont raccourcies dès l'écriture, avant le découpage technique. Cette estimation
ne garantit pas le débit obtenu dans la vidéo. Les plans ont des durées libres
(une réaction d'une seconde et demie, une réplique de trois secondes, un
mouvement de cinq secondes), puis H3 rend des clips plus longs (~5,17 s minimum)
et le montage ne conserve que la partie utile de chaque prise, sur la grille
24 fps. Un épisode de 65 secondes vient de la quantité de
contenu narratif, pas de plans étirés. La coupe retenue est visible dans la
fiche de chaque plan. Les diagnostics de parole sont des indications pour la
relecture : ils ne bloquent ni la production ni l'export et ne déclenchent pas
de nouvelle prise. Une transcription non validée ne sert pas à couper la vidéo.
Un fichier manquant ou une piste sonore native absente restent des erreurs techniques.

Le studio ne compose plus de musique. Dans "Musique et bruitages", importer
un fichier puis le sélectionner pour le prochain export. Sans fichier sélectionné,
aucune musique n'est ajoutée. Le volume, la baisse sous les voix, les ambiances
et les bruitages restent réglables sans régénérer les clips.

L'écriture cherche des accusations précises, des esquives et des détails vécus,
avec une façon de parler propre à chaque personnage. La réalisation distingue
le timbre permanent de la voix et le jeu de chaque réplique : assurance, bluff,
panique ou supplication dépendent de la scène. Pour les échanges vifs, les gestes
accompagnent les mots et la dernière réaction prépare la prise suivante.
Ces indications concernent les nouvelles préparations et prises ; elles ne
modifient pas le jeu des clips déjà rendus et ne garantissent pas leur qualité.

Chaque nouvel épisode natif contient une seule incitation, à la coupe la plus
proche du milieu du temps narratif. Un personnage de la série reste dans le lieu
actuel, pointe vers le spectateur et dit, en français :
"Abonne-toi, copie le lien et commente GO si tu veux recevoir un guide pour faire des vidéos gratuitement."
La parole démarre immédiatement, avec un débit vif mais compréhensible ; le
geste et le zoom l'accompagnent. La durée française demandée est estimée autour de
4,6 secondes pour cette phrase complète ; la prise native FastH3 peut être plus longue.
Le montage retire l'attente autour des mots
lorsqu'une transcription fiable est disponible, et utilise des coupes directes
à l'entrée et à la sortie de l'incitation. Aucun téléphone n'est ajouté au montage. Les autres langues
utilisent une traduction. Le plan demande un zoom rapide, sans musique ni
ambiance native ; le mix coupe les fonds et les
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
